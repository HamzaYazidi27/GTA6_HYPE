"""The motion-graphics catalogue: every scene template the renderer knows.

This file is the contract between the editorial side (the scene planner picks a
template and fills ``data``) and the Remotion components in ``templates/``.
Each entry has a human description for the planner prompt and a normaliser
that coerces whatever the model returned into exactly what the component expects
(or returns ``None`` so the planner falls back to a safe template).
"""
from __future__ import annotations

import re
from typing import Any, Callable

STATUSES = ("confirmed", "reported", "rumor", "speculation")
TRANSITIONS = ("cut", "wipe", "slice", "glitch", "push", "flash")
SOUND_EFFECTS = ("none", "whoosh", "impact", "riser", "tick", "pop", "glitch", "swell")


def _s(v: Any, n: int) -> str:
    """Clean string, hard-limited to ``n`` characters on a word boundary."""
    t = re.sub(r"\s+", " ", str(v or "")).strip().strip("\"“”")
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v or ""))
    return float(m.group(0).replace(",", "")) if m else None


def _items(v: Any, n: int, limit: int, minimum: int = 1) -> list[str] | None:
    if not isinstance(v, list):
        return None
    out = [_s(x.get("text") if isinstance(x, dict) else x, n) for x in v]
    out = [x for x in out if x][:limit]
    return out if len(out) >= minimum else None


def _cues(v: Any, count: int) -> list[str]:
    cues = [str(c or "") for c in v] if isinstance(v, list) else []
    return (cues + [""] * count)[:count]


KINETIC_WIDTH, KINETIC_LINES = 26, 3
_TAIL_WORDS = {"THE", "A", "AN", "OF", "TO", "AND", "OR", "IS", "ARE", "IN", "ON", "AT", "FOR", "WITH", "THAT", "BUT", "FROM", "BY", "AS", "ITS", "THEIR"}


def _wrap(words: list[str], width: int) -> list[str]:
    out, cur = [], ""
    for w in words:
        w = w[:width]
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    return out + ([cur] if cur else [])


def kinetic_lines(raw: Any, width: int = KINETIC_WIDTH, max_lines: int = KINETIC_LINES) -> list[str]:
    """Big on-screen words: the planner's own line breaks when they fit, otherwise re-wrapped.

    Never cut in the middle with "…" (a hook reading "GTA 6'S SAVE SYSTEM…" looks broken): too
    many words lose the last ones, and a line never ends on a small word ("REPORTS THE").
    """
    if not isinstance(raw, list):
        return []
    given = [re.sub(r"\s+", " ", str(x.get("text") if isinstance(x, dict) else x or "")).strip().strip("\"“”").upper() for x in raw]
    given = [g for g in given if g]
    lines = [piece for g in given for piece in (_wrap(g.split(), width) if len(g) > width else [g])]
    if len(lines) > max_lines:
        words = " ".join(given).split()
        lines = _wrap(words, width)
        while len(lines) > max_lines:
            words = words[:-1]
            lines = _wrap(words, width)
    words = " ".join(lines).split()
    while words and words[-1].strip(",.;:!?") in _TAIL_WORDS and len(words) > 1:
        words = words[:-1]
    if " ".join(words) != " ".join(lines):
        lines = _wrap(words, width)
    return [ln.rstrip(",;") for ln in lines]


def n_kinetic(d: dict[str, Any]) -> dict[str, Any] | None:
    lines = kinetic_lines(d.get("lines"))
    if not lines:
        return None
    accent = re.sub(r"\s+", " ", str(d.get("accent") or "")).strip().upper()
    if accent and accent not in " ".join(lines):
        accent = next((w for w in accent.split() if w in " ".join(lines).split()), "")
    return {"lines": lines, "accent": accent[:KINETIC_WIDTH], "kicker": _s(d.get("kicker"), 44)}


def n_title(d: dict[str, Any]) -> dict[str, Any] | None:
    if not d.get("title"):
        return None
    return {"title": _s(d["title"], 70), "subtitle": _s(d.get("subtitle"), 90), "date": _s(d.get("date"), 30)}


def n_headline(d: dict[str, Any]) -> dict[str, Any] | None:
    if not d.get("headline") or not d.get("publisher"):
        return None
    return {"publisher": _s(d["publisher"], 32), "date": _s(d.get("date"), 30), "headline": _s(d["headline"], 130), "highlight": _s(d.get("highlight"), 48), "byline": _s(d.get("byline"), 40)}


def n_quote(d: dict[str, Any]) -> dict[str, Any] | None:
    if not d.get("quote") or not d.get("speaker"):
        return None
    return {"quote": _s(d["quote"], 230), "speaker": _s(d["speaker"], 60), "publisher": _s(d.get("publisher"), 32)}


def n_claim(d: dict[str, Any]) -> dict[str, Any] | None:
    if not d.get("claim"):
        return None
    status = str(d.get("status") or "reported").lower()
    return {"claim": _s(d["claim"], 125), "status": status if status in STATUSES else "reported", "source": _s(d.get("source"), 40), "confidence": str(d.get("confidence") or "medium").lower(), "note": _s(d.get("note"), 80)}


def n_stat(d: dict[str, Any]) -> dict[str, Any] | None:
    value = _num(d.get("value"))
    if value is None or not d.get("label"):
        return None
    out = {
        "value": value,
        "prefix": _s(d.get("prefix"), 3),
        "suffix": _s(d.get("suffix"), 12),
        "decimals": int(d.get("decimals") or (0 if float(value).is_integer() else 1)),
        "label": _s(d["label"], 60),
        "context": _s(d.get("context"), 90),
        "source": _s(d.get("source"), 40),
    }
    cmp_ = d.get("compare")
    if isinstance(cmp_, dict) and _num(cmp_.get("value")) is not None and cmp_.get("label"):
        out["compare"] = {"value": _num(cmp_["value"]), "label": _s(cmp_["label"], 40), "prefix": out["prefix"], "suffix": _s(cmp_.get("suffix") or out["suffix"], 12)}
    return out


def n_bars(d: dict[str, Any]) -> dict[str, Any] | None:
    raw = d.get("items")
    if not isinstance(raw, list):
        return None
    items = []
    for it in raw[:5]:
        if isinstance(it, dict) and _num(it.get("value")) is not None and it.get("label"):
            items.append({"label": _s(it["label"], 28), "value": _num(it["value"]), "display": _s(it.get("display"), 14), "highlight": bool(it.get("highlight"))})
    if len(items) < 2:
        return None
    return {"title": _s(d.get("title"), 60), "unit": _s(d.get("unit"), 16), "items": items, "source": _s(d.get("source"), 40), "cues": _cues(d.get("cues"), len(items))}


def n_timeline(d: dict[str, Any]) -> dict[str, Any] | None:
    raw = d.get("events")
    if not isinstance(raw, list):
        return None
    events = []
    for ev in raw[:5]:
        if isinstance(ev, dict) and ev.get("label") and ev.get("date"):
            state = str(ev.get("state") or "past").lower()
            events.append({"date": _s(ev["date"], 18), "label": _s(ev["label"], 46), "state": state if state in ("past", "now", "future") else "past"})
    if len(events) < 2:
        return None
    return {"title": _s(d.get("title"), 60), "events": events, "cues": _cues(d.get("cues"), len(events))}


def n_bullets(d: dict[str, Any]) -> dict[str, Any] | None:
    items = _items(d.get("items"), 62, 5, minimum=2)
    if not items:
        return None
    style = str(d.get("style") or "check").lower()
    return {"title": _s(d.get("title"), 56), "items": items, "style": style if style in ("check", "number", "question", "cross") else "check", "cues": _cues(d.get("cues"), len(items))}


def n_versus(d: dict[str, Any]) -> dict[str, Any] | None:
    sides = []
    for key in ("left", "right"):
        side = d.get(key)
        if not isinstance(side, dict) or not side.get("title"):
            return None
        points = _items(side.get("points"), 52, 3)
        if not points:
            return None
        sides.append({"title": _s(side["title"], 30), "points": points, "value": _s(side.get("value"), 16)})
    return {"title": _s(d.get("title"), 56), "left": sides[0], "right": sides[1], "verdict": _s(d.get("verdict"), 80)}


def n_countdown(d: dict[str, Any]) -> dict[str, Any] | None:
    date = str(d.get("target_date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return None
    return {"target_date": date, "label": _s(d.get("label"), 56), "sublabel": _s(d.get("sublabel"), 70)}


def n_sources(d: dict[str, Any]) -> dict[str, Any] | None:
    raw = d.get("sources")
    if not isinstance(raw, list):
        return None
    rows = []
    for r in raw[:5]:
        if isinstance(r, dict) and r.get("publisher"):
            stance = str(r.get("stance") or "reports").lower()
            rows.append({"publisher": _s(r["publisher"], 30), "stance": stance if stance in ("confirms", "reports", "disputes", "silent") else "reports", "note": _s(r.get("note"), 58)})
    if len(rows) < 2:
        return None
    return {"title": _s(d.get("title"), 56), "sources": rows, "cues": _cues(d.get("cues"), len(rows))}


def n_takeaway(d: dict[str, Any]) -> dict[str, Any] | None:
    effects = _items(d.get("effects"), 54, 3)
    if not d.get("cause") or not effects:
        return None
    return {"title": _s(d.get("title"), 50), "cause": _s(d["cause"], 60), "effects": effects, "cues": _cues(d.get("cues"), len(effects))}


def n_chapter(d: dict[str, Any]) -> dict[str, Any] | None:
    if not d.get("title"):
        return None
    return {"title": _s(d["title"], 40).upper(), "index": int(_num(d.get("index")) or 1), "kicker": _s(d.get("kicker"), 40)}


def n_outro(d: dict[str, Any]) -> dict[str, Any] | None:
    if not d.get("question"):
        return None
    return {"question": _s(d["question"], 110), "cta": _s(d.get("cta") or "Tell us in the comments", 44)}


def n_broll(d: dict[str, Any]) -> dict[str, Any] | None:
    if not d.get("query") and not d.get("asset_id"):
        return None
    return {"query": _s(d.get("query"), 60), "asset_id": _s(d.get("asset_id"), 60), "caption": _s(d.get("caption"), 70), "kicker": _s(d.get("kicker"), 40)}


TEMPLATES: dict[str, dict[str, Any]] = {
    "kinetic": {
        "normalize": n_kinetic,
        "use": "Bold kinetic typography for hooks, punchy statements and transitions between ideas.",
        "data": '{"lines": [1-3 lines, max 4 words each], "accent": "the word(s) from lines to colour", "kicker": "optional small label above"}',
    },
    "title": {
        "normalize": n_title,
        "use": "The episode title card right after the cold open (use exactly once, near the start).",
        "data": '{"title": "...", "subtitle": "one-line framing", "date": "e.g. November 15, 2026"}',
    },
    "headline": {
        "normalize": n_headline,
        "use": "Shows where a story comes from: a clean source card with publisher, date and the article headline.",
        "data": '{"publisher": "...", "date": "...", "headline": "the real article title", "highlight": "phrase inside the headline to underline", "byline": "optional author"}',
    },
    "quote": {
        "normalize": n_quote,
        "use": "A direct quotation. ONLY for text from the VERIFIED QUOTES list, copied verbatim.",
        "data": '{"quote": "...", "speaker": "name, role", "publisher": "where it was published"}',
    },
    "claim": {
        "normalize": n_claim,
        "use": "A fact-check card: one claim with its verdict stamp (CONFIRMED / REPORTED / RUMOR / SPECULATION). The channel's signature device; use it for the key claims.",
        "data": '{"claim": "short statement", "status": "confirmed|reported|rumor|speculation", "source": "publisher", "confidence": "high|medium|low", "note": "optional caveat"}',
    },
    "stat": {
        "normalize": n_stat,
        "use": "One big animated number that the narration says in this scene.",
        "data": '{"value": number, "prefix": "$" or "", "suffix": "M" / "%" / " days" ..., "label": "what the number is", "context": "one line of context", "source": "publisher", "compare": optional {"value": number, "label": "..."}}',
    },
    "bars": {
        "normalize": n_bars,
        "use": "Compare 2-5 sourced numbers as animated bars.",
        "data": '{"title": "...", "unit": "...", "items": [{"label": "...", "value": number, "display": "text shown, e.g. $1.2B", "highlight": true|false}], "source": "publisher", "cues": [narration word/phrase at which each bar appears]}',
    },
    "timeline": {
        "normalize": n_timeline,
        "use": "2-5 dated events on an animated timeline (delays, announcements, what comes next).",
        "data": '{"title": "...", "events": [{"date": "Nov 19, 2026", "label": "...", "state": "past|now|future"}], "cues": [narration phrase per event]}',
    },
    "bullets": {
        "normalize": n_bullets,
        "use": "2-5 short points revealed one by one in sync with the narration.",
        "data": '{"title": "...", "items": ["max 9 words each"], "style": "check|number|question|cross", "cues": [narration phrase per item]}',
    },
    "versus": {
        "normalize": n_versus,
        "use": "Two-sided comparison (before/after, claim/reality, option A/option B).",
        "data": '{"title": "...", "left": {"title": "...", "points": ["..."], "value": "optional big value"}, "right": {...}, "verdict": "optional one-line conclusion"}',
    },
    "countdown": {
        "normalize": n_countdown,
        "use": "Countdown to a CONFIRMED upcoming date.",
        "data": '{"target_date": "YYYY-MM-DD", "label": "what happens", "sublabel": "optional"}',
    },
    "sources": {
        "normalize": n_sources,
        "use": "Source board: which outlets confirm, report or dispute the story.",
        "data": '{"title": "...", "sources": [{"publisher": "...", "stance": "confirms|reports|disputes|silent", "note": "short"}], "cues": [narration phrase per row]}',
    },
    "takeaway": {
        "normalize": n_takeaway,
        "use": "Cause -> consequences diagram for analysis ('what this means').",
        "data": '{"title": "...", "cause": "...", "effects": ["2-3 short consequences"], "cues": [narration phrase per effect]}',
    },
    "chapter": {
        "normalize": n_chapter,
        "use": "Very short section bumper. Only on a short transitional sentence that opens a new part.",
        "data": '{"title": "2-4 words", "index": chapter number, "kicker": "optional"}',
    },
    "outro": {
        "normalize": n_outro,
        "use": "The closing question for the comments (last scene).",
        "data": '{"question": "...", "cta": "short call to action"}',
    },
    "broll": {
        "normalize": n_broll,
        "use": "Licensed stock footage or an operator-supplied asset with a caption. Only for atmosphere (city, coast, gaming setup), never presented as game footage.",
        "data": '{"query": "generic stock search terms, e.g. miami skyline night", "caption": "optional on-screen caption", "kicker": "optional label"}',
    },
}

BACKGROUNDS = ("grid", "skyline", "mesh", "rays", "waves", "dots")
SECTION_BACKGROUND = {"hook": "rays", "context": "skyline", "story": "grid", "evidence": "dots", "analysis": "mesh", "community": "waves", "conclusion": "grid", "payoff": "mesh", "cta": "rays"}
DEFAULT_SFX = {"kinetic": "impact", "title": "swell", "headline": "whoosh", "quote": "pop", "claim": "impact", "stat": "riser", "bars": "tick", "timeline": "tick", "bullets": "pop", "versus": "whoosh", "countdown": "tick", "sources": "pop", "takeaway": "whoosh", "chapter": "glitch", "outro": "swell", "broll": "whoosh"}


def normalize_scene_data(template: str, data: Any) -> dict[str, Any] | None:
    spec = TEMPLATES.get(template)
    if not spec or not isinstance(data, dict):
        return None
    try:
        return spec["normalize"](data)
    except (TypeError, ValueError, KeyError):
        return None


def on_screen_text(template: str, data: dict[str, Any]) -> str:
    """Flatten a scene's data into the text a viewer reads (for scene_breakdown.json and QC)."""
    d = data
    pick: Callable[[str], str] = lambda k: str(d.get(k) or "")
    if template == "kinetic":
        return " ".join(d.get("lines", []))
    if template in ("bullets",):
        return " | ".join([pick("title")] + d.get("items", [])).strip(" |")
    if template == "timeline":
        return " | ".join([pick("title")] + [f"{e['date']}: {e['label']}" for e in d.get("events", [])]).strip(" |")
    if template == "bars":
        return " | ".join([pick("title")] + [f"{i['label']}: {i.get('display') or i['value']}" for i in d.get("items", [])]).strip(" |")
    if template == "stat":
        return f"{d.get('prefix', '')}{d.get('value'):g}{d.get('suffix', '')} - {pick('label')}"
    if template == "claim":
        return f"[{pick('status').upper()}] {pick('claim')}"
    if template == "quote":
        return f"\"{pick('quote')}\" - {pick('speaker')}"
    if template == "headline":
        return f"{pick('publisher')}: {pick('headline')}"
    if template == "versus":
        return f"{d['left']['title']} vs {d['right']['title']}"
    if template == "sources":
        return " | ".join([pick("title")] + [f"{s['publisher']}: {s['stance']}" for s in d.get("sources", [])]).strip(" |")
    if template == "takeaway":
        return " -> ".join([pick("cause")] + d.get("effects", []))
    if template == "countdown":
        return f"{pick('label')} ({pick('target_date')})"
    if template == "outro":
        return pick("question")
    if template == "broll":
        return pick("caption")
    return pick("title")


def catalog_prompt(allow_broll: bool) -> str:
    rows = []
    for name, spec in TEMPLATES.items():
        if name == "broll" and not allow_broll:
            continue
        rows.append(f"- {name}: {spec['use']}\n    data = {spec['data']}")
    return "\n".join(rows)
