"""Put real GTA 6 footage behind the motion graphics, on a target share of the video.

``footage.ratio`` (default 0.8) is measured on narration time: 80 % means that
about 80 % of the running time shows trailer footage or official screenshots
behind the overlays, and the remaining ~20 % stays pure motion graphics. The
graphics-only scenes are the ones that need the cleanest backdrop to be read
(bar charts, source boards, comparisons, timelines...).

Selection is deterministic for a given storyboard and library, so re-running a
step gives the same edit (and the render cache keeps working).

1. ``assign_footage`` (rights step): picks, for each chosen scene, either a photo
   or an ordered list of trailer pieces (each <= ``max_seconds_per_official_clip``),
   matching footage tags with the scene's words when possible and never reusing a
   shot until the library is exhausted.
2. ``cut_scene_clips`` (timeline step, once real durations are known): renders one
   silent H.264 clip per scene at the output resolution (cover-cropped for 9:16),
   which Remotion plays with ``<OffthreadVideo>``.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Callable

from ...app.core.errors import RenderError
from ...app.core.utils import stable_hash, tokens
from .library import DEFAULT_CREDIT, build_index, footage_dir, library_summary

# templates whose content is dense enough to read best on a clean graphic backdrop,
# in the order they are kept graphics-only when the ratio leaves room for some
GRAPHICS_FIRST = ["bars", "sources", "versus", "timeline", "takeaway", "headline", "quote", "stat", "bullets", "countdown", "claim"]
ALWAYS_FOOTAGE = {"kinetic", "title", "chapter", "outro"}

# common words of GTA 6 coverage -> tags that footage of it usually carries
TOPIC_HINTS = {
    "lucia": ["lucia"], "jason": ["jason"], "vice": ["vice", "city"], "leonida": ["leonida"], "miami": ["vice", "city", "beach"],
    "beach": ["beach"], "car": ["car", "drive"], "cars": ["car"], "police": ["police", "cops"], "cops": ["police"], "heist": ["heist", "robbery"],
    "robbery": ["robbery", "heist"], "map": ["aerial", "city"], "trailer": [], "boat": ["boat"], "swamp": ["swamp", "everglades"], "club": ["club", "night"],
}


def _words(sc: dict[str, Any]) -> set[str]:
    base = set(tokens(" ".join([sc.get("narration", ""), sc.get("on_screen_text", ""), sc.get("emphasis", "")])))
    for w in list(base):
        base.update(TOPIC_HINTS.get(w, []))
    return base


def _est_seconds(sc: dict[str, Any], wpm: float) -> float:
    return max(1.5, len(sc.get("narration", "").split()) / wpm * 60.0 + 0.4)


def _pick_scenes(scenes: list[dict[str, Any]], ratio: float, wpm: float) -> set[int]:
    """Scene numbers that get footage, so that footage covers about ``ratio`` of the time."""
    dur = {sc["scene_number"]: _est_seconds(sc, wpm) for sc in scenes}
    total = sum(dur.values()) or 1.0
    budget = (1.0 - ratio) * total  # seconds that may stay graphics-only
    eligible = {sc["scene_number"] for sc in scenes if not (sc["template"] == "broll" and sc.get("media_file"))}
    keep_graphics: set[int] = set()
    if ratio < 1.0:
        rank = {t: i for i, t in enumerate(GRAPHICS_FIRST)}
        cands = sorted((sc for sc in scenes[1:] if sc["template"] in rank and sc["scene_number"] in eligible), key=lambda s: (rank[s["template"]], s["scene_number"]))
        used = 0.0
        for sc in cands:
            d = dur[sc["scene_number"]]
            if used + d <= budget + 0.5:
                keep_graphics.add(sc["scene_number"])
                used += d
    return {n for n in eligible if n not in keep_graphics}


def assign_footage(settings: Any, scenes: list[dict[str, Any]], *, log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Mutates ``scenes`` (adds ``footage``) and returns {"ledger": [...], "summary": {...}}."""
    for sc in scenes:
        sc.pop("footage", None)
    cfg = settings.get("footage", {}) or {}
    if not cfg.get("enabled", True):
        return {"ledger": [], "summary": {"enabled": False}}
    index = build_index(settings, log=log)
    lib = library_summary(index)
    if not lib["usable_shots"] and not lib["photos"]:
        log("footage: the GTA 6 footage library is empty - run `python -m gta_factory footage fetch` to use real trailer footage")
        return {"ledger": [], "summary": {"enabled": True, "library": lib, "share": 0.0}}
    ratio = max(0.0, min(1.0, float(cfg.get("ratio", 0.8))))
    photo_share = max(0.0, min(1.0, float(cfg.get("photo_share", 0.25))))
    max_clip = float(cfg.get("max_clip_seconds") or settings.get("rights_review.max_seconds_per_official_clip", 6))
    min_piece = float(cfg.get("min_piece_seconds", 1.2))
    wpm = float(settings.get("script.words_per_minute", 165))
    chosen = _pick_scenes(scenes, ratio, wpm)

    root = footage_dir(settings)
    shots = [
        {"video": v, "s": sh["s"], "e": sh["e"], "tags": set(v.get("tags", [])) | set(sh.get("tags", [])), "key": f"{v['file']}@{sh['s']}"}
        for v in index["videos"]
        for sh in v["shots"]
        if sh.get("ok")
    ]
    photos = [{**p, "tagset": set(p.get("tags", []))} for p in index["photos"]]
    used_shots: dict[str, int] = {}
    used_photos: dict[str, int] = {}
    ledger: dict[str, dict[str, Any]] = {}
    time_total = sum(_est_seconds(sc, wpm) for sc in scenes) or 1.0
    time_footage = 0.0
    n_photo = n_video = 0

    def ledger_add(file_rel: str, kind: str, credit: str, source_url: str, scene_n: int) -> str:
        aid = "gta6-" + hashlib.sha1(file_rel.encode()).hexdigest()[:10]
        e = ledger.setdefault(aid, {
            "asset_id": aid,
            "kind": kind,
            "file_path": str(root / file_rel),
            "origin_url": source_url or "",
            "license": "Official Rockstar Games promotional material, used for news commentary (short excerpts, transformed with graphics, credited)",
            "commercial_use": True,
            "attribution_required": True,
            "attribution": credit or DEFAULT_CREDIT,
            "review_status": "approved",
            "reason": f"Real GTA 6 footage enabled by footage.ratio. Excerpts <= {max_clip:g}s, credited on screen and in the description. YouTube Content ID may still claim the video for Rockstar/Take-Two.",
            "scenes": [],
        })
        e["scenes"].append(scene_n)
        return aid

    for i, sc in enumerate(scenes):
        n = sc["scene_number"]
        if n not in chosen:
            continue
        words = _words(sc)
        need = _est_seconds(sc, wpm) * 1.5 + 2.0
        # photo or video: alternate deterministically around photo_share, prefer a strong tag match
        want_photo = bool(photos) and (not shots or (n * 0.6180339 % 1.0) < photo_share)
        best_photo = max(photos, key=lambda p: (len(words & p["tagset"]), -used_photos.get(p["file"], 0), p["file"]), default=None) if photos else None
        if best_photo and shots and len(words & best_photo["tagset"]) >= 2 and used_photos.get(best_photo["file"], 0) == 0:
            want_photo = True
        if want_photo and best_photo:
            if not (words & best_photo["tagset"]):
                best_photo = min(photos, key=lambda p: (used_photos.get(p["file"], 0), hashlib.sha1(f"{n}{p['file']}".encode()).hexdigest()))
            used_photos[best_photo["file"]] = used_photos.get(best_photo["file"], 0) + 1
            aid = ledger_add(best_photo["file"], "official_screenshot", best_photo.get("credit", ""), best_photo.get("source_url", ""), n)
            sc["footage"] = {"kind": "photo", "file": str(root / best_photo["file"]), "credit": best_photo.get("credit") or DEFAULT_CREDIT, "asset_ids": [aid]}
            n_photo += 1
        else:
            pieces: list[dict[str, Any]] = []
            got = 0.0
            # rank shots: tag match first, then least used, then a per-scene shuffle
            order = sorted(shots, key=lambda s: (-len(words & s["tags"]), used_shots.get(s["key"], 0), hashlib.sha1(f"{n}{s['key']}".encode()).hexdigest()))
            if order:
                start = order[0]
                vid_shots = [s for s in shots if s["video"]["file"] == start["video"]["file"]]
                j = vid_shots.index(start)
                # follow the trailer's own edit from the chosen shot: consecutive shots feel like real cuts
                k = 0
                while got < need and k < len(vid_shots) * 2:
                    s = vid_shots[(j + k) % len(vid_shots)]
                    k += 1
                    if used_shots.get(s["key"], 0) > used_shots.get(start["key"], 0) and k > 1:
                        continue
                    length = min(max_clip, s["e"] - s["s"])
                    if length < min_piece:
                        continue
                    # take the middle of long shots (the most "settled" part of the camera move)
                    off = max(0.0, (s["e"] - s["s"] - length) / 2)
                    pieces.append({"file": str(root / s["video"]["file"]), "start": round(s["s"] + off, 3), "end": round(s["s"] + off + length, 3)})
                    used_shots[s["key"]] = used_shots.get(s["key"], 0) + 1
                    got += length
            if not pieces:
                continue
            files = sorted({p["file"] for p in pieces})
            aids = []
            for f in files:
                v = next(s["video"] for s in shots if str(root / s["video"]["file"]) == f)
                aids.append(ledger_add(v["file"], "official_trailer", v.get("credit", ""), v.get("source_url", ""), n))
            credit = next(s["video"].get("credit") for s in shots if str(root / s["video"]["file"]) == files[0]) or DEFAULT_CREDIT
            sc["footage"] = {"kind": "video", "pieces": pieces, "credit": credit, "asset_ids": aids}
            n_video += 1
        time_footage += _est_seconds(sc, wpm)
    share = round(time_footage / time_total, 3)
    summary = {"enabled": True, "library": {k: v for k, v in lib.items() if k != "skipped"}, "ratio_target": ratio, "share": share, "video_scenes": n_video, "photo_scenes": n_photo, "graphics_only_scenes": len(scenes) - n_video - n_photo}
    log(f"footage: {share:.0%} of the narration on real GTA 6 footage ({n_video} trailer scene(s), {n_photo} screenshot scene(s), {summary['graphics_only_scenes']} graphics-only)")
    return {"ledger": list(ledger.values()), "summary": summary}


def _cover_filter(w: int, h: int, fps: int) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},format=yuv420p"


def cut_scene_clips(
    settings: Any,
    scenes: list[dict[str, Any]],
    durations: dict[int, float],
    *,
    out_dir: Path,
    width: int,
    height: int,
    fps: int,
    log: Callable[[str], None] = lambda m: None,
) -> int:
    """Render one silent clip per video-footage scene (cached by content). Sets ``footage["clip"]``. Returns clips cut."""
    cut = 0
    cache_dir = settings.jobs_dir / ".cache" / "footage"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for sc in scenes:
        fo = sc.get("footage")
        if not fo or fo.get("kind") != "video":
            continue
        need = durations.get(sc["scene_number"], 0.0) + 0.2
        pieces: list[dict[str, Any]] = []
        total = 0.0
        src = fo["pieces"]
        k = 0
        while total < need and src and k < 200:  # loop the pieces if the voice ran longer than planned
            p = src[k % len(src)]
            pieces.append(p)
            total += p["end"] - p["start"]
            k += 1
        key = stable_hash({"p": pieces, "need": round(need, 2), "w": width, "h": height, "fps": fps, "v": 1})
        cached = cache_dir / f"clip_{key}.mp4"
        if not (cached.exists() and cached.stat().st_size > 0):
            cmd = ["ffmpeg", "-y", "-v", "error"]
            for p in pieces:
                cmd += ["-ss", f"{p['start']:.3f}", "-t", f"{p['end'] - p['start']:.3f}", "-i", p["file"]]
            vf = _cover_filter(width, height, fps)
            chains = "".join(f"[{i}:v]{vf}[v{i}];" for i in range(len(pieces)))
            graph = chains + "".join(f"[v{i}]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=0[out]"
            tmp = cached.with_suffix(".part.mp4")
            cmd += ["-filter_complex", graph, "-map", "[out]", "-t", f"{need:.3f}", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-g", str(fps), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0 or not tmp.exists():
                raise RenderError(f"Could not cut the GTA 6 footage for scene {sc['scene_number']}: {proc.stderr.strip()[-500:]}", hint="Check the files in assets/gta6_footage/videos (`python -m gta_factory footage list`).")
            tmp.rename(cached)
            cut += 1
        dest = out_dir / f"footage_scene_{sc['scene_number']:03d}.mp4"
        out_dir.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.stat().st_size != cached.stat().st_size:
            dest.write_bytes(cached.read_bytes())
        fo["clip"] = str(dest)
    if cut:
        log(f"footage: {cut} scene clip(s) cut from the trailers")
    return cut


def thumbnail_photos(settings: Any, text: str, count: int, *, out_dir: Path) -> list[Path]:
    """Real GTA 6 pictures for the thumbnails: matching screenshots first, else sharp stills from the trailers."""
    cfg = settings.get("footage", {}) or {}
    if not cfg.get("enabled", True) or not cfg.get("thumbnails", True) or count <= 0:
        return []
    index = build_index(settings)
    root = footage_dir(settings)
    words = set(tokens(text))
    for w in list(words):
        words.update(TOPIC_HINTS.get(w, []))
    seed = hashlib.sha1(text.encode()).hexdigest()
    photos = sorted(index["photos"], key=lambda p: (-len(words & set(p.get("tags", []))), hashlib.sha1((seed + p["file"]).encode()).hexdigest()))
    out = [root / p["file"] for p in photos[:count]]
    if len(out) < count:
        shots = [(v, sh) for v in index["videos"] for sh in v["shots"] if sh.get("ok") and sh["e"] - sh["s"] >= 1.5]
        shots.sort(key=lambda x: (-len(words & (set(x[0].get("tags", [])) | set(x[1].get("tags", [])))), -x[1]["luma"], hashlib.sha1((seed + x[0]["file"] + str(x[1]["s"])).encode()).hexdigest()))
        out_dir.mkdir(parents=True, exist_ok=True)
        for v, sh in shots[: (count - len(out)) * 3 : 3] or shots[: count - len(out)]:
            t = round((sh["s"] + sh["e"]) / 2, 2)
            dest = out_dir / f"thumb_still_{len(out) + 1}.jpg"
            proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", str(root / v["file"]), "-frames:v", "1", "-q:v", "2", str(dest)], capture_output=True, text=True)
            if proc.returncode == 0 and dest.exists():
                out.append(dest)
            if len(out) >= count:
                break
    return out
