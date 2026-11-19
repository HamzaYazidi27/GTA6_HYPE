"""Quality control of the finished package.

Every check has a severity:

* ``critical`` - the job cannot be marked completed (it ends as ``needs_review``);
* ``warning``  - shown on the dashboard and in the review checklist;
* ``info``     - measurements kept for the record.

The picture and sound are measured on the final MP4 itself with ffmpeg
(blackdetect, freezedetect, silencedetect, EBU R128), not inferred from the plan.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ..voice_generation.engine import measure_loudness

SCRIPT_CRITICAL = {"invented_quote", "overstated_certainty", "unverified_number", "unsupported_fact", "wrong_number_or_date"}
# a video that opens on another story, or never gives what its title promises, must be read before it goes out
FOCUS_CRITICAL = {"off_topic_hook", "title_not_delivered"}


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def add(self, check_id: str, severity: str, passed: bool, detail: str, **data: Any) -> None:
        self.checks.append({"id": check_id, "severity": severity, "passed": bool(passed), "detail": detail, **({"data": data} if data else {})})

    def verdict(self) -> str:
        if any(not c["passed"] and c["severity"] == "critical" for c in self.checks):
            return "fail"
        if any(not c["passed"] and c["severity"] == "warning" for c in self.checks):
            return "warn"
        return "pass"


def probe(path: Path) -> dict[str, Any]:
    proc = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        return {}
    return json.loads(proc.stdout or "{}")


def _fps(rate: str) -> float:
    try:
        num, den = rate.split("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def analyse_picture(path: Path, *, max_black: float, max_static: float) -> dict[str, Any]:
    vf = f"scale=320:-2,blackdetect=d={max(0.05, max_black / 2):.2f}:pix_th=0.10,freezedetect=n=-60dB:d={max_static:.1f}"
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf", vf, "-an", "-f", "null", "-"], capture_output=True, text=True)
    err = proc.stderr
    black = [{"start": float(a), "end": float(b), "duration": float(c)} for a, b, c in re.findall(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", err)]
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", err)]
    durs = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", err)]
    freezes = [{"start": s, "duration": d} for s, d in zip(starts, durs)]
    if len(starts) > len(durs):  # frozen until the end of the file
        freezes.append({"start": starts[-1], "duration": None})
    return {"black": black, "freezes": freezes}


def analyse_silence(path: Path, *, min_seconds: float = 2.5) -> list[dict[str, float]]:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", f"silencedetect=n=-50dB:d={min_seconds}", "-vn", "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", proc.stderr)]
    ends = [(float(a), float(b)) for a, b in re.findall(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", proc.stderr)]
    return [{"start": s, "duration": d} for s, (_, d) in zip(starts, ends)]


def caption_stats(cues: list[dict[str, Any]], max_chars: int, max_cps: float) -> dict[str, Any]:
    long_lines = [c for c in cues if any(len(ln) > max_chars for ln in c["text"].split("\n"))]
    fast = []
    for c in cues:
        dur = max(0.01, c["end"] - c["start"])
        cps = len(c["text"].replace("\n", " ")) / dur
        if cps > max_cps:
            fast.append({"start": c["start"], "cps": round(cps, 1), "text": c["text"][:60]})
    return {"cues": len(cues), "long_lines": len(long_lines), "too_fast": fast}


def run_qc(
    settings: Any,
    *,
    fmt: str,
    video_path: Path,
    master: dict[str, Any],
    props: dict[str, Any],
    cues: list[dict[str, Any]],
    script: dict[str, Any],
    rights: dict[str, Any],
    mix_stats: dict[str, Any],
    thumbnails: list[Path],
    metadata: dict[str, Any],
    expected_fps: int | None = None,
) -> dict[str, Any]:
    q = settings.get("quality_control") or {}
    r = Report()
    profile = settings.video_profile(fmt)
    fmt_cfg = settings.format(fmt)

    # --- container and streams
    info = probe(video_path) if video_path.exists() else {}
    vs = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    as_ = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    r.add("file.exists", "critical", bool(info), "Final MP4 exists and is readable." if info else f"{video_path.name} is missing or unreadable.")
    if not info:
        return {"verdict": r.verdict(), "checks": r.checks}
    r.add("video.stream", "critical", vs is not None and vs.get("codec_name") == "h264", f"Video codec: {vs.get('codec_name') if vs else 'none'}.")
    r.add("audio.stream", "critical", as_ is not None and as_.get("codec_name") == "aac", f"Audio codec: {as_.get('codec_name') if as_ else 'none'}.")
    if vs:
        size_ok = int(vs.get("width", 0)) == int(profile["width"]) and int(vs.get("height", 0)) == int(profile["height"])
        r.add("video.size", "critical", size_ok, f"{vs.get('width')}x{vs.get('height')} (expected {profile['width']}x{profile['height']}).")
        fps = _fps(vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "0/1")
        want_fps = float(expected_fps or profile["fps"])
        r.add("video.fps", "critical", abs(fps - want_fps) < 0.05, f"{fps:.3f} fps (expected {want_fps:g}).")
        r.add("video.pix_fmt", "warning", vs.get("pix_fmt") == settings.get("video.pixel_format", "yuv420p"), f"Pixel format {vs.get('pix_fmt')}.")

    # --- duration and sync
    duration = float(info.get("format", {}).get("duration") or 0)
    drift = abs(duration - float(master["total_seconds"]))
    max_drift = float(q.get("max_duration_drift_seconds", 0.6))
    r.add("sync.duration", "critical", drift <= max_drift, f"File {duration:.2f}s vs timeline {master['total_seconds']:.2f}s (drift {drift:.2f}s, max {max_drift}s).")
    if vs and as_:
        vd, ad = float(vs.get("duration") or duration), float(as_.get("duration") or duration)
        r.add("sync.av", "critical", abs(vd - ad) <= max_drift, f"Video {vd:.2f}s, audio {ad:.2f}s.")
    lo, hi = float(fmt_cfg.get("min_seconds", 0)), float(fmt_cfg.get("max_seconds", 1e9))
    r.add("format.length", "warning", lo * 0.9 <= duration <= hi * 1.1, f"{duration:.0f}s for a {fmt_cfg.get('label', fmt)} (target {lo:.0f}-{hi:.0f}s).")
    if fmt_cfg.get("aspect") == "9:16":
        r.add("format.short_limit", "critical", duration <= 180, f"Shorts must stay under 3 minutes ({duration:.0f}s).")

    # --- picture
    pic = analyse_picture(video_path, max_black=float(q.get("max_black_seconds", 0.6)), max_static=float(q.get("max_static_seconds", 9.0)))
    long_black = [b for b in pic["black"] if b["duration"] > float(q.get("max_black_seconds", 0.6))]
    r.add("picture.black", "critical", not long_black, f"{len(long_black)} black segment(s) longer than {q.get('max_black_seconds', 0.6)}s." if long_black else "No black segments.", segments=long_black[:10])
    r.add("picture.static", "warning", not pic["freezes"], f"{len(pic['freezes'])} static segment(s) of {q.get('max_static_seconds', 9)}s or more." if pic["freezes"] else "The picture never stays static too long.", segments=pic["freezes"][:10])

    # --- sound
    loud = measure_loudness(video_path)
    target = float(settings.get("audio.master_lufs", -14.0))
    r.add("audio.loudness", "warning", abs(loud["lufs"] - target) <= 1.0, f"Integrated loudness {loud['lufs']:.1f} LUFS (target {target}).")
    max_peak = float(q.get("max_audio_peak_db", -0.3))
    r.add("audio.true_peak", "critical", loud["true_peak_db"] <= max_peak, f"True peak {loud['true_peak_db']:.1f} dBTP (max {max_peak}).")
    vom = mix_stats.get("voice_over_music_db")
    min_vom = float(q.get("min_voice_over_music_db", 9.0))
    if vom is not None:
        r.add("audio.voice_clarity", "critical", vom >= min_vom, f"Voice sits {vom:.1f} dB above the music while speaking (min {min_vom}).")
    silences = [s for s in analyse_silence(video_path) if s["start"] > 0.5]
    r.add("audio.silence", "warning", not silences, f"{len(silences)} silence(s) of 2.5s or more." if silences else "No long silences.", segments=silences[:10])

    # --- captions
    cs = caption_stats(cues, int(q.get("max_caption_chars_per_line", 42)), float(q.get("max_caption_cps", 21)))
    r.add("captions.present", "critical", cs["cues"] > 0, f"{cs['cues']} subtitle cues.")
    r.add("captions.line_length", "warning", cs["long_lines"] == 0, f"{cs['long_lines']} cue(s) with a line over {q.get('max_caption_chars_per_line', 42)} characters.")
    r.add("captions.reading_speed", "warning", len(cs["too_fast"]) <= max(1, cs["cues"] // 20), f"{len(cs['too_fast'])} cue(s) faster than {q.get('max_caption_cps', 21)} characters/second.", examples=cs["too_fast"][:5])

    # --- editorial integrity
    quality = script.get("quality") or {}
    lint = [i for i in quality.get("remaining_lint_issues") or [] if i.get("type") in SCRIPT_CRITICAL]
    audit = [i for i in quality.get("remaining_audit_issues") or [] if i.get("type") in SCRIPT_CRITICAL]
    r.add("script.fact_discipline", "critical", not lint and not audit, "No unresolved fact issues in the script." if not (lint or audit) else f"{len(lint) + len(audit)} unresolved fact issue(s) after revision: review script.md before publishing.", issues=(lint + audit)[:10])
    remaining = (quality.get("remaining_lint_issues") or []) + (quality.get("remaining_audit_issues") or [])
    focus = [i for i in remaining if i.get("type") in FOCUS_CRITICAL]
    r.add("script.focus", "critical", not focus, "The script opens on the main story and delivers its title." if not focus else f"{len(focus)} focus issue(s): the hook is not about the main story, or the title's promise is never delivered. Rewrite the hook (`rewrite <job> hook ...`) or change the title.", issues=focus[:10])
    other = [i for i in remaining if i.get("type") not in SCRIPT_CRITICAL | FOCUS_CRITICAL]
    r.add("script.style", "warning", not other, f"{len(other)} style note(s) left (length, phrasing, repetition)." if other else "Script passed the style checks.", issues=other[:10])

    # --- rights
    pending = [a for a in rights.get("assets", []) if a.get("review_status") == "needs_human_review"]
    r.add("rights.cleared", "critical", not pending, "Every asset has a documented licence." if not pending else f"{len(pending)} asset(s) need a human rights review: " + ", ".join(a["asset_id"] for a in pending), assets=[a["asset_id"] for a in pending])
    fo = rights.get("footage") or {}
    lib = fo.get("library") or {}
    if fo.get("mode") == "topic":  # comparisons and top lists: real footage from start to end, of the game being talked about
        covered = sum(sc["durationFrames"] for sc in props.get("scenes", []) if sc.get("footage") or sc.get("footageB")) / max(1, int(props.get("totalFrames") or 1))
        empty = [g["name"] for g in (fo.get("games") or {}).values() if not (g.get("library") or {}).get("usable_shots") and not (g.get("library") or {}).get("photos")]
        r.add("footage.share", "warning", covered >= 0.8 and not empty, f"Real footage on {covered:.0%} of the video." + (f" Empty footage library: {', '.join(empty)} (its scenes have no footage: `python -m gta_factory footage update --game ...`)." if empty else ""), matched=len(fo.get("matched") or []))
    elif fo.get("enabled") and (lib.get("usable_shots") or lib.get("photos")) and fo.get("mode") != "details":
        covered = sum(sc["durationFrames"] for sc in props.get("scenes", []) if sc.get("footage")) / max(1, int(props.get("totalFrames") or 1))
        lo, hi = float(fo.get("ratio_min", 0.8)), float(fo.get("ratio_max", 0.9))
        r.add("footage.share", "warning", lo - 0.03 <= covered <= hi + 0.03, f"Real GTA 6 footage on {covered:.0%} of the video (target {lo:.0%}-{hi:.0%}).", matched=len(fo.get("matched") or []))

    # --- storyboard rhythm (from the render props)
    scenes = props.get("scenes", [])
    if scenes:
        fps_p = props["fps"]
        longest = max(scenes, key=lambda s: s["durationFrames"])
        vertical = int(props.get("height") or 0) > int(props.get("width") or 0)
        limit = settings.rhythm(vertical=vertical)["max_scene_seconds"] + (1.5 if vertical else 2.0)
        r.add("edit.scene_length", "warning", longest["durationFrames"] / fps_p <= limit, f"Longest scene: #{longest['n']} {longest['template']} {longest['durationFrames'] / fps_p:.1f}s.")
        templates = [s["template"] for s in scenes]
        r.add("edit.variety", "info", True, f"{len(set(templates))} different templates over {len(scenes)} scenes.", counts={t: templates.count(t) for t in sorted(set(templates))})

    # --- package
    thumbs_ok = [p for p in thumbnails if p.exists() and p.stat().st_size > 10_000]
    r.add("package.thumbnails", "warning", len(thumbs_ok) >= 1, f"{len(thumbs_ok)} thumbnail(s) rendered.")
    r.add("package.title", "critical", 0 < len(metadata.get("title", "")) <= 100, f"Title: {metadata.get('title', '')!r} ({len(metadata.get('title', ''))} characters).")
    r.add("package.description", "critical", 0 < len(metadata.get("description", "")) <= 5000, f"Description: {len(metadata.get('description', ''))} characters.")

    return {"verdict": r.verdict(), "checks": r.checks, "measurements": {"duration": duration, "lufs": loud["lufs"], "true_peak_db": loud["true_peak_db"], "voice_over_music_db": vom}}
