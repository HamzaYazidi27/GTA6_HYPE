"""AI reading of the radar's discoveries: a cheap first pass, then a full analysis with video ideas.

Facts are guarded by code, not only by the prompt:

* the verification level can never be higher than the sources allow: *official* needs a
  Rockstar / Take-Two source (or Rockstar's own channel), *credible* needs a news outlet,
  anything else is at most a *community* discovery; the model may only lower it;
* the sources of an idea are the radar's own entries (real URLs from the feeds), referred to
  by id (S1, S2 ...): the model never writes a URL, and any URL it writes is removed;
* a Shorts script about a community find or a rumour must say so (reportedly, players spotted,
  unconfirmed ...), otherwise the answer is sent back for correction;
* the opportunity score is computed here from the model's sub-scores and from facts the radar
  measured itself (how solid, how many outlets, how old).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

from ...app.core.errors import FactoryError
from ...providers.llm import LLMProvider
from .store import CATEGORIES, parse_iso, utcnow

LEVELS = ("rumor", "community", "credible", "official")  # weakest -> strongest
CATEGORY_LABEL = {
    "secrets": "GTA VI Secrets & Easter Eggs",
    "map": "GTA VI Map & Locations",
    "missions": "GTA VI Missions & Gameplay",
    "heists": "GTA VI Robberies & Heists",
}
HEDGE = re.compile(
    r"(?i)\b(rumou?rs?|reportedly|reports?|reported|unconfirmed|not (?:yet )?confirmed|alleged(?:ly)?|leak(?:s|ed)?|claims?|claimed|"
    r"theor(?:y|ies)|might|may|could|apparently|seems?|appears?|spotted|noticed|according to|community|fans?|players? (?:think|believe|say|found|noticed|spotted)|"
    r"if true|speculat\w*|possibly|probably|looks like)\b"
)
URL_RE = re.compile(r"https?://\S+")

SYSTEM = """You are the research editor of "Ghost Plates", a YouTube channel about Grand Theft Auto VI for a US gaming audience.
You decide which GTA VI discoveries deserve a video and you pitch them. You are sceptical and precise:
you only use what the provided sources say, you never add facts from memory, you never invent a source,
and you never present a rumour, a theory or a community find as an official fact.
Write in natural, conversational American English."""

TRIAGE_PROMPT = """RADAR TRIAGE - first pass on new GTA VI headlines. Today: {today}

Content categories of the channel:
- secrets: hidden details, easter eggs, references to older GTA games, unnoticed details, community discoveries
- map: map discoveries, hidden locations, Vice City and Leonida regions, comparisons with real Florida, interiors
- missions: missions, gameplay mechanics, character abilities, activities and side missions
- heists: robberies, bank and store heists, vehicle theft, police response and wanted system, weapons, escapes

HEADLINES (id | trust of the source | outlets reporting it | headline | excerpt)
{lines}

For EVERY id above, rate how good a GTA VI video (Short or long) this could make for our audience.
Return {{"items": [{{"id": <id>, "interest": 0-10, "keep": true|false, "categories": [one or more of "secrets","map","missions","heists"], "reason": "one short sentence"}}]}}
- keep=false for: not really about GTA VI, memes, fan art, polls, generic hype, pure opinion, price or stock news with nothing for players, duplicates of the same news.
- interest 8-10 only for genuinely new, specific information players will want to see."""

ANALYSIS_PROMPT = """RADAR ANALYSIS - pitch a video about this GTA VI discovery. Today: {today}

SOURCES (only these exist; refer to them by id)
{sources}

The strongest verification these sources allow: {ceiling}
(official = Rockstar Games / Take-Two said or showed it; credible = a news outlet reports it; community = players, creators or forums found or claim it; rumor = unverified claim, leak or theory)

VIDEOS AND IDEAS WE ALREADY HAVE (do not pitch the same video again)
{ours}

Category definitions:
- secrets: hidden details, easter eggs, references to older GTA games, hidden characters/objects, unnoticed details, community discoveries
- map: new map discoveries, hidden locations and buildings, Vice City, Leonida regions, comparisons with real Florida, interiors
- missions: mission details and objectives, ways to complete them, gameplay mechanics, character abilities, activities, side missions
- heists: robbery mechanics, bank and store heists, vehicle theft, police response and wanted system, weapons and escape strategies

Return one JSON object:
"headline": neutral one-line description of the discovery (max 110 characters, no hype).
"what_is_new": 1-3 sentences: exactly what is new, as the sources state it.
"why_interesting": 1-2 sentences: why GTA VI players would care.
"verification": "official" | "credible" | "community" | "rumor" - never stronger than the level above; lower it if the sources themselves only relay a rumour.
"verification_note": one sentence saying who says it and how solid it is.
"evidence": list of source ids (e.g. ["S1","S3"]) that support what_is_new.
"categories": 1-3 of "secrets", "map", "missions", "heists".
"coverage": "low" | "medium" | "high" - how widely this is already covered, judged from the sources.
"overlaps_our_videos": true if one of OUR videos/ideas above already covers this; else false.
"unique_angle": the original angle WE can take (not what everyone else will post).
"format": "short" or "long" - the best format for this topic. "format_reason": one sentence.
"urgency": "high" (cover within 24 h) | "medium" (this week) | "low" (evergreen). "urgency_reason": one sentence.
"scores": {{"audience_interest": 0-10, "novelty": 0-10, "timeliness": 0-10, "visual_potential": 0-10, "angle_strength": 0-10}}
"alert": {{"kind": "official_announcement" | "new_mechanic" | "hidden_detail" | "none", "reason": "one sentence"}}
"ideas": {{
  "titles": 3 YouTube titles (max 70 characters, curiosity without lying, no ALL CAPS sentences),
  "hooks": 3 opening lines that grab attention in the first 3 seconds,
  "explanation": 2-3 sentences explaining the topic,
  "why_now": one sentence: why this topic is interesting right now,
  "thumbnail": a thumbnail concept: the image, 2-4 words of text, the emotion,
  "short": {{"hook": the first 3 seconds, "body": the main information (2-5 sentences), "visuals": list of 3-6 shots or gameplay footage to show, "ending": closing line with a question or a call to action}},
  "long": {{"title": "...", "structure": list of 4-8 {{"section": name, "points": [2-4 short points]}}, "talking_points": list of 3-8 key points, "source_ids": ["S1", ...]}}
}}
  Give "short" when format is "short" and "long" when format is "long" (the other one may be null).
  A Shorts script is 75 to 150 words in total (30-60 seconds read aloud).
"caution": what the video must NOT claim as fact (one sentence; empty string if everything is official).
Rules: no URLs anywhere; if verification is community or rumor, the scripts must say so in plain words
("players spotted", "reportedly", "not confirmed by Rockstar yet" ...)."""


def _clean(v: Any) -> Any:
    """Remove URLs the model may have written: sources are only the radar's own entries."""
    if isinstance(v, str):
        return URL_RE.sub("", v).strip()
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def ceiling(items: list[dict[str, Any]]) -> str:
    trusts = {i.get("trust") for i in items}
    if "official" in trusts:
        return "official"
    if "press" in trusts:
        return "credible"
    return "community"


def cap(level: str, top: str) -> str:
    level = level if level in LEVELS else "rumor"
    return LEVELS[min(LEVELS.index(level), LEVELS.index(top))]


# ----------------------------------------------------------------------------- first pass

def triage(llm: LLMProvider, opportunities: list[dict[str, Any]], items_of: Callable[[int], list[dict[str, Any]]], today: str) -> dict[int, dict[str, Any]]:
    """One AI call for a batch of new headlines. Returns {opportunity id: {"interest", "keep", "categories", "reason"}}."""
    lines, ids = [], []
    for o in opportunities:
        its = items_of(int(o["id"]))
        if not its:
            continue
        lead = its[0]
        excerpt = re.sub(r"\s+", " ", lead.get("summary") or "")[:220]
        lines.append(f"{o['id']} | {lead.get('trust')} | {len({i.get('source_id') for i in its})} | {o['title'][:160]} | {excerpt}")
        ids.append(int(o["id"]))
    if not ids:
        return {}

    def validate(data: Any) -> str | None:
        rows = data.get("items") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return '"items" must be a list'
        seen = {r.get("id") for r in rows if isinstance(r, dict)}
        missing = [i for i in ids if i not in seen and str(i) not in {str(x) for x in seen}]
        if missing:
            return f"rate every id; missing: {missing[:10]}"
        return None

    data = llm.complete_json(SYSTEM, TRIAGE_PROMPT.format(today=today, lines="\n".join(lines)), max_tokens=3000, label="radar_triage", required_keys=("items",), validate=validate)
    out: dict[int, dict[str, Any]] = {}
    for r in data["items"]:
        try:
            oid = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        if oid not in ids:
            continue
        cats = [c for c in (r.get("categories") or []) if c in CATEGORIES]
        out[oid] = {"interest": max(0, min(10, int(float(r.get("interest") or 0)))), "keep": bool(r.get("keep")), "categories": cats, "reason": str(r.get("reason") or "")[:240]}
    return out


# ----------------------------------------------------------------------------- full analysis

def source_list(items: list[dict[str, Any]], texts: dict[int, str] | None = None) -> list[dict[str, Any]]:
    out = []
    for n, it in enumerate(items[:12], 1):
        out.append({
            "id": f"S{n}",
            "item_id": it["id"],
            "name": (it.get("meta") or {}).get("publisher") or it.get("source_name") or "",
            "trust": it.get("trust") or "community",
            "url": it["url"],
            "title": it["title"],
            "published_at": it.get("published_at"),
            "text": (texts or {}).get(int(it["id"])) or it.get("summary") or "",
        })
    return out


def _sources_block(sources: list[dict[str, Any]]) -> str:
    blocks = []
    for s in sources:
        body = " ".join(s["text"].split()[:650])
        blocks.append(f"[{s['id']}] {s['name']} (trust: {s['trust']}; published: {s['published_at'] or 'not stated'})\nTitle: {s['title']}\n{body}")
    return "\n\n".join(blocks)


def analyze(llm: LLMProvider, opportunity: dict[str, Any], sources: list[dict[str, Any]], ours: list[str], today: str) -> dict[str, Any]:
    """Full analysis of one opportunity. Raises :class:`FactoryError` when the model cannot produce a valid answer."""
    top = ceiling([{"trust": s["trust"]} for s in sources])
    known = {s["id"] for s in sources}

    def validate(d: Any) -> str | None:
        if not isinstance(d, dict):
            return "expected a JSON object"
        if d.get("verification") not in LEVELS:
            return 'verification must be "official", "credible", "community" or "rumor"'
        cats = [c for c in d.get("categories") or [] if c in CATEGORIES]
        if not cats:
            return 'categories must contain at least one of "secrets", "map", "missions", "heists"'
        if d.get("format") not in ("short", "long"):
            return 'format must be "short" or "long"'
        ev = [e for e in d.get("evidence") or [] if e in known]
        if not ev:
            return f"evidence must list the ids of the sources that support it ({', '.join(sorted(known))})"
        ideas = d.get("ideas")
        if not isinstance(ideas, dict):
            return '"ideas" must be an object'
        if len([t for t in ideas.get("titles") or [] if isinstance(t, str) and t.strip()]) < 3:
            return "ideas.titles needs 3 titles"
        if len([t for t in ideas.get("hooks") or [] if isinstance(t, str) and t.strip()]) < 3:
            return "ideas.hooks needs 3 hooks"
        level = cap(d["verification"], top)
        if d["format"] == "short":
            sh = ideas.get("short")
            if not isinstance(sh, dict) or not all(sh.get(k) for k in ("hook", "body", "ending")):
                return "ideas.short needs hook, body, visuals and ending"
            words = len(" ".join(str(sh.get(k) or "") for k in ("hook", "body", "ending")).split())
            if not 60 <= words <= 175:
                return f"the Shorts script has {words} words; it must have 75 to 150 (30-60 seconds)"
            if level in ("community", "rumor") and not HEDGE.search(" ".join(str(sh.get(k) or "") for k in ("hook", "body", "ending"))):
                return "this is not confirmed by Rockstar: the Shorts script must say so (e.g. 'players spotted', 'reportedly', 'not confirmed yet')"
        else:
            lg = ideas.get("long")
            if not isinstance(lg, dict) or len(lg.get("structure") or []) < 3 or len(lg.get("talking_points") or []) < 3:
                return "ideas.long needs a structure (4-8 sections) and talking_points (3-8)"
        return None

    data = llm.complete_json(
        SYSTEM,
        ANALYSIS_PROMPT.format(today=today, sources=_sources_block(sources), ceiling=top, ours="\n".join(f"- {t}" for t in ours[:40]) or "- (none yet)"),
        max_tokens=5000,
        label="radar_analysis",
        required_keys=("verification", "categories", "format", "ideas", "scores"),
        validate=validate,
    )
    data = _clean(data)
    claimed = data["verification"]
    data["verification"] = cap(claimed, top)
    if data["verification"] != claimed:
        data["verification_note"] = (data.get("verification_note") or "") + f" (The radar lowered '{claimed}' to '{data['verification']}': the sources do not support more.)"
    data["categories"] = [c for c in dict.fromkeys(data.get("categories") or []) if c in CATEGORIES][:3]
    data["evidence"] = [e for e in data.get("evidence") or [] if e in known]
    ideas = data["ideas"]
    lg = ideas.get("long") if isinstance(ideas.get("long"), dict) else None
    if lg is not None:
        lg["source_ids"] = [e for e in lg.get("source_ids") or [] if e in known] or data["evidence"]
    if not isinstance(ideas.get("short"), dict):
        ideas["short"] = None
    if lg is None:
        ideas["long"] = None
    return data


# ----------------------------------------------------------------------------- score

def _sub(scores: dict[str, Any], key: str) -> float:
    try:
        return max(0.0, min(10.0, float(scores.get(key) or 0)))
    except (TypeError, ValueError):
        return 0.0


def score(analysis: dict[str, Any], *, published_at: str | None, outlets: int, now: datetime | None = None, high: int = 75, medium: int = 55) -> dict[str, Any]:
    """0-100 opportunity score + priority, with its breakdown (shown in the dashboard)."""
    now = now or utcnow()
    s = analysis.get("scores") or {}
    base = 10 * (0.30 * _sub(s, "audience_interest") + 0.25 * _sub(s, "novelty") + 0.15 * _sub(s, "timeliness") + 0.15 * _sub(s, "angle_strength") + 0.15 * _sub(s, "visual_potential"))
    trust = {"official": 1.0, "credible": 0.95, "community": 0.85, "rumor": 0.7}.get(analysis.get("verification", "rumor"), 0.7)
    coverage = {"low": 1.05, "medium": 1.0, "high": 0.92}.get(analysis.get("coverage", "medium"), 1.0)
    if outlets >= 4:
        coverage = min(coverage, 0.95)  # everyone already has it: harder to stand out
    ours = 0.6 if analysis.get("overlaps_our_videos") else 1.0
    pub = parse_iso(published_at)
    hours = (now - pub).total_seconds() / 3600 if pub else 24.0
    fresh = 1.0 if hours < 24 else 0.93 if hours < 72 else 0.85 if hours < 168 else 0.75
    value = int(round(max(0.0, min(100.0, base * trust * coverage * ours * fresh))))
    return {
        "score": value,
        "priority": "high" if value >= high else "medium" if value >= medium else "low",
        "breakdown": {"base": round(base, 1), "verification": trust, "coverage": coverage, "already_ours": ours, "freshness": fresh, "age_hours": round(hours, 1), "outlets": outlets},
    }


def urgency(analysis: dict[str, Any], published_at: str | None, now: datetime | None = None) -> str:
    u = analysis.get("urgency") if analysis.get("urgency") in ("high", "medium", "low") else "medium"
    pub = parse_iso(published_at)
    if analysis.get("verification") == "official" and pub and ((now or utcnow()) - pub).total_seconds() < 86400:
        return "high"  # fresh official news: the first hours decide the views
    return u


class AnalysisFailed(FactoryError):
    pass
