"""Quality control of a Script-to-Short video: sync, authenticity, rights, captions, claims.

The technical checks (codec, size, fps, loudness, black frames ...) are the shared ``run_qc``;
these are the checks specific to a Short cut to a script:

* footage on every beat, and only verified footage on the beats that show something specific
* frame-level sync: the RENDERED video, at the middle of every beat, shows the source frame that
  was matched to that beat (CLIP image similarity on the same crop), so an off-by-one cut, a wrong
  clip or a drift is caught in the file that will be published
* every cut lands with its words (never after them, never long before)
* real gameplay only (no screenshot standing in for a clip), of the game the beat talks about
* rights documented for every clip, the burned-in captions are the script word for word,
  no dead air, no clip shown twice, claims checked

``fully_verified`` is True only when all of that holds: one missing or unverified beat, one clip
without documented rights or found on the web (permission to check), one picture standing in for a clip, one unchecked claim and the video is "not fully verified".
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ...app.core.utils import read_json

FRAME_SIM_MIN = 0.85  # rendered frame vs matched source frame, same crop (CLIP cosine)
VERIFIED = ("verified_vision", "verified_clip")
REGION = "crop=iw:ih*0.42:0:ih*0.2"  # above the captions, below the credit line


def _check(cid: str, severity: str, passed: bool, detail: str, **extra: Any) -> dict[str, Any]:
    return {"id": cid, "severity": severity, "passed": bool(passed), "detail": detail, **extra}


def _frame(path: Path, t: float, vf: str) -> np.ndarray | None:
    proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1", "-vf", f"{vf},scale=224:224", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True)
    if proc.returncode != 0 or len(proc.stdout) < 224 * 224 * 3:
        return None
    return np.frombuffer(proc.stdout[: 224 * 224 * 3], dtype=np.uint8).reshape(224, 224, 3)


def frame_sync(settings: Any, video: Path, edl: list[dict[str, Any]], *, width: int, height: int, log: Callable[[str], None] = lambda m: None) -> list[dict[str, Any]]:
    """For the middle of every footage event: does the rendered video show the matched source frame?"""
    from ..footage.clip import Clip
    from ..footage.library import crop_filter

    model = Clip.load(settings)
    if model is None:
        return []
    out = []
    pairs = []
    for ev in edl:
        if ev["type"] != "shot" or not ev.get("path"):
            continue
        d = ev["record_out"] - ev["record_in"]
        live = d - float(ev.get("hold") or 0)
        mid = min(d / 2, max(0.0, live - 0.1))
        rendered = _frame(video, ev["record_in"] + mid, REGION)
        source = _frame(Path(ev["path"]), float(ev["source_in"]) + mid, f"{crop_filter(ev.get('crop'))}scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},{REGION}")
        if rendered is None or source is None:
            out.append({**_short(ev), "at": round(ev["record_in"] + mid, 3), "similarity": None, "passed": False, "note": "frame could not be read"})
            continue
        pairs.append((ev, mid, rendered, source))
    if pairs:
        vec = model.embed_images([p[2] for p in pairs] + [p[3] for p in pairs])
        n = len(pairs)
        for k, (ev, mid, _, _) in enumerate(pairs):
            sim = float(vec[k] @ vec[n + k])
            # the matched frame must also be closer than any other event's source frame (no swapped clips)
            others = [float(vec[k] @ vec[n + j]) for j in range(n) if j != k and pairs[j][0]["beat"] != ev["beat"]]
            swapped = bool(others) and max(others) > sim + 0.02
            out.append({**_short(ev), "at": round(ev["record_in"] + mid, 3), "similarity": round(sim, 3), "passed": sim >= FRAME_SIM_MIN and not swapped, **({"note": "looks more like another beat's footage"} if swapped else {})})
    log(f"qc: frame sync checked on {len(out)} cut(s)")
    return out


def _short(ev: dict[str, Any]) -> dict[str, Any]:
    return {"beat": ev["beat"] + 1, "text": ev["text"], "file": ev.get("file"), "record_in": ev["record_in"], "source_in": ev.get("source_in")}


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower())


def script_checks(settings: Any, *, job_root: Path, files: Any, video: Path, master: dict[str, Any], log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    plan = read_json(files.plan, {}) or {}
    timing = read_json(files.beats, {}) or {}
    mdata = read_json(files.matches, {}) or {}
    matches = mdata.get("matches") or []
    edl = (read_json(files.edl_json, {}) or {}).get("events") or []
    claims = read_json(files.claims, {}) or {}
    style = read_json(files.style, {}) or {}
    words_data = read_json(job_root / "subtitles" / "words.json", {}) or {}
    props = read_json(job_root / "render" / "props.json", {}) or {}
    checks: list[dict[str, Any]] = []
    by_beat = {m["beat"]: m for m in matches}
    segs_by_path = {(sg["file"], round(sg["start"], 3)): sg for m in matches for sg in m["segments"]}
    for ev in edl:
        sg = segs_by_path.get((ev.get("file"), round(float(ev.get("source_in") or -1), 3)))
        if sg:
            ev["path"], ev["crop"] = sg["path"], sg.get("crop")

    # --- coverage and verification
    specific = [m for m in matches if m.get("specific")]
    missing = [m for m in matches if not m["segments"]]
    missing_specific = [m for m in missing if m.get("specific")]
    unverified = [m for m in specific if m["segments"] and m["status"] not in VERIFIED]
    checks.append(_check("footage.coverage", "critical", not missing_specific,
                         "Every phrase that names something has footage of it." if not missing_specific else f"{len(missing_specific)} phrase(s) have no verified footage and show their words instead: " + "; ".join(f"beat {m['beat'] + 1} “{m['text'][:40]}” ({m.get('reason', '')})" for m in missing_specific),
                         beats=[m["beat"] + 1 for m in missing_specific]))
    if missing and not missing_specific:
        checks.append(_check("footage.coverage_general", "warning", False, f"{len(missing)} hook / teaser / call-to-action beat(s) without footage."))
    checks.append(_check("footage.verified", "critical", not unverified, "Every specific beat's footage was verified frame by frame." if not unverified else f"{len(unverified)} beat(s) with an unverified match.", beats=[m["beat"] + 1 for m in unverified]))
    method = mdata.get("verification")
    checks.append(_check("footage.verification_method", "info", True, "Frames looked at by the local vision model and CLIP." if method == "vision" else "Frames verified by CLIP's vote against look-alikes (no vision model running: start Ollama with a vision model for a second opinion)."))

    # --- authenticity
    segs = [sg for m in matches for sg in m["segments"]]
    photos = [sg for sg in segs if sg["kind"] != "shot"]
    checks.append(_check("authenticity.gameplay", "warning", not photos, "Only moving gameplay / trailer footage (no screenshot stands in for a clip)." if not photos else f"{len(photos)} picture(s) stand in where no gameplay video showed the phrase: " + "; ".join(sorted({f"beat {m['beat'] + 1}" for m in matches for sg in m['segments'] if sg['kind'] != 'shot'})),
                         beats=sorted({m["beat"] + 1 for m in matches for sg in m["segments"] if sg["kind"] != "shot"})))
    wrong_game = [(m["beat"] + 1, sg["game"]) for m in matches for sg in m["segments"] if sg["game"] != (m.get("game") or "gta6")]
    checks.append(_check("authenticity.game", "critical", not wrong_game, "Every clip is from the game its phrase talks about." if not wrong_game else f"Clips from another game: {wrong_game}"))
    unrighted = [sg for sg in segs if sg["rights"] not in ("official", "own", "licensed", "third_party_allowed", "web_found")]
    checks.append(_check("rights.documented", "critical", not unrighted, "Every clip's origin and permission is documented." if not unrighted else f"{len(unrighted)} clip(s) without documented rights: " + ", ".join(sorted({sg['file'] for sg in unrighted}))[:300]))
    web = [sg for sg in segs if sg["rights"] == "web_found"]
    checks.append(_check("rights.web_found", "warning", not web, "No footage found automatically on the web." if not web else f"{len(web)} clip(s) / picture(s) found automatically on the web: check you may use them before publishing: " + "; ".join(sorted({f"{sg.get('creator') or '?'} ({sg.get('source_url') or sg['file']})" for sg in web}))[:400],
                         beats=sorted({m["beat"] + 1 for m in matches for sg in m["segments"] if sg["rights"] == "web_found"})))

    # --- frame-level sync on the rendered file
    w, h = int(props.get("width") or 1080), int(props.get("height") or 1920)
    fsync = frame_sync(settings, video, edl, width=w, height=h, log=log) if video.exists() else []
    bad = [x for x in fsync if not x["passed"]]
    if fsync:
        checks.append(_check("sync.frames", "critical", not bad, f"The rendered video shows the matched source frame on all {len(fsync)} cut(s) (CLIP similarity >= {FRAME_SIM_MIN})." if not bad else f"{len(bad)} cut(s) do not show the matched footage at the expected time: " + "; ".join(f"beat {x['beat']} at {x['at']:.2f}s ({x.get('similarity')})" for x in bad[:6]), cuts=bad[:10]))
    else:
        checks.append(_check("sync.frames", "warning", False, "Frame-level sync could not be checked (no footage on screen, or CLIP unavailable)."))

    # --- cut timing against the words
    late, early = [], []
    for t in timing.get("beats") or []:
        lag = t["speech_start"] - t["start"]
        if lag < -0.02:
            late.append(f"beat {t['beat'] + 1} cut {-lag:.2f}s after its first word")
        elif lag > 0.6:
            early.append(f"beat {t['beat'] + 1} shown {lag:.2f}s before its words")
    checks.append(_check("sync.cut_timing", "critical" if late else "warning", not late and not early, "Every cut lands with its words (at most 0.6 s before, never after)." if not (late or early) else "; ".join(late + early)[:400]))
    methods = words_data.get("methods") or {}
    measured = not methods or set(methods) <= {"provider_timestamps", "forced_alignment"}
    checks.append(_check("sync.word_timing", "warning", measured, f"Word times measured on the narration audio ({methods})." if measured else f"Some word times are estimated from sentence lengths ({methods}): install faster-whisper (`pip install -e \".[align]\"`) for measured word timings."))

    # --- captions = script
    script_words = [_norm(x) for x in plan.get("words") or []]
    burned = [_norm(wd["w"]) for sc in props.get("scenes", []) for ph in sc.get("phrases", []) for wd in ph["words"]]
    checks.append(_check("captions.script", "critical", burned == script_words, "The burned-in captions are the script, word for word." if burned == script_words else f"Captions differ from the script ({len(burned)} vs {len(script_words)} words)."))

    # --- dead air, repetition, frozen frames
    all_words = sorted(words_data.get("words") or [], key=lambda x: x["s"])
    gaps = [{"at": round(a["e"], 2), "seconds": round(b["s"] - a["e"], 2)} for a, b in zip(all_words, all_words[1:]) if b["s"] - a["e"] > 0.7]
    checks.append(_check("audio.dead_air", "warning", not gaps, "No pause longer than 0.7 s in the narration." if not gaps else f"{len(gaps)} pause(s) over 0.7 s.", gaps=gaps[:10]))
    windows: dict[str, list[tuple[float, float, int]]] = {}
    repeats = []
    for m in matches:
        for sg in m["segments"]:
            for a, b, beat in windows.get(sg["file"], []):
                if min(b, sg["end"]) - max(a, sg["start"]) > 0.3 and beat != m["beat"]:
                    repeats.append(f"{sg['file']} {max(a, sg['start']):.1f}s (beats {beat + 1} and {m['beat'] + 1})")
            windows.setdefault(sg["file"], []).append((sg["start"], sg["end"], m["beat"]))
    checks.append(_check("edit.repetition", "warning", not repeats, "No clip is shown twice." if not repeats else "Clip shown twice: " + "; ".join(repeats[:5])))
    frozen = sum(float(ev.get("hold") or 0) for ev in edl)
    checks.append(_check("edit.frozen", "warning", frozen < 0.1, "No frozen frames." if frozen < 0.1 else f"{frozen:.1f}s of frozen last frames (the library had no longer shot): add footage for these beats."))
    longest = max(((ev["record_out"] - ev["record_in"]) for ev in edl), default=0.0)
    checks.append(_check("edit.pacing", "info", True, f"{len(edl)} cuts in {master['total_seconds']:.1f}s (one every {master['total_seconds'] / max(1, len(edl)):.2f}s); longest {longest:.1f}s."))

    # --- claims and style
    cl = claims.get("claims") or []
    contradicted = [c for c in cl if c.get("status") == "contradicted"]
    unchecked = [c for c in cl if c.get("status") == "unverified"]
    checks.append(_check("claims.contradicted", "critical", not contradicted, "No claim is contradicted by a source." if not contradicted else "Contradicted: " + "; ".join(c["claim"][:60] for c in contradicted)))
    checks.append(_check("claims.supported", "warning", not unchecked, f"All {len(cl)} claim(s) are supported by a source." if not unchecked else f"{len(unchecked)} claim(s) not confirmed by any source read ({claims.get('reason') or 'see claims_report.md'})."))
    smeasured = style.get("status") == "measured"
    checks.append(_check("style.reference", "warning", smeasured, "Editing style measured from the reference channel." if smeasured else "The reference channel's style has not been measured yet: the channel's default editing is used (`python -m gta_factory style analyze`)."))

    fully = not missing and not unverified and not photos and not web and not wrong_game and not unrighted and bool(fsync) and not bad and not late and burned == script_words and not contradicted and not unchecked
    sync = {
        "fully_verified": fully,
        "beats": [_beat_row(t, by_beat.get(t["beat"]), [x for x in fsync if x["beat"] == t["beat"] + 1]) for t in timing.get("beats") or []],
        "frame_checks": fsync,
    }
    return {"checks": checks, "fully_verified": fully, "sync": sync}


def _beat_row(t: dict[str, Any], m: dict[str, Any] | None, fchecks: list[dict[str, Any]]) -> dict[str, Any]:
    m = m or {}
    return {
        "beat": t["beat"] + 1,
        "narration": t["text"],
        "start": t["start"], "end": t["end"], "speech_start": t["speech_start"], "speech_end": t["speech_end"],
        "shows": m.get("concept"),
        "status": m.get("status"),
        "confidence": m.get("confidence"),
        "seen": m.get("seen") or None,
        "reason": m.get("reason") if not m.get("segments") else None,
        "footage": [{"file": sg["file"], "source_url": sg.get("source_url") or None, "source_timestamp": sg["start"], "clip_start": sg["start"], "clip_end": sg["end"], "rights": sg["rights"], "credit": sg.get("credit")} for sg in m.get("segments") or []],
        "frame_check": [{"at": x["at"], "similarity": x["similarity"], "passed": x["passed"]} for x in fchecks],
    }


def _ts(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:05.2f}"


def production_report(ctx: Any, report: dict[str, Any], extra: dict[str, Any]) -> str:
    from .produce import files

    f = files(ctx)
    meta = read_json(ctx.paths.metadata, {}) or {}
    master = read_json(ctx.paths.master, {}) or {}
    style = read_json(f.style, {}) or {}
    claims = read_json(f.claims, {}) or {}
    plan = read_json(f.plan, {}) or {}
    out = [
        f"# Production report - {meta.get('title') or plan.get('title', '')}",
        "",
        f"- Job `{ctx.job_id}` - {master.get('total_seconds', 0):.1f}s at {master.get('fps')} fps, 1080x1920 H.264 / AAC",
        f"- QC verdict: **{report['verdict'].upper()}**",
        f"- Fully verified: **{'YES' if extra['fully_verified'] else 'NO'}**",
        f"- Script plan: {plan.get('method')} ({len(plan.get('beats') or [])} beats)",
        f"- Editing style: **{style.get('status', 'not_measured')}**" + (f" - {style.get('reason')}" if style.get("status") != "measured" and style.get("reason") else ""),
        "",
        "## Beat by beat",
        "",
        "| # | Time | Narration | On screen | Source (timestamp) | Status | Conf. | Frame check |",
        "|---|------|-----------|-----------|--------------------|--------|-------|-------------|",
    ]
    for r in extra["sync"]["beats"]:
        src = "<br>".join(f"{x['file']} @ {_ts(x['source_timestamp'])}-{_ts(x['clip_end'])} ({x['rights']})" for x in r["footage"]) or f"**MISSING** - {r.get('reason') or ''}"
        fc = ", ".join(f"{x['similarity']}" + ("" if x["passed"] else " FAIL") for x in r["frame_check"]) or "-"
        out.append(f"| {r['beat']} | {_ts(r['start'])}-{_ts(r['end'])} | {r['narration']} | {r['shows'] or ''}" + (f" (seen: {r['seen']})" if r.get("seen") else "") + f" | {src} | {r['status']} | {r['confidence']} | {fc} |")
    out += ["", "## Checks", ""]
    for c in report["checks"]:
        mark = "PASS" if c["passed"] else c["severity"].upper()
        out.append(f"- [{mark}] `{c['id']}`: {c['detail']}")
    out += ["", "## Claims", ""]
    for c in claims.get("claims") or []:
        out.append(f"- [{c['status'].upper()}] {c['claim']}" + "".join(f" - [{s['publisher']}]({s['url']})" for s in c.get("corroborating_sources") or []))
    if not claims.get("claims"):
        out.append("- (no factual claim)")
    if claims.get("reason"):
        out.append(f"- note: {claims['reason']}")
    out += ["", "## What to do before publishing", ""]
    todo = []
    if not extra["fully_verified"]:
        missing = [r for r in extra["sync"]["beats"] if not r["footage"]]
        if missing:
            todo.append("Add real footage for the missing beats (`python -m gta_factory footage add <file or URL>`), then resume from `footage`: " + "; ".join(f"beat {r['beat']} “{r['narration']}”" for r in missing))
        if any(not c["passed"] and c["id"] == "rights.documented" for c in report["checks"]):
            todo.append("Document the rights of the flagged clips (rights_review.json) or replace them.")
        web = next((c for c in report["checks"] if c["id"] == "rights.web_found" and not c["passed"]), None)
        if web:
            todo.append("Footage found on the web was used (beats " + ", ".join(str(b) for b in web.get("beats") or []) + "): check each source in rights_review.json, or replace it with `python -m gta_factory footage add <file or URL>` and resume from `match`.")
        if any(not c["passed"] and c["id"] == "claims.supported" for c in report["checks"]):
            todo.append("Check the unconfirmed claims (claims_report.md) or reword them.")
    todo.append("Watch the Short once with sound.")
    out += [f"- [ ] {t}" for t in todo]
    return "\n".join(out) + "\n"
