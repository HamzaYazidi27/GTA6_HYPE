"""The real GTA 6 footage library: official trailers (video) and official screenshots (photos).

Layout (``footage.dir``, default ``assets/gta6_footage``)::

    videos/       trailers and gameplay videos (mp4/mov/webm/mkv): `footage update`, `footage add`, or copy them here
    photos/       official screenshots (jpg/png/webp), or stills pulled from the trailers
    index.json    built automatically: duration, resolution and the list of usable shots
    autotags.json what the local vision model saw in each shot / photo (`footage autotag`)

Any file dropped anywhere in the folder is picked up by the next job; nothing
else to do. Optional sidecar next to any file, ``<file>.json`` (the dashboard and
`footage tag` write it for you)::

    {"tags": ["lucia", "jason", "vice city", "beach"],
     "credit": "Footage: Rockstar Games",
     "segments": [{"start": 12.0, "end": 19.5, "tags": ["car", "police"]}],
     "exclude": [[0, 4.5], [151, 165]],
     "allow_overlay": false}

Re-uploads are never used by default:

* a file downloaded from someone else's post or channel (X, TikTok, Instagram, a fan's
  YouTube channel...) is left out: only Rockstar's own uploads, the official sites and your
  own captures (``footage add --own``) are used, unless you allow it (``--third-party-ok``,
  ``"allow_third_party": true``);
* a video whose picture keeps a logo, a social-media post or subtitles at the same place
  (``scan_overlays``: for the whole video, or only for a part of it) loses those shots;
* a shot or photo where the vision tagger saw a watermark or subtitles is left out.

Set ``"allow_overlay": true`` in the sidecar to keep a game HUD that was taken for a logo.

Tags are what lets a scene that *says* "Lucia" show Lucia (see ``vocabulary.py``).
They come from, strongest first: the sidecar (per shot or per photo), the file
name (``lucia_beach_01.jpg`` -> lucia, beach), the vision tagger, and the whole
video's tags. Shots are found with ffmpeg's scene-change detector. Dark shots
(title cards, fades, black frames), the first seconds (logos) and the last seconds
(end card, rating screen) are left out automatically.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from ...app.core.errors import ConfigError, FactoryError
from ...app.core.utils import read_json, which, write_json
from .vocabulary import GENERIC_OBJECTS, Vocabulary, load_vocabulary

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
INDEX_VERSION = 3
DEFAULT_CREDIT = "Footage: Rockstar Games"

# official Rockstar Games uploads (edit footage.sources in config.yaml to add newer trailers)
DEFAULT_SOURCES = [
    "https://www.youtube.com/watch?v=QdBZY2fkU-0",  # Grand Theft Auto VI Trailer 1 (Dec 2023)
    "https://www.youtube.com/watch?v=VQRLujxTm3c",  # Grand Theft Auto VI Trailer 2 (May 2025)
]
# where `footage update` looks for new official GTA VI videos
DEFAULT_CHANNELS = ["https://www.youtube.com/@RockstarGames/videos"]
DEFAULT_TITLE_FILTER = r"Grand Theft Auto VI|GTA VI\b|GTA 6\b"

# how much each kind of tag counts when matching footage to the narration
W_ITEM = 3.0  # sidecar tags of a photo, or of a shot (segments), and photo file names: exact
W_AUTO = 2.0  # what the vision model saw in this very shot / photo
W_VIDEO = 2.0  # tags of the whole video (name, sidecar): true for most of its shots
W_OBJECT = 1.0  # other things the vision model noticed


def footage_dir(settings: Any) -> Path:
    d = Path(str(settings.get("footage.dir", "assets/gta6_footage")))
    return d if d.is_absolute() else settings.root / d


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".json")


def _sidecar(path: Path) -> dict[str, Any]:
    side = sidecar_path(path)
    if not side.exists():
        return {}
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def name_tags(path: Path, vocab: Vocabulary | None = None) -> list[str]:
    """``Lucia_beach-01 [QdBZY2fkU-0].jpg`` -> ['beach', 'lucia'] (+ concepts its name pattern carries)."""
    stem = re.sub(r"\[[^\]]*\]", " ", path.stem).replace(".", " ")
    if vocab is None:
        from .vocabulary import Vocabulary as _V

        vocab = _V({})
    return sorted(vocab.tags_from_text(stem) | vocab.file_concepts(path.name))


def canonical_tags(tags: Any, vocab: Vocabulary) -> list[str]:
    if isinstance(tags, str):
        tags = re.split(r"[,;]", tags)
    return sorted({c for t in tags or [] if (c := vocab.canonical(str(t)))})


def _probe(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FactoryError(f"ffprobe could not read {path.name}: {proc.stderr.strip()[-300:]}")
    data = json.loads(proc.stdout or "{}")
    st = (data.get("streams") or [{}])[0]
    return {"width": int(st.get("width") or 0), "height": int(st.get("height") or 0), "duration": float((data.get("format") or {}).get("duration") or 0.0)}


def detect_shots(path: Path, duration: float, *, threshold: float = 0.3, sample_fps: int = 4) -> list[dict[str, float]]:
    """Cut points by scene change, plus the mean brightness (0-255) of each shot."""
    cut_proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    cuts = sorted({round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", cut_proc.stderr)})
    luma_proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf", f"fps={sample_fps},scale=160:-2,signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    samples = [(float(t), float(y)) for t, y in re.findall(r"pts_time:([0-9.]+)[^\n]*\n[^\n]*lavfi\.signalstats\.YAVG=([0-9.]+)", luma_proc.stderr)]
    bounds = [0.0] + [c for c in cuts if 0.2 < c < duration - 0.2] + [duration]
    shots = []
    for s, e in zip(bounds, bounds[1:]):
        if e - s < 0.25:
            continue
        ys = [y for t, y in samples if s <= t < e]
        shots.append({"s": round(s, 3), "e": round(e, 3), "luma": round(sum(ys) / len(ys), 1) if ys else 0.0})
    return shots


def split_long_shots(shots: list[dict[str, Any]], max_len: float) -> list[dict[str, Any]]:
    """Cut long takes into pieces of about ``max_len`` so each can be used on its own."""
    out = []
    for sh in shots:
        length = sh["e"] - sh["s"]
        n = int(length // max_len) if length > 1.5 * max_len else 1
        for k in range(max(1, n)):
            a = sh["s"] + k * length / n if n > 1 else sh["s"]
            b = sh["s"] + (k + 1) * length / n if n > 1 else sh["e"]
            out.append({**sh, "s": round(a, 3), "e": round(b, 3), **({"part": k + 1} if n > 1 else {})})
    return out


# ----------------------------------------------------------------------------- burned-in logos (re-uploads)
OVERLAY_WIDTH = 320  # enough to see a semi-transparent channel logo
OVERLAY_SAMPLES = 28


def _official(settings: Any, side: dict[str, Any]) -> bool:
    """Downloaded from Rockstar itself (``footage.sources`` / ``footage.channels``): never a re-upload."""
    url = str(side.get("source_url") or "")
    sources = {str(u) for u in (settings.get("footage.sources") or DEFAULT_SOURCES)}
    who = " ".join(str(side.get(k) or "") for k in ("uploader", "channel", "channel_url")).lower()
    channels = [str(c).lower().split("/videos")[0] for c in (settings.get("footage.channels") or DEFAULT_CHANNELS)]
    return bool(url and url in sources) or "rockstar games" in who or any(c and c in who for c in channels)


def _host(url: str) -> str:
    m = re.match(r"^https?://([^/:?#]+)", url or "")
    return m.group(1).lower().removeprefix("www.") if m else ""


def third_party(settings: Any, side: dict[str, Any]) -> str | None:
    """Where a file comes from when that is someone else's post or channel (a re-upload), else None.

    Downloaded files keep their ``source_url``: Rockstar's own channels and sites (the primary sources)
    are official; anything else (X, TikTok, Instagram, a fan's YouTube channel, a news site's image)
    is a third party. Local files and your own captures (``own_capture``) are not judged here.
    """
    url = str(side.get("source_url") or "")
    if not url or side.get("own_capture") or _official(settings, side):
        return None
    host = _host(url)
    if host and settings.source_tier(host) == "primary":
        return None
    who = str(side.get("uploader") or side.get("channel") or "").strip()
    return f"{who} on {host}" if who else host or "another website"


def static_overlay_box(frames: Any, *, edge_th: float = 24.0, persist: float = 0.8) -> dict[str, Any] | None:
    """A logo that stays at the same place while the picture changes: [x0, y0, x1, y1] (0-1) or None.

    ``frames`` is a uint8 array [n, h, w] sampled across the whole video. A pixel belongs to an overlay
    when it is an edge in most frames although the footage under it keeps changing; straight lines
    across the frame (letterbox, borders) are ignored. A video that barely moves is not judged.
    """
    import numpy as np

    if frames.ndim != 3 or len(frames) < 8:
        return None
    f = frames.astype(np.float32)
    n, h, w = f.shape
    edge = (np.abs(np.diff(f, axis=2))[:, :-1, :] + np.abs(np.diff(f, axis=1))[:, :, :-1]) > edge_th
    stable = edge.mean(axis=0) >= persist
    moving = f.std(axis=0)[:-1, :-1] > 16
    if moving.mean() < 0.35:  # a still or nearly still video: everything is "static", nothing to conclude
        return None
    stable[stable.mean(axis=1) > 0.35, :] = False  # horizontal lines (letterbox edges, borders)
    stable[:, stable.mean(axis=0) > 0.35] = False
    hh, ww = stable.shape
    if stable.sum() < max(10, 0.0008 * stable.size):
        return None
    gy, gx = 8, 8
    best, cell = 0.0, (0, 0)
    for cy in range(gy):
        for cx in range(gx):
            block = stable[cy * hh // gy : (cy + 1) * hh // gy, cx * ww // gx : (cx + 1) * ww // gx]
            d = float(block.mean()) if block.size else 0.0
            if d > best:
                best, cell = d, (cy, cx)
    if best < 0.035:
        return None
    cy, cx = cell
    y0, y1 = max(0, (cy - 1) * hh // gy), min(hh, (cy + 2) * hh // gy)
    x0, x1 = max(0, (cx - 1) * ww // gx), min(ww, (cx + 2) * ww // gx)
    ys, xs = np.nonzero(stable[y0:y1, x0:x1])
    if len(ys) < 10:
        return None
    box = [(x0 + xs.min()) / ww, (y0 + ys.min()) / hh, (x0 + xs.max() + 1) / ww, (y0 + ys.max() + 1) / hh]
    if box[2] - box[0] < 0.02 or box[3] - box[1] < 0.015:  # a thin line (the edge of letterbox bars, a border), not a logo or text
        return None
    return {"box": [round(float(v), 3) for v in box], "density": round(best, 3)}


def detect_static_overlay(path: Path, duration: float, *, samples: int = OVERLAY_SAMPLES) -> dict[str, Any] | None:
    """Look for a channel logo / watermark burned into a video (see ``static_overlay_box``)."""
    import numpy as np

    if duration < 6:
        return None
    a, b = 0.08 * duration, 0.88 * duration  # past the intro logo, before the end card
    fps = samples / max(1.0, b - a)
    vf = f"fps={fps:.4f},scale={OVERLAY_WIDTH}:-2,format=gray"
    proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(path), "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True)
    info = _probe(path) if proc.returncode == 0 and proc.stdout else None
    if not info or not info["width"]:
        return None
    sh = int(round(OVERLAY_WIDTH * info["height"] / info["width"] / 2)) * 2
    n = len(proc.stdout) // (OVERLAY_WIDTH * sh)
    frames = np.frombuffer(proc.stdout[: n * OVERLAY_WIDTH * sh], dtype=np.uint8).reshape(n, sh, OVERLAY_WIDTH)
    frames = frames[frames.reshape(len(frames), -1).mean(axis=1) > 22]  # fades and black cards say nothing
    return static_overlay_box(frames)


OVERLAY_SCAN_FPS = 1.5
OVERLAY_WINDOW = 16  # frames (~10 s): an overlay that stays that long at the same place is not part of the game


def scan_overlays(path: Path, duration: float) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """(overlay of the whole video, overlays of parts of it). One decode of the video.

    The whole-video check is ``detect_static_overlay``'s; the parts are found with the same test on
    windows of ~10 s, so a post's interface, a channel logo or subtitles shown during one part of a
    compilation are found too. Each part is then followed frame by frame for as long as the same
    strokes stay in place. Returns spans [{"s", "e", "box"}] in seconds.
    """
    import numpy as np

    if duration < 6:
        return None, []
    fps = min(OVERLAY_SCAN_FPS, 900 / duration)
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf", f"fps={fps:.4f},scale={OVERLAY_WIDTH}:-2,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True)
    info = _probe(path) if proc.returncode == 0 and proc.stdout else None
    if not info or not info["width"]:
        return None, []
    sh = int(round(OVERLAY_WIDTH * info["height"] / info["width"] / 2)) * 2
    n = len(proc.stdout) // (OVERLAY_WIDTH * sh)
    frames = np.frombuffer(proc.stdout[: n * OVERLAY_WIDTH * sh], dtype=np.uint8).reshape(n, sh, OVERLAY_WIDTH)
    times = np.array([(i + 0.5) / fps for i in range(n)])
    bright = frames.reshape(n, -1).mean(axis=1) > 22  # fades and black cards say nothing
    core = bright & (times >= 0.08 * duration) & (times <= 0.88 * duration)  # past the intro logo, before the end card
    whole = static_overlay_box(frames[core])
    spans: list[dict[str, Any]] = []
    if whole is not None or n < OVERLAY_WINDOW:
        return whole, spans
    f = frames.astype(np.float32)
    edges = (np.abs(np.diff(f, axis=2))[:, :-1, :] + np.abs(np.diff(f, axis=1))[:, :, :-1]) > 24.0
    hh, ww = edges.shape[1:]
    covered = -1
    for a in range(0, n - OVERLAY_WINDOW + 1, OVERLAY_WINDOW // 4):
        idx = [i for i in range(a, a + OVERLAY_WINDOW) if bright[i]]
        if len(idx) < OVERLAY_WINDOW * 2 // 3 or idx[-1] <= covered:
            continue
        box = static_overlay_box(frames[idx])
        if box is None:
            continue
        x0, y0, x1, y1 = (int(round(v * d)) for v, d in zip(box["box"], (ww, hh, ww, hh)))
        stable = edges[idx, y0:y1, x0:x1].mean(axis=0) >= 0.8
        if stable.sum() < 6:
            continue

        def present(i: int) -> bool:  # the same strokes are still there
            return bool(edges[i, y0:y1, x0:x1][stable].mean() >= 0.6)

        lo, hi = idx[0], idx[-1]
        while lo > 0 and present(lo - 1):
            lo -= 1
        while hi < n - 1 and present(hi + 1):
            hi += 1
        s, e = round(max(0.0, float(times[lo]) - 0.5 / fps), 2), round(float(times[hi]) + 0.5 / fps, 2)
        if spans and s <= spans[-1]["e"]:
            spans[-1]["e"] = max(spans[-1]["e"], e)
        else:
            spans.append({"s": s, "e": e, "box": box["box"]})
        covered = hi
    return whole, spans


def _where(box: list[float]) -> str:
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return ("top" if y < 0.34 else "bottom" if y > 0.66 else "middle") + "-" + ("left" if x < 0.34 else "right" if x > 0.66 else "center")


# ----------------------------------------------------------------------------- letterbox (black bars)
LB_WIDTH = 160  # detection works on small grey frames


def _gray_frames(path: Path, *, fps: float | None) -> tuple[list[float], Any]:
    """Small grey frames of a video (``fps`` per second) or of a picture (``fps=None``): (times, uint8 array [n, h, w])."""
    import numpy as np

    vf = (f"fps={fps}," if fps else "") + f"scale={LB_WIDTH}:-2,format=gray"
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf", vf, *([] if fps else ["-frames:v", "1"]), "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return [], np.zeros((0, 1, 1), dtype=np.uint8)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        w, h = (int(x) for x in probe.stdout.strip().split(",")[:2])
    except ValueError:
        return [], np.zeros((0, 1, 1), dtype=np.uint8)
    sh = int(round(LB_WIDTH * h / w / 2)) * 2
    n = len(proc.stdout) // (LB_WIDTH * sh)
    frames = np.frombuffer(proc.stdout[: n * LB_WIDTH * sh], dtype=np.uint8).reshape(n, sh, LB_WIDTH)
    times = [(i + 0.5) / fps for i in range(n)] if fps else [0.0]
    return times, frames


def bars_of(frame: Any) -> tuple[int, int] | None:
    """(top, bottom) rows of letterbox bars in one grey frame; None when there are none (or the frame is too dark to tell).

    Letterbox bars are near-black, at the top AND the bottom, of about the same height: a dark sky
    or a night street is never symmetrical with the ground, so it is not taken for bars.
    """
    import numpy as np

    h = frame.shape[0]
    dark = (frame.mean(axis=1) < 18) & (np.percentile(frame, 95, axis=1) < 36)
    if dark.all() or not dark[0] or not dark[-1]:
        return None
    top = int(np.argmin(dark))
    bottom = int(np.argmin(dark[::-1]))
    if min(top, bottom) < 0.04 * h or abs(top - bottom) > max(2, 0.025 * h) or h - top - bottom < 0.5 * h:
        return None
    return top, bottom


def _crop_from(bars: list[tuple[int, int] | None], small_h: int, w: int, h: int) -> list[int] | None:
    """The crop [w, h, x, y] (source pixels) when most informative frames show the same bars."""
    from collections import Counter

    seen = [b for b in bars if b is not None]
    if not seen or len(seen) < 0.6 * len(bars):
        return None
    import math

    (top, bottom), _ = Counter(seen).most_common(1)[0]
    # the first non-black row of the small frame may still hold a few black pixel rows: cut it too, never a dark line
    y0 = math.ceil((top + 1) * h / small_h / 2) * 2
    y1 = h - math.ceil((bottom + 1) * h / small_h / 2) * 2
    return [int(w), max(2, (y1 - y0) // 2 * 2), 0, y0]


def detect_letterbox(path: Path, shots: list[dict[str, Any]], width: int, height: int, *, fps: float = 2.0) -> None:
    """Sets ``crop`` ([w, h, x, y] or None) on each shot: the black bars to cut away before framing it."""
    times, frames = _gray_frames(path, fps=fps)
    bars = [bars_of(f) for f in frames]
    small_h = frames.shape[1] if len(frames) else 1
    for sh in shots:
        mine = [b for t, b in zip(times, bars) if sh["s"] <= t < sh["e"]]
        sh["crop"] = _crop_from(mine, small_h, width, height) if mine else None


def photo_crop(path: Path) -> list[int] | None:
    _, frames = _gray_frames(path, fps=None)
    if not len(frames):
        return None
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        w, h = (int(x) for x in probe.stdout.strip().split(",")[:2])
    except ValueError:
        return None
    return _crop_from([bars_of(frames[0])], frames.shape[1], w, h)


def crop_filter(crop: list[int] | None) -> str:
    """ffmpeg filter (with a trailing comma) that removes the black bars, or ""."""
    return f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else ""


def cropped_photo(root: Path, rel: str, crop: list[int] | None) -> Path:
    """A picture without its black bars (cached next to the library), or the picture itself."""
    src = root / rel
    if not crop:
        return src
    dest = root / ".cropped" / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', Path(rel).stem)[:60]}_{crop[1]}x{crop[3]}.jpg"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", crop_filter(crop).rstrip(","), "-q:v", "2", str(dest)], capture_output=True)
        if proc.returncode != 0 or not dest.exists():
            return src
    return dest


def _usable(shot: dict[str, float], duration: float, cfg: dict[str, Any], exclude: list[list[float]]) -> bool:
    if shot["e"] - shot["s"] < float(cfg.get("min_shot_seconds", 1.0)):
        return False
    if shot["luma"] < float(cfg.get("min_brightness", 28)):
        return False
    if shot["s"] < float(cfg.get("skip_head_seconds", 3.0)) or shot["e"] > duration - float(cfg.get("skip_tail_seconds", 10.0)):
        return False
    for a, b in exclude:
        if shot["s"] < float(b) and shot["e"] > float(a):
            return False
    return True


def _overlap(seg: dict[str, Any], sh: dict[str, Any]) -> float:
    try:
        return min(float(seg.get("end", 0)), sh["e"]) - max(float(seg.get("start", 0)), sh["s"])
    except (TypeError, ValueError):
        return 0.0


def _blocked(text: str, blocked: list[str]) -> str | None:
    low = re.sub(r"[_\-.]+", " ", (text or "").lower())  # file names: "leaked_build.jpg" must match "leaked"
    for word in blocked:
        if re.search(rf"\b{re.escape(word.lower())}\b", low):
            return word
    return None


def build_index(settings: Any, *, force: bool = False, log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Scan the footage folder and (re)index files that are new or changed. Cheap when nothing changed."""
    root = footage_dir(settings)
    cfg = settings.get("footage", {}) or {}
    blocked = [str(w) for w in settings.get("rights_review.blocked_keywords", []) or []]
    vocab = load_vocabulary(settings)
    index_path = root / "index.json"
    old = read_json(index_path, {}) or {}
    if old.get("version") != INDEX_VERSION:
        old = {}
    old_videos = {v["file"]: v for v in old.get("videos", [])}
    old_photos = {(ph["file"], ph.get("size")): ph for ph in old.get("photos", []) if "crop" in ph}
    videos, photos, skipped = [], [], []
    if not root.exists():
        return {"version": INDEX_VERSION, "videos": [], "photos": [], "skipped": []}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith(".") or any(part.startswith(".") for part in p.relative_to(root).parts[:-1]):
            continue
        ext = p.suffix.lower()
        if ext not in VIDEO_EXT and ext not in PHOTO_EXT:
            continue
        rel = p.relative_to(root).as_posix()
        side = _sidecar(p)
        hit = _blocked(" ".join([p.name, " ".join(str(t) for t in side.get("tags") or []), str(side.get("source_url") or "")]), blocked)
        if hit:
            skipped.append({"file": rel, "reason": f"blocked keyword '{hit}' (leaked or re-uploaded material is never used)"})
            continue
        tags = sorted(set(name_tags(p, vocab)) | set(canonical_tags(side.get("tags"), vocab)))
        credit = str(side.get("credit") or cfg.get("credit") or DEFAULT_CREDIT)
        st = p.stat()
        foreign = third_party(settings, side)
        allowed_foreign = bool(side.get("allow_third_party"))
        if ext in PHOTO_EXT:
            if foreign and not allowed_foreign:
                skipped.append({"file": rel, "reason": f"third-party post ({foreign}): re-uploads are not used (`footage add <url> --third-party-ok` to allow it)"})
                continue
            known = old_photos.get((rel, st.st_size))
            crop = known["crop"] if known else photo_crop(p)
            photo = {"id": p.stem, "file": rel, "size": st.st_size, "tags": tags, "credit": credit, "source_url": side.get("source_url", ""), "crop": crop, **({"allow_overlay": True} if side.get("allow_overlay") else {}),
                     **({"web_found": True} if side.get("web_found") else {}),
                     **({"article_media": side["article_media"], "caption": side.get("caption", ""), "role": side.get("role", "")} if side.get("article_media") else {})}
            if side.get("from_video"):  # a still pulled from a trailer: same picture as that shot
                photo.update(from_video=str(side["from_video"]), time=float(side.get("time") or 0), video_tags=canonical_tags(side.get("video_tags"), vocab))
            photos.append(photo)
            continue
        prev = old_videos.get(rel)
        cropped = bool(prev) and all("crop" in sh for sh in prev.get("shots") or [])
        if prev and cropped and prev.get("rules") == RULES and not force and prev.get("size") == st.st_size and prev.get("mtime") == int(st.st_mtime) and prev.get("sidecar") == side and prev.get("tags") == tags:
            skipped += _skip_notes(rel, prev["overlay"], prev.get("overlay_spans") or [], foreign, side)
            videos.append(prev)
            continue
        if not which("ffmpeg") or not which("ffprobe"):
            raise ConfigError("ffmpeg / ffprobe are needed to index the GTA 6 footage.", hint="macOS: `brew install ffmpeg`.")
        same_file = prev and prev.get("size") == st.st_size and prev.get("mtime") == int(st.st_mtime) and not force
        if same_file:  # only tags or sidecar changed: keep the detected shots
            info = {k: prev[k] for k in ("width", "height", "duration")}
            shots = [{k: v for k, v in sh.items() if k not in ("ok", "tags")} for sh in prev["shots"]]
        else:
            log(f"footage: indexing {p.name} ...")
            info = _probe(p)
            shots = detect_shots(p, info["duration"], threshold=float(cfg.get("scene_threshold", 0.3)))
            shots = split_long_shots(shots, float(cfg.get("max_clip_seconds") or settings.get("rights_review.max_seconds_per_official_clip", 6)))
        if not all("crop" in sh for sh in shots):  # black bars of each shot (also for videos indexed before this existed)
            detect_letterbox(p, shots, info["width"], info["height"])
        if same_file and prev and "overlay_spans" in prev:
            overlay, spans = prev["overlay"], prev["overlay_spans"]
        elif _official(settings, side) or side.get("own_capture"):
            overlay, spans = None, []
        else:  # a re-upload keeps the other channel's logo, a post's interface or subtitles at the same place
            overlay, spans = scan_overlays(p, info["duration"])
            if overlay:
                log(f"footage: {p.name}: a logo / watermark stays {_where(overlay['box'])} in the picture: not used (re-upload?)."
                    f" If it is part of the game (HUD), put \"allow_overlay\": true in {sidecar_path(p).name}")
            elif spans:
                when = ", ".join("%.0f-%.0fs" % (x["s"], x["e"]) for x in spans[:6])
                log(f"footage: {p.name}: text or a logo stays on screen during {when}: those shots are not used")
        if foreign and not allowed_foreign and not same_file:
            log(f"footage: {p.name}: posted by a third party ({foreign}): not used. Your own capture? `footage add <file> --own`. Allowed re-use? put \"allow_third_party\": true in {sidecar_path(p).name}")
        skipped += _skip_notes(rel, overlay, spans, foreign, side)
        blocked_overlay = (bool(overlay) and not side.get("allow_overlay")) or (bool(foreign) and not allowed_foreign)
        hidden = [] if side.get("allow_overlay") else spans
        seg_tags = [seg for seg in side.get("segments") or [] if isinstance(seg, dict)]
        exclude = [x for x in side.get("exclude") or [] if isinstance(x, list) and len(x) == 2]
        for sh in shots:
            sh["ok"] = _usable(sh, info["duration"], cfg, exclude) and not blocked_overlay and not any(_overlap({"start": x["s"], "end": x["e"]}, sh) > 0.2 for x in hidden)
            # a tagged part of the video applies to the shots it really covers (not one it grazes at a cut)
            extra = {t for seg in seg_tags if _overlap(seg, sh) >= min(1.0, 0.5 * (sh["e"] - sh["s"])) for t in canonical_tags(seg.get("tags"), vocab)}
            if extra:
                sh["tags"] = sorted(extra)
        videos.append({
            "id": re.sub(r"[^a-z0-9]+", "-", p.stem.lower()).strip("-")[:60] or "video",
            "file": rel,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "sidecar": side,
            "tags": tags,
            "credit": credit,
            "source_url": side.get("source_url", ""),
            **info,
            "overlay": overlay,
            "overlay_spans": spans,
            "rules": RULES,
            "shots": shots,
        })
        if not same_file:
            log(f"footage: {p.name}: {info['duration']:.0f}s, {sum(1 for s in shots if s['ok'])}/{len(shots)} usable shots")
    index = {"version": INDEX_VERSION, "videos": videos, "photos": photos, "skipped": skipped}
    if index != old:
        write_json(index_path, index)
    return index


RULES = 2  # version of the re-upload rules applied to an indexed video (a new version re-checks every video once)


def _skip_notes(rel: str, overlay: dict[str, Any] | None, spans: list[dict[str, Any]], foreign: str | None, side: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if foreign and not side.get("allow_third_party"):
        out.append({"file": rel, "reason": f"third-party post ({foreign}): re-uploads are not used (`footage add <url> --third-party-ok` to allow it)"})
    if side.get("allow_overlay"):
        return out
    if overlay:
        out.append({"file": rel, "reason": f"logo or watermark burned into the picture ({_where(overlay['box'])}): looks like a re-upload"})
    elif spans:
        out.append({"file": rel, "reason": "logo, post or subtitles burned into part of the video (" + ", ".join(f"{x['s']:.0f}-{x['e']:.0f}s {_where(x['box'])}" for x in spans[:4]) + "): those shots are not used"})
    return out


def library_summary(index: dict[str, Any]) -> dict[str, Any]:
    usable = [s for v in index.get("videos", []) for s in v.get("shots", []) if s.get("ok")]
    return {
        "videos": len(index.get("videos", [])),
        "photos": len(index.get("photos", [])),
        "usable_shots": len(usable),
        "usable_seconds": round(sum(s["e"] - s["s"] for s in usable), 1),
        "skipped": index.get("skipped", []),
    }


# ----------------------------------------------------------------------------- tags as the selector sees them

def shot_key(file_rel: str, size: int, sh: dict[str, Any]) -> str:
    return f"{file_rel}|{size}|{(sh['s'] + sh['e']) / 2:.1f}"


def photo_key(file_rel: str, size: int) -> str:
    return f"{file_rel}|{size}"


def autotags_path(settings: Any) -> Path:
    return footage_dir(settings) / "autotags.json"


def load_autotags(settings: Any) -> dict[str, Any]:
    data = read_json(autotags_path(settings), {}) or {}
    return data.get("items") or {}


def still_shot_key(index: dict[str, Any], photo: dict[str, Any]) -> str | None:
    """The autotag key of the trailer shot a still was pulled from (its tags apply to the still)."""
    if not photo.get("from_video"):
        return None
    v = next((v for v in index.get("videos", []) if v["file"] == photo["from_video"]), None)
    if v is None:
        return None
    t = float(photo.get("time") or 0)
    sh = next((sh for sh in v.get("shots", []) if sh["s"] <= t < sh["e"]), None)
    return shot_key(v["file"], v.get("size", 0), sh) if sh else None


def load_items(settings: Any, index: dict[str, Any] | None = None, *, job: str | None = None, web: bool | None = None) -> dict[str, Any]:
    """Every usable shot and photo with its weighted tags, plus the vocabulary that knows all of them.

    Pictures and videos taken from a job's articles (``article_media``) belong to that job only: they are
    left out for any other video, and marked ``story`` for their own job's (``job``).

    Footage a Short found by itself on the web (``web_found``, kept in the library) is used by the
    Shorts made from a script (``web=True``); the other videos use it only with
    ``footage.use_web_found: true`` (its permission has not been checked).

    ``{"shots": [...], "photos": [...], "vocab": Vocabulary, "index": index}``. Each item has
    ``tags`` = {concept: weight} (see W_ITEM / W_AUTO / W_VIDEO / W_OBJECT).
    """
    index = index if index is not None else build_index(settings)
    root = footage_dir(settings)
    auto = load_autotags(settings)
    vocab = load_vocabulary(settings)

    def add(tags: dict[str, float], names: Any, w: float) -> None:
        for t in names or []:
            if t and tags.get(t, 0.0) < w:
                tags[t] = w

    def add_auto(tags: dict[str, float], key: str) -> None:
        a = auto.get(key) or {}
        add(tags, a.get("tags"), W_AUTO)
        add(tags, [o for o in a.get("objects") or [] if o not in GENERIC_OBJECTS], W_OBJECT)

    def overlaid(key: str, allowed: Any) -> bool:
        """The vision tagger saw a watermark, a post or burned-in subtitles there, or a real-world photo (not the game)."""
        seen = auto.get(key) or {}
        return (bool(seen.get("overlay")) and not allowed) or seen.get("game") is False

    def belongs(story_job: Any) -> bool | None:
        """None: not article media; True: this job's; False: another job's (never shown)."""
        return None if not story_job else story_job == job

    web = bool(settings.get("footage.use_web_found", False)) if web is None else web
    shots, photos = [], []
    for v in index.get("videos", []):
        side = v.get("sidecar") or {}
        mine = belongs(side.get("article_media"))
        if mine is False or (side.get("web_found") and not web and not mine):
            continue
        for sh in v.get("shots", []):
            if not sh.get("ok") or overlaid(shot_key(v["file"], v.get("size", 0), sh), side.get("allow_overlay")):
                continue
            tags: dict[str, float] = {}
            add(tags, v.get("tags"), W_VIDEO)
            add_auto(tags, shot_key(v["file"], v.get("size", 0), sh))
            add(tags, sh.get("tags"), W_ITEM)
            shots.append({"kind": "shot", "video": v, "file": v["file"], "path": str(root / v["file"]), "s": sh["s"], "e": sh["e"], "vdur": v.get("duration", sh["e"]), "luma": sh.get("luma", 0), "crop": sh.get("crop"), "tags": tags, "key": f"{v['file']}@{sh['s']}", "autokey": shot_key(v["file"], v.get("size", 0), sh),
                          **({"story": True, "caption": side.get("caption", ""), "role": side.get("role", "")} if mine else {})})
    for p in index.get("photos", []):
        mine = belongs(p.get("article_media"))
        if mine is False or (p.get("web_found") and not web and not mine):
            continue
        own = photo_key(p["file"], p.get("size", 0))
        if overlaid(own if own in auto else (still_shot_key(index, p) or own), p.get("allow_overlay")):
            continue
        tags = {}
        add(tags, p.get("video_tags"), W_VIDEO)
        add_auto(tags, own if own in auto else (still_shot_key(index, p) or own))
        add(tags, p.get("tags"), W_ITEM)
        photos.append({"kind": "photo", **p, "path": str(cropped_photo(root, p["file"], p.get("crop"))), "source_path": str(root / p["file"]), "tags": tags, "key": p["file"], "autokey": own if own in auto or not still_shot_key(index, p) else still_shot_key(index, p), **({"story": True} if mine else {})})
    every_tag = {t for it in shots + photos for t in it["tags"]}
    return {"shots": shots, "photos": photos, "vocab": vocab.with_tags(every_tag), "index": index}


def concept_coverage(settings: Any, items: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Each known concept and how many shots / photos carry it (0 = said in a video, nothing to show)."""
    items = items or load_items(settings)
    vocab = load_vocabulary(settings)
    rows = []
    for cid, c in vocab.concepts.items():
        n_shots = sum(1 for it in items["shots"] if cid in it["tags"])
        n_photos = sum(1 for it in items["photos"] if cid in it["tags"])
        rows.append({"concept": cid, "say": (c.get("say") or [cid.replace("_", " ")])[:4], "shots": n_shots, "photos": n_photos})
    return rows


# ----------------------------------------------------------------------------- fetch / import

def _yt_dlp():
    try:
        import yt_dlp  # type: ignore

        return yt_dlp
    except ImportError:
        return None


AGE_GATE = re.compile(r"confirm your age|age[- ]restricted|inappropriate for some users|sign in to confirm", re.I)


def _cookies(settings: Any) -> str:
    """``footage.cookies_from_browser`` (chrome, safari, firefox, edge, brave ...): lets yt-dlp open age-restricted videos."""
    return str(settings.get("footage.cookies_from_browser") or "").strip()


def _failure_hint(settings: Any, message: str) -> str:
    if AGE_GATE.search(message or "") and not _cookies(settings):
        return (
            " - YouTube asks to sign in for this video (age-restricted). Log into YouTube in your browser, then set"
            ' footage.cookies_from_browser: "chrome" (or safari, firefox, edge, brave) in config.yaml and try again.'
        )
    return ""


def fetch(settings: Any, urls: list[str] | None = None, *, max_height: int = 1080, tags: list[str] | None = None, log: Callable[[str], None] = print) -> list[Path]:
    """Download videos with yt-dlp into ``videos/`` (skips files already there)."""
    urls = urls or list(settings.get("footage.sources") or DEFAULT_SOURCES)
    blocked = [str(w) for w in settings.get("rights_review.blocked_keywords", []) or []]
    out_dir = footage_dir(settings) / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    yt_dlp = _yt_dlp()
    if yt_dlp is None and not which("yt-dlp"):
        raise ConfigError("yt-dlp is not installed.", hint="Run `pip install -e \".[footage]\"` (or `pip install yt-dlp`), then retry.")
    done: list[Path] = []
    failed: list[str] = []
    fmt = f"bv*[height<={max_height}][ext=mp4]/bv*[height<={max_height}]/b[height<={max_height}]/b"
    template = str(out_dir / "%(title).80B [%(id)s].%(ext)s")
    for url in urls:
        before = set(out_dir.iterdir())
        log(f"footage: downloading {url}")
        if yt_dlp is not None:
            opts = {"format": fmt, "outtmpl": template, "noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True, "download_archive": str(out_dir / ".downloaded.txt"), "writeinfojson": False}
            if _cookies(settings):
                opts["cookiesfrombrowser"] = (_cookies(settings),)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False) or {}
                    hit = _blocked(str(info.get("title") or ""), blocked)
                    if hit:
                        log(f"footage: skipped '{info.get('title')}' (blocked keyword '{hit}')")
                        continue
                    ydl.download([url])
            except Exception as exc:  # one bad URL must not stop the others
                failed.append(url)
                log(f"footage: download failed for {url}: {str(exc).splitlines()[0][:300]}{_failure_hint(settings, str(exc))}")
                continue
        else:
            cookie_args = ["--cookies-from-browser", _cookies(settings)] if _cookies(settings) else []
            proc = subprocess.run(["yt-dlp", "-f", fmt, "-o", template, "--no-playlist", "--download-archive", str(out_dir / ".downloaded.txt"), *cookie_args, url], capture_output=True, text=True)
            if proc.returncode != 0:
                failed.append(url)
                log(f"footage: download failed for {url}: {proc.stderr.strip()[-400:]}{_failure_hint(settings, proc.stderr)}")
                continue
        new = [p for p in set(out_dir.iterdir()) - before if p.suffix.lower() in VIDEO_EXT]
        who = {k: str(info.get(k)) for k in ("uploader", "channel", "channel_url", "extractor_key") if yt_dlp is not None and info.get(k)}
        for p in new:
            side = sidecar_path(p)
            if not side.exists():
                write_json(side, {"source_url": url, **who, "credit": str(settings.get("footage.credit") or DEFAULT_CREDIT), "tags": list(tags or [])})
            log(f"footage: added videos/{p.name}")
        done += new
        if not new:
            log("footage: already downloaded")
    if failed and len(failed) == len(urls):
        raise FactoryError(
            f"No video could be downloaded ({len(failed)} URL(s) failed).",
            hint="Check your internet connection and update yt-dlp (`pip install -U yt-dlp`). For age-restricted videos set footage.cookies_from_browser in config.yaml."
            " You can also download the files yourself and run `python -m gta_factory footage add <folder>`.",
        )
    return done


def find_new_official(settings: Any, *, limit: int = 60, log: Callable[[str], None] = print) -> list[dict[str, str]]:
    """New GTA VI videos on the official channels (``footage.channels``) that are not in the library yet."""
    yt_dlp = _yt_dlp()
    if yt_dlp is None:
        raise ConfigError("yt-dlp is not installed.", hint="Run `pip install -e \".[footage]\"` (or `pip install yt-dlp`), then retry.")
    rx = re.compile(str(settings.get("footage.title_filter") or DEFAULT_TITLE_FILTER), re.I)
    blocked = [str(w) for w in settings.get("rights_review.blocked_keywords", []) or []]
    vids = footage_dir(settings) / "videos"
    archive = (vids / ".downloaded.txt").read_text(encoding="utf-8") if (vids / ".downloaded.txt").exists() else ""
    have = set(re.findall(r"\S+\s+(\S+)", archive))
    if vids.exists():
        have |= {m for p in vids.iterdir() for m in re.findall(r"\[([A-Za-z0-9_-]{6,})\]", p.name)}
    found: list[dict[str, str]] = []
    for channel in settings.get("footage.channels") or DEFAULT_CHANNELS:
        log(f"footage: looking for new GTA VI videos on {channel}")
        try:
            with yt_dlp.YoutubeDL({"extract_flat": "in_playlist", "playlistend": limit, "quiet": True, "no_warnings": True, **({"cookiesfrombrowser": (_cookies(settings),)} if _cookies(settings) else {})}) as ydl:
                info = ydl.extract_info(channel, download=False) or {}
        except Exception as exc:
            log(f"footage: could not read {channel}: {str(exc).splitlines()[0][:200]}")
            continue
        for e in info.get("entries") or []:
            title, vid = str(e.get("title") or ""), str(e.get("id") or "")
            if not vid or vid in have or not rx.search(title) or _blocked(title, blocked):
                continue
            if any(f["id"] == vid for f in found):
                continue
            found.append({"id": vid, "title": title, "url": e.get("url") if str(e.get("url") or "").startswith("http") else f"https://www.youtube.com/watch?v={vid}"})
    return found


def _download_url(settings: Any, url: str, *, tags: list[str] | None, credit: str, log: Callable[[str], None]) -> list[Path]:
    """A direct image / video link is saved as is; anything else (YouTube, X, TikTok ...) goes through yt-dlp."""
    import httpx

    path_ext = Path(url.split("?", 1)[0].split("#", 1)[0]).suffix.lower()
    if path_ext in PHOTO_EXT | VIDEO_EXT:
        name = re.sub(r"[^\w.\- ]+", "_", Path(url.split("?", 1)[0]).name)[:120] or f"download{path_ext}"
        dest = footage_dir(settings) / ("videos" if path_ext in VIDEO_EXT else "photos") / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        log(f"footage: downloading {url}")
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as r:
                r.raise_for_status()
                tmp = dest.with_name(dest.name + ".part")
                with tmp.open("wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)
                tmp.rename(dest)
        except httpx.HTTPError as exc:
            raise FactoryError(f"Could not download {url}: {exc}") from exc
        write_json(sidecar_path(dest), {**_sidecar(dest), "source_url": url, "credit": credit or str(settings.get("footage.credit") or DEFAULT_CREDIT), **({"tags": tags} if tags else {})})
        log(f"footage: added {dest.relative_to(footage_dir(settings))}")
        return [dest]
    got = fetch(settings, [url], tags=tags, log=log)
    if credit:
        for p in got:
            write_json(sidecar_path(p), {**_sidecar(p), "credit": credit})
    return got


def add_files(settings: Any, paths: list[Any], *, tags: list[str] | None = None, credit: str = "", own: bool = False, third_party_ok: bool = False, log: Callable[[str], None] = print) -> list[Path]:
    """Add trailers / screenshots to the library: local files or folders (copied), or URLs (downloaded).

    ``own``: the operator's own gameplay captures. The game's HUD (minimap, health) stays at the same place
    and would look like a burned-in logo, so these files skip the re-upload check (``allow_overlay``).
    """
    root = footage_dir(settings)
    added: list[Path] = []
    for item in paths:
        if isinstance(item, str) and re.match(r"^https?://", item):
            got = _download_url(settings, item, tags=tags, credit=credit, log=log)
            for g in got:
                side = _sidecar(g)
                if third_party_ok:
                    write_json(sidecar_path(g), {**side, "allow_third_party": True})
                elif third_party(settings, side):
                    log(f"footage: {g.name} comes from a third party ({third_party(settings, side)}): it stays in the folder but is NOT used."
                        " Re-uploading other people's posts risks claims and a 'reused content' strike. Add `--third-party-ok` only if you have the right to use it.")
            added += got
            continue
        p = Path(item).expanduser()
        if not p.exists():
            log(f"footage: not found: {p}")
            continue
        files = [p] if p.is_file() else sorted(f for f in p.rglob("*") if f.is_file())
        for f in files:
            ext = f.suffix.lower()
            if ext not in VIDEO_EXT and ext not in PHOTO_EXT:
                continue
            dest = root / ("videos" if ext in VIDEO_EXT else "photos") / f.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.resolve() != f.resolve():
                shutil.copyfile(f, dest)
                if sidecar_path(f).exists() and not sidecar_path(dest).exists():
                    shutil.copyfile(sidecar_path(f), sidecar_path(dest))
            if tags or credit or own:
                write_json(sidecar_path(dest), {**_sidecar(dest), **({"tags": sorted(set(_sidecar(dest).get("tags") or []) | set(tags or []))} if tags else {}), **({"credit": credit} if credit else {}), **({"own_capture": True, "allow_overlay": True} if own else {})})
            added.append(dest)
            log(f"footage: added {dest.relative_to(root)}")
    return added


def resolve_file(settings: Any, name: str) -> Path:
    """A library file from its relative path, its file name, or a unique part of its name."""
    root = footage_dir(settings)
    direct = (root / name).resolve()
    if direct.is_file() and str(direct).startswith(str(root.resolve()) + "/"):
        return direct
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXT | PHOTO_EXT and not any(x.startswith(".") for x in p.relative_to(root).parts)]
    exact = [p for p in files if p.name == name]
    if len(exact) == 1:
        return exact[0]
    low = name.lower()
    some = [p for p in files if low in p.name.lower()]
    if len(some) == 1:
        return some[0]
    if not some:
        raise FactoryError(f"No file matching '{name}' in the footage library.", hint="See `python -m gta_factory footage list`.")
    raise FactoryError(f"'{name}' matches {len(some)} files: " + ", ".join(p.name for p in some[:6]), hint="Give more of the name, or the path shown by `footage list`.")


def set_tags(settings: Any, path: Path, *, add: list[str] | None = None, remove: list[str] | None = None, replace: list[str] | None = None, at: tuple[float, float] | None = None) -> dict[str, Any]:
    """Edit the tags of a file (or of a time range of a video) in its sidecar. Returns the new sidecar."""
    vocab = load_vocabulary(settings)
    side = _sidecar(path)
    add_c = canonical_tags(add or [], vocab)
    rem_c = set(canonical_tags(remove or [], vocab))
    if at is not None:
        start, end = float(at[0]), float(at[1])
        if end <= start:
            raise FactoryError("--at needs START-END in seconds, with END after START (e.g. 12-19.5).")
        segs = [s for s in side.get("segments") or [] if isinstance(s, dict)]
        seg = next((s for s in segs if abs(float(s.get("start", -1)) - start) < 0.05 and abs(float(s.get("end", -1)) - end) < 0.05), None)
        if seg is None:
            seg = {"start": start, "end": end, "tags": []}
            segs.append(seg)
        base = canonical_tags(replace, vocab) if replace is not None else canonical_tags(seg.get("tags"), vocab)
        seg["tags"] = sorted((set(base) | set(add_c)) - rem_c)
        side["segments"] = sorted((s for s in segs if s.get("tags")), key=lambda s: float(s.get("start", 0)))
    else:
        base = canonical_tags(replace, vocab) if replace is not None else canonical_tags(side.get("tags"), vocab)
        side["tags"] = sorted((set(base) | set(add_c)) - rem_c)
    write_json(sidecar_path(path), side)
    return side


def remove_file(settings: Any, path: Path) -> None:
    root = footage_dir(settings).resolve()
    if not str(path.resolve()).startswith(str(root) + "/"):
        raise FactoryError("Only files inside the footage library can be removed.")
    path.unlink(missing_ok=True)
    sidecar_path(path).unlink(missing_ok=True)


def extract_stills(settings: Any, *, per_video: int = 12, log: Callable[[str], None] = print) -> list[Path]:
    """Pull sharp stills from the brightest usable trailer shots into ``photos/`` (for photo scenes and thumbnails)."""
    index = build_index(settings, log=log)
    out_dir = footage_dir(settings) / "photos"
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for v in index["videos"]:
        shots = sorted((s for s in v["shots"] if s.get("ok") and s["e"] - s["s"] >= 1.5), key=lambda s: -s["luma"])[: per_video * 2]
        shots = sorted(shots, key=lambda s: s["s"])
        step = max(1, len(shots) // max(1, per_video))
        for sh in shots[::step][:per_video]:
            t = round((sh["s"] + sh["e"]) / 2, 2)
            dest = out_dir / f"{v['id']}_still_{int(t * 100):06d}.jpg"
            if dest.exists():
                continue
            proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", str(footage_dir(settings) / v["file"]), "-frames:v", "1", *(["-vf", crop_filter(sh.get("crop")).rstrip(",")] if sh.get("crop") else []), "-q:v", "2", str(dest)], capture_output=True, text=True)
            if proc.returncode == 0 and dest.exists():
                write_json(sidecar_path(dest), {"tags": sorted(sh.get("tags", [])), "video_tags": sorted(v.get("tags", [])), "credit": v.get("credit") or DEFAULT_CREDIT, "source_url": v.get("source_url", ""), "from_video": v["file"], "time": t})
                made.append(dest)
    log(f"footage: {len(made)} still(s) extracted into {out_dir}")
    return made
