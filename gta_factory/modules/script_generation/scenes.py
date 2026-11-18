"""Scene planning: turn the approved narration into a scene-by-scene storyboard.

Narration integrity is guaranteed structurally: Python splits the script into
numbered sentences and the planner only assigns *sentence ranges* to scenes, so
the model cannot rewrite, drop or reorder a single spoken word.
"""
from __future__ import annotations

import re
from typing import Any

from ...app.core.utils import contains_quote, normalize_ws, tokens
from ...providers.llm import LLMProvider
from ..visual_research.catalog import DEFAULT_SFX, SECTION_BACKGROUND, SOUND_EFFECTS, TEMPLATES, TRANSITIONS, catalog_prompt, normalize_scene_data, on_screen_text
from .writer import split_sentences, word_count

PLANNER_SYSTEM = """You are the lead motion designer and editor of a GTA VI news channel.
You storyboard a finished narration into scenes for an automated motion-graphics renderer.
The result must feel like a deliberately edited gaming-news video: a visual change every few seconds,
the right graphic for each idea (numbers get stat or bar scenes, dates get timelines, key claims get
fact-check cards, origins get source cards), and no template used three times in a row.
On-screen text supports the voice; it never contradicts it and never adds facts the narration does not say."""

PLANNER_PROMPT = """Today's date: {today}
Video format: {format_label} ({aspect}). Aim for one scene per {scene_words} words on average; a scene covers 1-2 sentences (3 only if they are very short).
A scene never lasts longer than about {max_scene_seconds} seconds ({max_scene_words} words): split long passages into several scenes.

SCENE TEMPLATES (name: when to use, and the exact data object to supply)
{catalog}

FACT-CHECK (status per claim; a "claim" scene must use the exact status listed here)
{claims}

VERIFIED QUOTES (the only text allowed in "quote" scenes)
{quotes}

SOURCE ARTICLES (for "headline" scenes use these real titles)
{sources}

NARRATION - numbered sentences with their section
{sentences}

Return a JSON object {{"scenes": [...]}}. Scenes must cover every sentence exactly once, in order, with no gaps. Each scene:
"sentences": [first_sentence_number, last_sentence_number]
"template": one template name
"data": the data object for that template (short, punchy on-screen wording; numbers and dates must match the narration)
"emphasis": 1-4 consecutive words copied exactly from this scene's narration that carry the meaning (used for caption highlighting)
"visual_description": one sentence describing what the viewer sees and how it moves
"claim_ids": claim ids this scene relies on ([] for transitions/analysis)
"transition": how this scene ENTERS: one of {transitions} ("cut" for most; stronger ones at section changes)
"sound_effect": one of {sfx}

Rules
- First scene: "kinetic" built from the hook. Use "title" exactly once within the first 4 scenes. Last scene: "outro".
- Never the same template three scenes in a row. Use at least {min_variety} different templates.
- "cues" arrays: for each list item give the exact word or short phrase from the scene narration at which it should appear.
- "stat"/"bars" values must be numbers the narration states in that scene.
- Use "claim" for the 3-6 most important claims so viewers always see what is confirmed versus reported or rumoured.
- Every on-screen word comes from what THIS scene's narration says. "takeaway" effects and causes are short phrases built from its own words,
  never generic lines ("maintaining the series' legacy", "embracing new technologies").
- Labels such as "Breaking" or "Just in" only for news published in the last 2 days.
"""


# words too common in this channel's narration to prove that on-screen text is grounded in it
_GENERIC = {"gta", "game", "games", "rockstar", "player", "players", "fans", "series", "new", "future", "big", "more"}
_HYPE_KICKER = re.compile(r"\b(breaking|just in|just dropped|exclusive|alert)\b", re.I)


def _grounded(text: str, narration_stems: set[str]) -> bool:
    """True when ``text`` shares a meaningful word (by its first 5 letters) with the narration."""
    return any(t[:5] in narration_stems for t in tokens(text) if t not in _GENERIC and len(t) > 2 and not t.isdigit())


def _ground_takeaway(payload: dict[str, Any], narration: str) -> dict[str, Any] | None:
    """Keep only the takeaway lines the voice actually says; None (-> fallback) when too little is left."""
    stems = {t[:5] for t in tokens(narration) if t not in _GENERIC}
    keep = [i for i, e in enumerate(payload["effects"]) if _grounded(e, stems)]
    if len(keep) < 2 or not _grounded(payload["cause"], stems):
        return None
    return {**payload, "effects": [payload["effects"][i] for i in keep], "cues": [payload["cues"][i] for i in keep]}


def newest_source_age(articles: list[dict[str, Any]], today: str) -> int | None:
    from ..fact_checking.verify import days_since

    ages = [d for d in (days_since(a.get("published_at"), today) for a in articles) if d is not None]
    return min(ages) if ages else None


def number_sentences(script: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for sec in script["sections"]:
        for sent in split_sentences(sec["narration"]):
            out.append({"n": len(out) + 1, "section": sec["id"], "text": sent, "claim_ids": sec.get("claim_ids") or []})
    return out


def _fallback_scene(text: str, emphasis: str) -> tuple[str, dict[str, Any]]:
    """A safe kinetic scene built from the narration's own words."""
    words = re.findall(r"[A-Za-z0-9$%'’.-]+", emphasis or "") or re.findall(r"[A-Za-z0-9$%'’.-]+", text)[:4]
    words = words[:6]
    lines = [" ".join(words[i : i + 3]) for i in range(0, len(words), 3)] or ["GTA 6"]
    return "kinetic", {"lines": [ln.upper() for ln in lines[:2]], "accent": (words[-1] if words else "").upper(), "kicker": ""}


def _repair_coverage(raw: list[dict[str, Any]], total: int) -> list[dict[str, Any]]:
    """Force the scene list to cover sentences 1..total exactly once, in order."""
    scenes = []
    cursor = 1
    for sc in raw:
        rng = sc.get("sentences")
        if not (isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, int) for x in rng)):
            continue
        start, end = max(cursor, rng[0]), min(total, rng[1])
        if end < cursor:
            continue
        start = cursor  # absorb any gap into this scene
        sc = dict(sc)
        sc["sentences"] = [start, end]
        scenes.append(sc)
        cursor = end + 1
        if cursor > total:
            break
    if cursor <= total:
        if scenes:
            scenes[-1]["sentences"][1] = total
        else:
            scenes.append({"sentences": [1, total], "template": "kinetic", "data": {}})
    return scenes


def plan_scenes(
    llm: LLMProvider,
    settings: Any,
    *,
    script: dict[str, Any],
    fact_check: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    fmt: str,
    today: str,
    allow_broll: bool = False,
    log=lambda m: None,
) -> list[dict[str, Any]]:
    sentences = number_sentences(script)
    total = len(sentences)
    is_short = settings.format(fmt).get("aspect") == "9:16"
    wpm = float(settings.get("script.words_per_minute", 165))
    target_s = float(settings.get("script.short_target_scene_seconds" if is_short else "script.target_scene_seconds", 8))
    scene_words = int(round(target_s * wpm / 60))
    # a Short holds one picture/card for a few seconds at most (the long-form ceiling would let one card sit for 10 s+)
    max_scene_s = float(settings.get("script.short_max_scene_seconds", 6) if is_short else settings.get("script.max_scene_seconds", 14))
    max_words = max(scene_words, int(max_scene_s * wpm / 60))
    age = newest_source_age(articles, today)
    fresh = age is not None and age <= 2
    by_id = {c["id"]: c for c in fact_check}
    claims_txt = "\n".join(f"{c['id']}: {c['classification']} (confidence {c['confidence']}) - {c['claim']} [source: {c['original_publisher']}]" for c in fact_check)
    quotes_txt = "\n".join(f"- \"{q['text']}\" - {q['speaker']} (via {q['publisher']})" for q in quotes) or "(none - do not use the quote template)"
    sources_txt = "\n".join(f"- {a['publisher']} | {(a.get('published_at') or 'date not stated')[:10]} | {a['title']} | by {a.get('author') or 'n/a'}" for a in articles)
    sent_txt = "\n".join(f"{s['n']}. [{s['section']}] {s['text']}" for s in sentences)

    def validate(d: Any) -> str | None:
        sc = d.get("scenes")
        if not isinstance(sc, list) or not sc:
            return "scenes must be a non-empty list"
        covered = 0
        for s in sc:
            rng = s.get("sentences")
            if not (isinstance(rng, list) and len(rng) == 2):
                return "every scene needs sentences: [first, last]"
            covered = max(covered, rng[1] if isinstance(rng[1], int) else 0)
            if s.get("template") not in TEMPLATES:
                return f"unknown template '{s.get('template')}'"
        if covered < total:
            return f"scenes stop at sentence {covered} but the narration has {total} sentences"
        return None

    data = llm.complete_json(
        PLANNER_SYSTEM,
        PLANNER_PROMPT.format(
            today=today,
            format_label=settings.format(fmt).get("label", fmt),
            aspect=settings.format(fmt).get("aspect"),
            scene_words=scene_words,
            catalog=catalog_prompt(allow_broll),
            claims=claims_txt,
            quotes=quotes_txt,
            sources=sources_txt,
            sentences=sent_txt,
            max_scene_seconds=f"{max_scene_s:g}",
            max_scene_words=max_words,
            transitions=", ".join(TRANSITIONS),
            sfx=", ".join(SOUND_EFFECTS),
            min_variety=4 if is_short else 7,
        ),
        max_tokens=14000,
        label="plan_scenes",
        required_keys=("scenes",),
        validate=validate,
    )

    raw = _repair_coverage(data["scenes"], total)
    scenes: list[dict[str, Any]] = []
    fallbacks = 0
    for sc in raw:
        first, last = sc["sentences"]
        chunk = sentences[first - 1 : last]
        # split over-long scenes at sentence boundaries so nothing stays on screen too long
        groups: list[list[dict[str, Any]]] = [[]]
        for s in chunk:
            if groups[-1] and word_count(" ".join(x["text"] for x in groups[-1] + [s])) > max_words:
                groups.append([])
            groups[-1].append(s)
        for gi, group in enumerate(groups):
            narration = " ".join(s["text"] for s in group)
            template = sc.get("template") if gi == 0 else "kinetic"
            payload = normalize_scene_data(template, sc.get("data")) if gi == 0 else None
            emphasis = normalize_ws(str(sc.get("emphasis") or ""))
            if emphasis and emphasis.lower() not in narration.lower():
                emphasis = ""
            claim_ids = [i for i in (sc.get("claim_ids") or []) if i in by_id]
            if template == "takeaway" and payload:
                payload = _ground_takeaway(payload, narration)
            if payload and payload.get("kicker") and _HYPE_KICKER.search(payload["kicker"]) and not fresh:
                payload["kicker"] = ""  # "BREAKING" on a story that is days old reads as clickbait
            if template == "quote" and payload and not any(contains_quote(q["text"], payload["quote"]) or contains_quote(payload["quote"], q["text"]) for q in quotes):
                payload = None  # never show a quotation that is not verified
            if template == "claim" and payload:
                ref = next((by_id[i] for i in claim_ids), None)
                if ref:  # the verdict shown on screen always comes from the fact-check, not from the model
                    payload["status"], payload["confidence"] = ref["classification"], ref["confidence"]
                    payload["source"] = payload["source"] or ref["original_publisher"]
            if template in ("stat", "bars") and payload:
                said = {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", narration)}
                values = [payload["value"]] if template == "stat" else [i["value"] for i in payload["items"]]
                def spoken(v: float) -> bool:
                    forms = {f"{v:g}", f"{v:.1f}".rstrip("0").rstrip("."), str(int(v)) if float(v).is_integer() else ""}
                    return bool(forms & said)
                if template == "stat" and not spoken(values[0]):
                    payload = None
            if payload is None:
                template, payload = _fallback_scene(narration, emphasis)
                if gi == 0:
                    fallbacks += 1
            sec = group[0]["section"]
            status_pool = [by_id[i]["classification"] for i in claim_ids]
            status = next((s for s in ("rumor", "speculation", "reported", "confirmed") if s in status_pool), None)
            ref = by_id.get(claim_ids[0]) if claim_ids else None
            transition = sc.get("transition") if sc.get("transition") in TRANSITIONS and gi == 0 else "cut"
            sfx = sc.get("sound_effect") if sc.get("sound_effect") in SOUND_EFFECTS and gi == 0 else DEFAULT_SFX.get(template, "none")
            scenes.append(
                {
                    "scene_number": len(scenes) + 1,
                    "section": sec,
                    "sentence_range": [group[0]["n"], group[-1]["n"]],
                    "narration": narration,
                    "template": template,
                    "data": payload,
                    "emphasis": emphasis,
                    "on_screen_text": on_screen_text(template, payload),
                    "visual_description": normalize_ws(str(sc.get("visual_description") or "")) if gi == 0 else "Kinetic typography continuing the previous idea.",
                    "transition": transition,
                    "sound_effect": sfx,
                    "claim_ids": claim_ids,
                    "status": status,
                    "source_reference": f"{ref['original_publisher']} - {ref['original_source']}" if ref else "",
                    "background": SECTION_BACKGROUND.get(sec, "grid"),
                }
            )
    # no template three times in a row
    for i in range(2, len(scenes)):
        if scenes[i]["template"] == scenes[i - 1]["template"] == scenes[i - 2]["template"] and scenes[i]["template"] != "kinetic":
            scenes[i]["template"], scenes[i]["data"] = _fallback_scene(scenes[i]["narration"], scenes[i]["emphasis"])
            scenes[i]["on_screen_text"] = on_screen_text("kinetic", scenes[i]["data"])
    # a new section always gets a visible transition and a fresh background family
    prev_sec = None
    for sc in scenes:
        if prev_sec is not None and sc["section"] != prev_sec and sc["transition"] == "cut":
            sc["transition"] = "slice"
        prev_sec = sc["section"]
    if scenes:
        scenes[0]["transition"] = "flash"
    if fallbacks:
        log(f"scene planner: {fallbacks} scene(s) fell back to kinetic typography after validation")
    return scenes
