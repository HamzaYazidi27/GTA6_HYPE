"""Build the Remotion timeline (the ``props`` of the Video / Short compositions).

Inputs are the storyboard (``scene_breakdown.json``), the master audio timeline
(``build_timeline``) and the word alignment of every scene. Everything the
templates animate in sync with the voice (caption words, list items appearing
"on cue", the emphasis beat) is converted here to *scene-local frames*, which is
what ``templates/video/shared.tsx`` expects.
"""
from __future__ import annotations

import re
from typing import Any

from ..subtitles.align import burn_in_phrases

CHAPTER_TITLES = {
    "hook": "The news",
    "context": "Context",
    "story": "The story",
    "evidence": "What is confirmed",
    "analysis": "Analysis",
    "community": "What it means for players",
    "conclusion": "Wrap-up",
    "payoff": "Why it matters",
}


def _norm(tok: str) -> str:
    return re.sub(r"[^a-z0-9$%]", "", tok.lower().replace("’", "'"))


def find_phrase(words: list[dict[str, Any]], phrase: str, start_at: int = 0) -> int | None:
    """Index of the first word of ``phrase`` inside ``words`` (token match), searching from ``start_at``."""
    target = [t for t in (_norm(x) for x in phrase.split()) if t]
    if not target:
        return None
    toks = [_norm(w["w"]) for w in words]
    for i in range(start_at, len(toks) - len(target) + 1):
        if toks[i : i + len(target)] == target:
            return i
    # tolerate a cue that is a little off: match on its rarest (longest) token
    key = max(target, key=len)
    for i in range(start_at, len(toks)):
        if toks[i] == key:
            return i
    return None


def scene_local_words(words: list[dict[str, Any]], voice_offset: float) -> list[dict[str, Any]]:
    """Word timings relative to the scene start (the voice starts ``voice_offset`` seconds in)."""
    return [{"w": w["w"], "s": round(w["s"] + voice_offset, 3), "e": round(w["e"] + voice_offset, 3)} for w in words]


def cue_frames(data: dict[str, Any] | None, words: list[dict[str, Any]], fps: int) -> list[int]:
    cues = (data or {}).get("cues") or []
    out: list[int] = []
    cursor = 0
    for cue in cues:
        idx = find_phrase(words, str(cue or ""), cursor) if cue else None
        if idx is None:
            out.append(-1)
            continue
        out.append(int(round(words[idx]["s"] * fps)))
        cursor = idx + 1
    # cues must move forward in time; an out-of-order cue is treated as unknown
    last = -1
    for i, f in enumerate(out):
        if f >= 0 and f < last:
            out[i] = -1
        elif f >= 0:
            last = f
    return out


def emphasis_range(emphasis: str, words: list[dict[str, Any]], fps: int) -> dict[str, int] | None:
    if not emphasis:
        return None
    idx = find_phrase(words, emphasis)
    if idx is None:
        return None
    n = max(1, len([t for t in emphasis.split() if _norm(t)]))
    last = words[min(len(words) - 1, idx + n - 1)]
    return {"from": int(round(words[idx]["s"] * fps)), "to": int(round(last["e"] * fps))}


def phrases_to_frames(phrases: list[dict[str, Any]], fps: int, duration_frames: int) -> list[dict[str, Any]]:
    out = []
    for p in phrases:
        s = int(round(p["s"] * fps))
        e = min(duration_frames, max(s + 1, int(round(p["e"] * fps))))
        out.append({"s": s, "e": e, "words": [{"w": w["w"], "s": int(round(w["s"] * fps)), "e": max(int(round(w["s"] * fps)) + 1, int(round(w["e"] * fps)))} for w in p["words"]]})
    return out


def build_chapters(scenes: list[dict[str, Any]], placed: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    prev = None
    for sc in scenes:
        sec = sc["section"]
        if sec == "cta":
            continue
        if sec != prev:
            p = placed[sc["scene_number"]]
            chapters.append({"title": CHAPTER_TITLES.get(sec, sec.replace("_", " ").title()), "startFrame": p["start_frame"], "start_time": p["start_time"], "section": sec})
            prev = sec
    return chapters


def _source_label(sc: dict[str, Any]) -> str:
    ref = sc.get("source_reference") or ""
    pub = ref.split(" - ", 1)[0].strip()
    return f"SOURCE: {pub.upper()}" if pub else ""


def build_render_props(
    settings: Any,
    *,
    job_id: str,
    fmt: str,
    title: str,
    today: str,
    scenes: list[dict[str, Any]],
    master: dict[str, Any],
    alignments: dict[int, dict[str, Any]],
    media_urls: dict[int, str] | None = None,
    footage: dict[int, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Assemble the ``timeline`` prop. ``alignments`` maps scene number -> {"words": [...]} (scene-audio relative)."""
    profile = settings.video_profile(fmt)
    fps = int(master["fps"])
    vertical = settings.format(fmt).get("aspect") == "9:16"
    placed = {p["scene_number"]: p for p in master["scenes"]}
    media_urls = media_urls or {}
    footage = footage or {}
    out_scenes = []
    for sc in scenes:
        p = placed[sc["scene_number"]]
        words = scene_local_words(alignments[sc["scene_number"]]["words"], p["voice_offset"])
        phrases = burn_in_phrases(words, max_words=3 if vertical else 5, max_chars=18 if vertical else 30)
        item = {
            "n": sc["scene_number"],
            "template": sc["template"],
            "data": sc["data"],
            "section": sc["section"],
            "startFrame": p["start_frame"],
            "durationFrames": p["duration_frames"],
            "background": sc.get("background") or "grid",
            "transition": sc.get("transition") or "cut",
            "status": sc.get("status"),
            "source": _source_label(sc),
            "cueFrames": cue_frames(sc.get("data"), words, fps),
            "emphasis": emphasis_range(sc.get("emphasis") or "", words, fps),
            "emphasisText": sc.get("emphasis") or "",
            "phrases": phrases_to_frames(phrases, fps, p["duration_frames"]),
            "seed": sc["scene_number"] * 7 + 3,
        }
        if sc["scene_number"] in media_urls:
            item["media"] = media_urls[sc["scene_number"]]
            item["mediaCredit"] = sc.get("media_credit") or ""
        elif sc["scene_number"] in footage:
            item["footage"] = footage[sc["scene_number"]]
        out_scenes.append(item)
    chapters = [] if vertical else build_chapters(scenes, placed)
    return {
        "format": "short" if vertical else "long",
        "fps": fps,
        "width": int(profile["width"]),
        "height": int(profile["height"]),
        "totalFrames": int(master["total_frames"]),
        "title": title,
        "today": today[:10],
        "brand": {"name": settings.get("channel.name", "NEON DISPATCH"), "tagline": settings.get("channel.tagline", "")},
        "burnCaptions": bool(settings.get("video.burn_captions", True)),
        "chapters": [{"title": c["title"], "startFrame": c["startFrame"]} for c in chapters],
        "scenes": out_scenes,
    }
