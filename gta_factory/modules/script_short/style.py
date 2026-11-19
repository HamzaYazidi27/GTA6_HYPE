"""The editing style of a Script-to-Short video, loaded from ``style/style_profile.json``.

The profile is written by the reference analyzer (``python -m gta_factory style analyze``, see
``reference.py``) from measurements of the reference channel's videos. Until that analysis has run,
``load_profile`` returns the channel's own defaults with ``status: "not_measured"``, and every video
says so in its report: nothing is presented as measured when it was not.

``editor_params`` turns the profile into the numbers the editor uses: how long a shot is held, when
the picture punches in, how strongly footage is graded, where captions sit and how many words they
show, how loud the music is under the voice, and which cuts get a sound effect.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROFILE_FILE = "style/style_profile.json"
REPORT_FILE = "style/style_reference_report.md"
REFERENCE_CHANNEL = "https://www.youtube.com/@Tacticalpath"

# the channel's own editing grammar, used until the reference has been measured
DEFAULT_EDITOR = {
    "shot_hold_s": 2.2,          # longest continuous piece of one shot
    "max_beat_s": 4.5,           # a beat longer than this is cut into several shots
    "zoom": {"push_per_s": 0.025, "punch_on_number": 1.12, "punch_frames": 4},
    "grade": {"saturation": 1.0, "contrast": 1.0, "brightness": 0.0, "sharpen": 0.0},
    "captions": {"top": 0.69, "size": 76, "max_words": 3, "uppercase": True, "highlight": "accent"},
    "footage_dim": 0.06,
    "sfx": {"number": "impact", "item_cut": "whoosh", "inner_cut": "none", "cta": "pop", "hook": "riser"},
    "music_gain_db": -19.0,
    "number_badge": True,
    "item_transition": "cut",    # cut | flash: how a new list item comes in
    "sentence_gap_s": 0.08,      # breath between two sentences of the narration
    "cut_lead_s": 0.05,          # a cut lands this long before the word it illustrates
}


def profile_path(settings: Any) -> Path:
    return settings.root / str(settings.get("script_short.style_profile") or PROFILE_FILE)


def default_profile(reason: str = "The reference channel has not been analysed yet.") -> dict[str, Any]:
    return {
        "status": "not_measured",
        "reason": reason,
        "reference": {"channel": REFERENCE_CHANNEL, "videos": []},
        "how_to_measure": "python -m gta_factory style analyze https://www.youtube.com/@Tacticalpath --videos 8",
        "editor": json.loads(json.dumps(DEFAULT_EDITOR)),
    }


def load_profile(settings: Any) -> dict[str, Any]:
    p = profile_path(settings)
    if not p.exists():
        return default_profile()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_profile("style_profile.json could not be read.")
    editor = {**json.loads(json.dumps(DEFAULT_EDITOR)), **(data.get("editor") or {})}
    for k in ("zoom", "grade", "captions", "sfx"):
        editor[k] = {**DEFAULT_EDITOR[k], **((data.get("editor") or {}).get(k) or {})}
    data["editor"] = editor
    return data


def editor_params(settings: Any) -> dict[str, Any]:
    prof = load_profile(settings)
    return {**prof["editor"], "status": prof.get("status", "not_measured"), "reason": prof.get("reason", "")}


def derive_editor(measured: dict[str, Any]) -> dict[str, Any]:
    """Editor settings from the measured reference (bounded so a bad measurement cannot break a video)."""
    e = json.loads(json.dumps(DEFAULT_EDITOR))

    def clamp(v: Any, lo: float, hi: float, default: float) -> float:
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return default

    pacing = measured.get("pacing") or {}
    if pacing.get("shot_median_s"):
        e["shot_hold_s"] = round(clamp(pacing["shot_median_s"] * 1.15, 0.6, 5.0, e["shot_hold_s"]), 2)
        e["max_beat_s"] = round(clamp(pacing.get("shot_p90_s", pacing["shot_median_s"] * 2), 1.2, 6.0, e["max_beat_s"]), 2)
    cam = measured.get("camera") or {}
    if cam.get("mean_zoom_rate_per_s") is not None:
        e["zoom"]["push_per_s"] = round(clamp(cam["mean_zoom_rate_per_s"], 0.0, 0.12, e["zoom"]["push_per_s"]), 4)
    if cam.get("punch_in_share_of_cuts") is not None:
        e["zoom"]["punch_on_number"] = 1.12 if cam["punch_in_share_of_cuts"] >= 0.08 else 1.0
        e["zoom"]["punch_share"] = round(clamp(cam["punch_in_share_of_cuts"], 0, 1, 0), 3)
    col = measured.get("color") or {}
    if col.get("saturation_ratio") is not None:  # reference saturation / typical game footage saturation
        e["grade"]["saturation"] = round(clamp(col["saturation_ratio"], 0.8, 1.4, 1.0), 3)
    if col.get("contrast_ratio") is not None:
        e["grade"]["contrast"] = round(clamp(col["contrast_ratio"], 0.85, 1.3, 1.0), 3)
    cap = measured.get("captions") or {}
    if cap.get("present_share", 0) >= 0.3:
        e["captions"]["top"] = round(clamp(cap.get("center_y", 0.69) - cap.get("height_frac", 0.06) / 2, 0.15, 0.82, 0.69), 3)
        e["captions"]["size"] = int(clamp((cap.get("height_frac") or 0.04) * 1920 * 0.82, 44, 130, 76))
        e["captions"]["max_words"] = int(clamp(round(cap.get("words_on_screen_median") or 3), 1, 6, 3))
    tr = measured.get("transitions") or {}
    if tr.get("flash_share") is not None:
        e["item_transition"] = "flash" if tr["flash_share"] >= 0.15 else "cut"
    aud = measured.get("audio") or {}
    if aud.get("music_db_below_voice") is not None:
        e["music_gain_db"] = round(-clamp(aud["music_db_below_voice"], 8, 30, 19), 1)
    if aud.get("sfx_on_cut_share") is not None:
        share = aud["sfx_on_cut_share"]
        e["sfx"]["item_cut"] = "whoosh" if share >= 0.25 else "none"
        e["sfx"]["inner_cut"] = "whoosh" if share >= 0.6 else "none"
    if aud.get("silence_gaps_per_min") is not None:  # a channel that never pauses between sentences
        e["sentence_gap_s"] = 0.04 if aud["silence_gaps_per_min"] < 2 else 0.08 if aud["silence_gaps_per_min"] < 6 else 0.14
    return e
