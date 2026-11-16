"""Thumbnail concepts: the LLM proposes, deterministic rules keep every word honest.

Three concepts are produced (one per layout of ``templates/thumbnails``):
``stamp`` (headline + verdict stamp), ``number`` (a hero figure or date) and
``split`` (analysis). Rules applied after the model:

* the verdict stamp always comes from the fact-check of the lead claim;
* a hero number must appear in the fact-checked claims;
* no clickbait or leak vocabulary, no word implying certainty for unconfirmed news;
* short text only (thumbnails are read at a glance on a phone).
"""
from __future__ import annotations

import re
from typing import Any

from ...providers.llm import LLMProvider

CONCEPTS = ("stamp", "number", "split")
MOTIFS = ("sunset", "calendar", "chart", "document", "question", "controller")
COLORS = ("coral", "cyan", "sun", "violet", "lime")
BANNED = re.compile(r"\b(leak(ed|s)?|shock(ing)?|insane|unbelievable|you won'?t believe|exposed|destroyed|rip|dead|finally confirmed|100%)\b", re.I)
CERTAIN = re.compile(r"\b(confirmed|official(ly)?)\b", re.I)
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

THUMB_SYSTEM = """You design YouTube thumbnails for an independent GTA VI news channel whose identity is
"news, verified": an ink-dark newsroom look with neon colours and a verification stamp.
A thumbnail carries ONE idea in 2-5 big words. It never states more than the fact-check supports,
never uses shock vocabulary, fake urgency, arrows, faces or leaked material."""

THUMB_PROMPT = """STORY
Working title: {title}
Angle: {thesis}

FACT-CHECKED CLAIMS (id, verdict, claim)
{claims}

Design exactly 3 thumbnail concepts, one per layout, in this order: "stamp", "number", "split".
- stamp: 1-3 short lines (max 12 characters per line) stating the news; it gets a verdict stamp.
- number: one hero figure or date taken from the claims ("bigText", max 8 characters, e.g. "$3.2B", "NOV 19", "200+"),
  plus a short "label" (max 26 characters) saying what the number is.
- split: 1-3 short lines (max 12 characters per line) for the "what it means" angle.

Return a JSON object {{"concepts": [...]}} where each concept is:
{{"concept": "stamp|number|split", "lines": ["..."], "accent": "word(s) from lines to colour", "lead_claim_id": "C1",
  "motif": one of {motifs}, "bigText": "" or the figure, "label": "" or short label,
  "color": one of {colors}, "calendar": null or {{"month": "NOV", "day": "19"}} (only with the calendar motif and a date from the claims),
  "why": "one sentence: why a GTA fan would click, honestly"}}"""


def _clean_line(s: Any, n: int = 14) -> str:
    t = re.sub(r"\s+", " ", str(s or "")).strip().strip("\"“”").upper()
    return t[:n].rstrip()


def _numbers_in(text: str) -> set[str]:
    return {m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")}


def normalize_concept(raw: dict[str, Any], fact_check: list[dict[str, Any]], brand: str, fallback_lines: list[str]) -> dict[str, Any]:
    by_id = {c["id"]: c for c in fact_check}
    concept = raw.get("concept") if raw.get("concept") in CONCEPTS else "stamp"
    lead = by_id.get(str(raw.get("lead_claim_id") or "")) or (max(fact_check, key=lambda c: c.get("importance", 3)) if fact_check else None)
    status = lead["classification"] if lead else None
    lines = [_clean_line(x) for x in (raw.get("lines") or []) if _clean_line(x)][:3] or fallback_lines
    notes: list[str] = []
    joined = " ".join(lines)
    if BANNED.search(joined):
        notes.append("Removed shock/leak wording.")
        lines = fallback_lines
    if CERTAIN.search(" ".join(lines)) and status != "confirmed":
        notes.append("Removed certainty wording for an unconfirmed claim.")
        lines = [CERTAIN.sub("", ln).strip() or "GTA 6" for ln in lines]
    big = _clean_line(raw.get("bigText"), 8)
    if big:
        allowed = set().union(*(_numbers_in(c["claim"] + " " + (c.get("event_date") or "")) for c in fact_check)) if fact_check else set()
        nums = _numbers_in(big)
        if nums and not nums <= allowed:
            notes.append(f"Dropped hero number '{big}' (not in the fact-checked claims).")
            big = ""
    if concept == "number" and not big:
        concept = "stamp"
    cal = raw.get("calendar") if isinstance(raw.get("calendar"), dict) else None
    motif = raw.get("motif") if raw.get("motif") in MOTIFS else "document"
    if cal:
        month, day = _clean_line(cal.get("month"), 3), re.sub(r"\D", "", str(cal.get("day") or ""))[:2]
        dated = any(c.get("event_date") and int(c["event_date"][5:7]) == MONTHS.index(month) + 1 and int(c["event_date"][8:10]) == int(day or 0) for c in fact_check if month in MONTHS and re.match(r"\d{4}-\d{2}-\d{2}", c.get("event_date") or ""))
        cal = {"month": month, "day": day} if dated else None
    if motif == "calendar" and not cal:
        motif = "document"
    accent = _clean_line(raw.get("accent"), 26)
    if accent and accent not in " ".join(lines):
        accent = lines[-1].split()[-1] if lines else ""
    return {
        "props": {
            "concept": concept,
            "lines": lines,
            "accent": accent,
            "status": status,
            "motif": motif,
            "bigText": big,
            "label": _clean_line(raw.get("label"), 26),
            "brand": brand,
            "color": raw.get("color") if raw.get("color") in COLORS else "coral",
            "calendar": cal,
        },
        "lead_claim_id": lead["id"] if lead else None,
        "why": str(raw.get("why") or ""),
        "notes": notes,
    }


def _fallback_lines(title: str) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z0-9$%']+", title or "GTA 6 news") if w.upper() not in ("THE", "A", "AN", "OF", "TO", "AND")][:4]
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > 12:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return [ln.upper() for ln in lines[:3]] or ["GTA 6"]


def design_thumbnails(llm: LLMProvider | None, settings: Any, *, script: dict[str, Any], story: dict[str, Any], fact_check: list[dict[str, Any]], log=lambda m: None) -> list[dict[str, Any]]:
    brand = settings.get("channel.name", "NEON DISPATCH")
    fallback = _fallback_lines(script.get("title") or story.get("working_title") or "")
    raw_concepts: list[dict[str, Any]] = []
    if llm is not None:
        claims = "\n".join(f"{c['id']} [{c['classification'].upper()}] {c['claim']}" + (f" (date: {c['event_date']})" if c.get("event_date") else "") for c in fact_check)
        try:
            data = llm.complete_json(
                THUMB_SYSTEM,
                THUMB_PROMPT.format(title=script.get("title"), thesis=(story.get("angle") or {}).get("thesis", ""), claims=claims, motifs=", ".join(MOTIFS), colors=", ".join(COLORS)),
                max_tokens=2500,
                label="thumbnails",
                required_keys=("concepts",),
                validate=lambda d: None if isinstance(d.get("concepts"), list) and d["concepts"] else "concepts must be a non-empty list",
            )
            raw_concepts = [c for c in data["concepts"] if isinstance(c, dict)]
        except Exception as exc:  # a thumbnail must never block the package
            log(f"thumbnail concepts: model failed ({getattr(exc, 'message', exc)}); using the built-in layout")
    if not raw_concepts:
        raw_concepts = [{"concept": "stamp", "lines": fallback, "accent": fallback[-1].split()[-1], "motif": "document", "color": "coral"}]
    count = int(settings.get("thumbnail.concepts", 3))
    out = [normalize_concept(c, fact_check, brand, fallback) for c in raw_concepts[:count]]
    for c in out:
        for note in c["notes"]:
            log(f"thumbnail: {note}")
    return out
