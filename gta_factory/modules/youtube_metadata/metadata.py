"""YouTube metadata: titles, description, chapters, tags, sources and credits.

The model writes the human parts (title options, the opening paragraph, tags).
Everything that must be exact is assembled deterministically: the
confirmed/reported/rumour scorecard comes from the fact-check, chapters from the
render timeline (YouTube rules: first at 0:00, at least three, each >= 10 s),
sources from the articles that were actually read, credits from the rights ledger.
"""
from __future__ import annotations

import re
from typing import Any

from ...app.core.utils import fmt_duration, normalize_ws
from ...providers.llm import LLMProvider
from ..thumbnail_generation.concepts import BANNED, CERTAIN

LABEL = {"confirmed": "CONFIRMED", "reported": "REPORTED", "rumor": "RUMOR", "speculation": "SPECULATION"}

META_SYSTEM = """You write YouTube metadata for an independent GTA VI news channel ("news, verified").
Titles are specific and curiosity-driven but never promise more than the video shows: no shock words,
no fake urgency, no ALL-CAPS sentences, no "leaked", no "confirmed" unless the fact-check says CONFIRMED.
Write in natural English for GTA fans."""

META_PROMPT = """VIDEO
Format: {format_label}, {duration}
Script title: {title}
Angle: {thesis}
Opening of the narration: {hook}

FACT-CHECKED CLAIMS
{claims}

Return a JSON object:
"titles": 3 title options, each max 70 characters, accurate, different from each other
"chosen": index 0-2 of the best one
"intro": 2 short paragraphs (max 600 characters total) for the top of the description: what the video covers and why it matters. No hashtags, no links, no emoji spam.
"tags": 12-20 search tags (lowercase, no #)
"hashtags": 3 hashtags without the # sign (e.g. "GTA6")
"pinned_comment": one question to pin under the video, inviting discussion of the story"""


def _title_ok(t: str, fact_check: list[dict[str, Any]]) -> str | None:
    if not t or len(t) > 100:
        return "empty or longer than 100 characters"
    if BANNED.search(t):
        return "shock or leak wording"
    if CERTAIN.search(t) and not any(c["classification"] == "confirmed" for c in fact_check):
        return "certainty wording without a confirmed claim"
    if sum(1 for w in re.findall(r"[A-Za-z]{4,}", t) if w.isupper()) > 2:
        return "too many all-caps words"
    return None


def youtube_chapters(chapters: list[dict[str, Any]], total_seconds: float) -> list[dict[str, Any]]:
    """Apply YouTube's chapter rules. Returns [] when the video cannot carry valid chapters."""
    out: list[dict[str, Any]] = []
    for c in chapters:
        t = float(c["start_time"])
        if out and t - out[-1]["t"] < 10:
            continue  # too close to the previous marker: merge into it
        out.append({"t": 0.0 if not out else t, "title": c["title"]})
    if out and total_seconds - out[-1]["t"] < 10:
        out.pop()
    if len(out) < 3:
        return []
    return [{"time": fmt_duration(c["t"]), "seconds": round(c["t"], 2), "title": c["title"]} for c in out]


def build_description(*, intro: str, fact_check: list[dict[str, Any]], chapters: list[dict[str, Any]], sources: list[dict[str, Any]], credits: list[str], voice_label: str, hashtags: list[str], channel: dict[str, Any], extra_sections: list[tuple[str, list[str]]] | None = None, how: str | None = None) -> str:
    parts = [intro.strip(), ""]
    for heading, lines in extra_sections or []:
        if lines:
            parts += [heading, *lines, ""]
    top = sorted(fact_check, key=lambda c: -int(c.get("importance", 3)))[:6]
    if top:
        parts.append("WHAT IS CONFIRMED, REPORTED OR RUMOURED")
        parts += [f"[{LABEL[c['classification']]}] {c['claim']} ({c['original_publisher']})" for c in top]
        parts.append("")
    if chapters:
        parts.append("CHAPTERS")
        parts += [f"{c['time']} {c['title']}" for c in chapters]
        parts.append("")
    if sources:
        parts.append("SOURCES")
        parts += [f"- {s['publisher']}: {s['title']} {s['url']}" for s in sources]
        parts.append("")
    parts.append("HOW THIS VIDEO IS MADE")
    parts.append(f"{how or 'Every claim is checked against the sources above and labelled CONFIRMED, REPORTED, RUMOR or SPECULATION.'} Narration: {voice_label}. Graphics, music and sound effects are original.")
    if credits:
        parts.append("Visual credits: " + "; ".join(credits))
    parts.append(f"{channel.get('name', '')} is an independent fan channel, not affiliated with Rockstar Games or Take-Two Interactive.")
    parts.append("")
    if hashtags:
        parts.append(" ".join("#" + h.lstrip("#") for h in hashtags[:3]))
    text = "\n".join(parts).strip()
    text = text.replace("<", "‹").replace(">", "›")  # YouTube rejects angle brackets in descriptions
    if len(text) > 5000:
        text = text[:4990].rsplit("\n", 1)[0]
    return text


def _clip_tags(tags: list[str], limit: int = 480) -> list[str]:
    out, total = [], 0
    for t in tags:
        t = normalize_ws(re.sub(r"[<>#,]", "", str(t))).lower()
        if not t or t in out:
            continue
        cost = len(t) + (2 if " " in t else 0) + (1 if out else 0)
        if total + cost > limit:
            break
        out.append(t)
        total += cost
    return out


def build_metadata(
    llm: LLMProvider | None,
    settings: Any,
    *,
    fmt: str,
    script: dict[str, Any],
    story: dict[str, Any],
    fact_check: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    total_seconds: float,
    sources: list[dict[str, Any]],
    rights: dict[str, Any],
    voice_provider: str,
    log=lambda m: None,
    extra_sections: list[tuple[str, list[str]]] | None = None,
    how: str | None = None,
    extra_tags: list[str] | None = None,
) -> dict[str, Any]:
    fmt_cfg = settings.format(fmt)
    hook = script["sections"][0]["narration"] if script.get("sections") else ""
    claims = "\n".join(f"{c['id']} [{c['classification'].upper()}] {c['claim']}" for c in fact_check)
    data: dict[str, Any] = {}
    if llm is not None:
        try:
            data = llm.complete_json(
                META_SYSTEM,
                META_PROMPT.format(format_label=fmt_cfg.get("label", fmt), duration=fmt_duration(total_seconds), title=script.get("title"), thesis=(story.get("angle") or {}).get("thesis", ""), hook=hook[:600], claims=claims),
                max_tokens=2500,
                label="youtube_metadata",
                required_keys=("titles", "intro", "tags"),
                validate=lambda d: None if isinstance(d.get("titles"), list) and d["titles"] else "titles must be a non-empty list",
            )
        except Exception as exc:
            log(f"metadata: model failed ({getattr(exc, 'message', exc)}); using the script title")
    titles = [normalize_ws(str(t)) for t in data.get("titles") or [] if isinstance(t, str)]
    rejected = {t: why for t in titles if (why := _title_ok(t, fact_check))}
    for t, why in rejected.items():
        log(f"metadata: title rejected ({why}): {t}")
    good = [t for t in titles if t not in rejected]
    chosen_idx = data.get("chosen") if isinstance(data.get("chosen"), int) else 0
    chosen = titles[chosen_idx] if 0 <= chosen_idx < len(titles) and titles[chosen_idx] in good else (good[0] if good else script.get("title") or story.get("working_title") or "GTA 6 news")
    yt_chapters = youtube_chapters(chapters, total_seconds) if fmt_cfg.get("aspect") != "9:16" else []
    voice_label = {"kokoro": "synthetic voice (Kokoro)", "elevenlabs": "synthetic voice (ElevenLabs)", "openai": "synthetic voice (OpenAI)"}.get(voice_provider, "synthetic voice")
    hashtags = [re.sub(r"[^A-Za-z0-9]", "", str(h)) for h in data.get("hashtags") or ["GTA6", "GTAVI", "GTA6News"]]
    hashtags = [h for h in hashtags if h][:3] or ["GTA6"]
    raw_intro = data.get("intro") if isinstance(data.get("intro"), str) else ""
    intro = "\n\n".join(normalize_ws(p) for p in raw_intro.split("\n") if p.strip()) or (story.get("angle") or {}).get("thesis", "") or chosen
    description = build_description(
        intro=intro,
        fact_check=fact_check,
        chapters=yt_chapters,
        sources=sources,
        credits=rights.get("credits") or [],
        voice_label=voice_label,
        hashtags=hashtags,
        channel={"name": settings.get("channel.name", "")},
        extra_sections=extra_sections,
        how=how,
    )
    base_tags = ["gta 6", "gta vi", "grand theft auto 6", "gta 6 news", "rockstar games"]
    tags = _clip_tags(base_tags + list(extra_tags or []) + [str(t) for t in data.get("tags") or []])
    is_short = fmt_cfg.get("aspect") == "9:16"
    return {
        "title": chosen[:100],
        "title_options": titles or [chosen],
        "rejected_titles": rejected,
        "description": description,
        "tags": tags,
        "hashtags": hashtags,
        "chapters": yt_chapters,
        "pinned_comment": normalize_ws(str(data.get("pinned_comment") or "")),
        "category_id": str(settings.get("youtube.category_id", "20")),
        "privacy_status": str(settings.get("youtube.default_privacy", "private")),
        "made_for_kids": bool(settings.get("youtube.made_for_kids", False)),
        "default_language": settings.get("channel.language", "en"),
        "is_short": is_short,
        "playlist": f"{settings.get('channel.playlist_prefix', 'GTA 6')} {'Shorts' if is_short else fmt_cfg.get('label', fmt)}",
        "review_notes": [
            "The narration voice is synthetic and disclosed in the description. YouTube's 'altered or synthetic content' setting is meant for realistic depictions of real people or events; review whether it applies before publishing.",
            "Upload is disabled by default (youtube.upload_enabled: false). Review the video, thumbnail and this metadata first.",
        ],
        "api_body": {
            "snippet": {"title": chosen[:100], "description": description, "tags": tags, "categoryId": str(settings.get("youtube.category_id", "20")), "defaultLanguage": settings.get("channel.language", "en"), "defaultAudioLanguage": settings.get("channel.language", "en")},
            "status": {"privacyStatus": str(settings.get("youtube.default_privacy", "private")), "selfDeclaredMadeForKids": bool(settings.get("youtube.made_for_kids", False))},
        },
    }
