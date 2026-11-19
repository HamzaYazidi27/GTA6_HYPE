"""Reference analyzer: measure a channel's editing language from its public videos.

``python -m gta_factory style analyze https://www.youtube.com/@Tacticalpath --videos 8``

1. Lists the channel's Shorts (yt-dlp, metadata only), downloads up to ``--videos`` of them at 720p
   into ``assets/reference_videos/`` for analysis only: they never enter the footage library and
   are never shown in a video.
2. Measures each video with ffmpeg, numpy and Pillow (and faster-whisper when installed):
   cuts and shot lengths, zoom and punch-ins (frame-to-frame scale estimate), flashes and dissolves,
   burned-in captions (position, height, words on screen, change rate, highlight colour), loudness,
   speech rate, music level under the voice, sound effects on cuts, colour (saturation, contrast,
   brightness, sharpness), and the story beats (hook length, numbers, call to action).
3. Writes ``style/style_profile.json`` (measured values, per video and pooled, plus the editor
   settings derived from them) and ``style/style_reference_report.md`` (URLs, timestamps, values).

Nothing is invented: a measurement that could not be made is ``null`` with the reason, and when no
video can be read the profile keeps ``status: "not_measured"``.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .style import REFERENCE_CHANNEL, default_profile, derive_editor, profile_path

AN_W = 320  # analysis width
NUMBER_RE = re.compile(r"^(one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})[:.,!]?$", re.I)


# ----------------------------------------------------------------------------- getting the videos

def list_videos(channel: str, limit: int, *, log: Callable[[str], None] = print, errors: list[str] | None = None) -> list[dict[str, Any]]:
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:
        raise RuntimeError("yt-dlp is needed: pip install -e \".[footage]\"") from exc
    url = channel.rstrip("/")
    feeds = [url + "/shorts", url + "/videos"] if "/watch" not in url and "/shorts/" not in url else [url]
    out: list[dict[str, Any]] = []
    for feed in feeds:
        try:
            with yt_dlp.YoutubeDL({"extract_flat": "in_playlist", "playlistend": limit * 2, "quiet": True, "no_warnings": True}) as ydl:
                info = ydl.extract_info(feed, download=False) or {}
        except Exception as exc:
            msg = str(exc).splitlines()[0][:220]
            log(f"style: could not list {feed}: {msg}")
            if errors is not None:
                errors.append(f"{feed}: {msg}")
            continue
        entries = info.get("entries") or ([info] if info.get("id") else [])
        for e in entries:
            vid = e.get("id")
            if vid and all(v["id"] != vid for v in out):
                out.append({"id": vid, "title": e.get("title") or "", "url": f"https://www.youtube.com/shorts/{vid}" if "shorts" in feed else f"https://www.youtube.com/watch?v={vid}", "duration": e.get("duration")})
        if len(out) >= limit:
            break
    return out[:limit]


def download(video: dict[str, Any], folder: Path, *, log: Callable[[str], None] = print) -> Path | None:
    import yt_dlp  # type: ignore

    folder.mkdir(parents=True, exist_ok=True)
    existing = list(folder.glob(f"{video['id']}.*"))
    if existing:
        return existing[0]
    opts = {"format": "bv*[height<=1280][ext=mp4]+ba[ext=m4a]/b[height<=1280]/b", "outtmpl": str(folder / "%(id)s.%(ext)s"), "quiet": True, "no_warnings": True, "merge_output_format": "mp4"}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([video["url"]])
    except Exception as exc:
        log(f"style: could not download {video['url']}: {str(exc).splitlines()[0][:160]}")
        return None
    got = list(folder.glob(f"{video['id']}.*"))
    return got[0] if got else None


# ----------------------------------------------------------------------------- measuring one video

def _probe(path: Path) -> dict[str, Any]:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,avg_frame_rate:format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    data = json.loads(proc.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    num, den = (v.get("avg_frame_rate") or "0/1").split("/")
    return {"width": int(v.get("width") or 0), "height": int(v.get("height") or 0), "fps": round(float(num) / float(den or 1), 2) if float(den or 1) else 0.0,
            "duration": float((data.get("format") or {}).get("duration") or 0.0), "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


def frames(path: Path, fps: float, width: int = AN_W, gray: bool = False) -> tuple[np.ndarray, list[float]]:
    pix = "gray" if gray else "rgb24"
    ch = 1 if gray else 3
    info = _probe(path)
    h = int(round(width * info["height"] / max(1, info["width"]) / 2)) * 2
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf", f"fps={fps},scale={width}:{h}", "-f", "rawvideo", "-pix_fmt", pix, "pipe:1"], capture_output=True)
    n = len(proc.stdout) // (width * h * ch)
    arr = np.frombuffer(proc.stdout[: n * width * h * ch], dtype=np.uint8).reshape((n, h, width) if gray else (n, h, width, ch))
    return arr, [(i + 0.5) / fps for i in range(n)]


def cuts(path: Path, threshold: float = 0.3, min_gap: float = 0.2) -> list[float]:
    """Cut times; detections closer than ``min_gap`` (one busy transition, a flash) count as one cut."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"], capture_output=True, text=True)
    out: list[float] = []
    for t in sorted({round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)}):
        if not out or t - out[-1] >= min_gap:
            out.append(t)
    return out


ZOOM_SCALES = tuple(round(0.9 + 0.01 * k, 2) for k in range(21))  # 0.90 ... 1.10


def scale_change(a: np.ndarray, b: np.ndarray, scales: tuple[float, ...] = ZOOM_SCALES) -> tuple[float, float, float]:
    """(best scale from a to b, its error, error with no zoom): b ~ a zoomed by ``scale`` about the centre.

    Gray frames; compared on the upper-middle of the picture (captions sit lower and do not zoom).
    """
    from PIL import Image, ImageFilter

    h, w = a.shape
    y0, y1, x0, x1 = int(h * 0.12), int(h * 0.55), int(w * 0.15), int(w * 0.85)
    target = np.asarray(Image.fromarray(b).filter(ImageFilter.GaussianBlur(1)), dtype=np.float32)[y0:y1, x0:x1]
    img = Image.fromarray(a).filter(ImageFilter.GaussianBlur(1))
    cx, cy = w / 2, h / 2
    best, err, ident = 1.0, float("inf"), float("inf")
    for sc in scales:
        z = img.transform((w, h), Image.AFFINE, (1 / sc, 0, cx - cx / sc, 0, 1 / sc, cy - cy / sc), resample=Image.BILINEAR)
        e = float(np.mean((np.asarray(z, dtype=np.float32)[y0:y1, x0:x1] - target) ** 2))
        if sc == 1.0:
            ident = e
        if e < err:
            best, err = sc, e
    return best, err, ident


def caption_stats(rgb: np.ndarray) -> dict[str, Any] | None:
    """Burned-in caption band of one frame: bright or coloured strokes with a dark outline, in a horizontal band."""
    f = rgb.astype(np.int16)
    luma = (0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2])
    mx, mn = f.max(axis=2), f.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1)
    bright = (luma > 185) | ((sat > 0.55) & (mx > 170))
    dark = luma < 50

    def near(axis: int, sign: int) -> np.ndarray:
        out = np.zeros_like(dark)
        for k in range(1, 5):
            out |= np.roll(dark, sign * k, axis=axis)
        return out

    # outlined text: a thin bright stroke with the dark outline on BOTH sides (a bright background
    # next to the outline has it on one side only)
    mask = bright & ((near(1, 1) & near(1, -1)) | (near(0, 1) & near(0, -1)))
    h, w = mask.shape
    rows = mask.sum(axis=1) / w
    band = rows > 0.04
    if band.sum() < max(3, h * 0.015):
        return None
    ys = np.nonzero(band)[0]
    # the densest contiguous run of text rows
    runs, start = [], ys[0]
    for a, b in zip(ys, ys[1:]):
        if b - a > max(2, h * 0.02):
            runs.append((start, a))
            start = b
    runs.append((start, ys[-1]))
    y0, y1 = max(runs, key=lambda r: rows[r[0] : r[1] + 1].sum())
    if y1 - y0 < h * 0.015:
        return None
    band_mask = mask[y0 : y1 + 1]
    band_rgb = f[y0 : y1 + 1]
    if band_mask.sum() > 20:  # the fill colour of the text: drops bright background caught between two outlines
        med = np.median(band_rgb[band_mask], axis=0)
        band_mask = band_mask & (np.abs(band_rgb - med).sum(axis=2) < 90)
    cols = band_mask.any(axis=0)
    # blank column runs between strokes: letters are a few px apart, words about a third of the text height
    xs = np.nonzero(cols)[0]
    gaps = np.diff(xs) - 1 if len(xs) > 1 else np.zeros(0, int)
    # blank runs between letters (their outlines) are under half the cap height; a word space is wider
    word_gap = max(3.0, 0.35 * (y1 - y0))
    words = 1 + int(np.sum(gaps >= word_gap)) if len(xs) else 0
    colored = float(((sat > 0.5) & mask)[y0 : y1 + 1].sum() / max(1, mask[y0 : y1 + 1].sum()))
    return {"center_y": round((y0 + y1) / 2 / h, 3), "height_frac": round((y1 - y0) / h, 3), "words": words, "colored_share": round(colored, 3), "mask_rows": (int(y0), int(y1))}


def audio_stats(path: Path, cut_times: list[float], words: list[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"I:\s*(-?[\d.]+) LUFS", proc.stderr)
    out["integrated_lufs"] = float(m.group(1)) if m else None
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"], capture_output=True).stdout
    if not raw:
        return out
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    hop = 160  # 10 ms
    n = len(x) // hop
    frames_ = x[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames_ ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    # onsets (sound effects, beats): spectral flux on 32 ms windows
    win = 512
    hopf = 160
    nf = max(0, (len(x) - win) // hopf)
    if nf > 10:
        spec = np.abs(np.fft.rfft(np.stack([x[i * hopf : i * hopf + win] * np.hanning(win) for i in range(nf)]), axis=1))
        flux = np.maximum(0, np.diff(spec, axis=0)).sum(axis=1)
        thr = np.median(flux) + 2.5 * np.std(flux)
        peaks = [i for i in range(1, len(flux) - 1) if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]]
        onset_t = np.array(peaks) * hopf / 16000
        if cut_times:
            hits = sum(1 for c in cut_times if len(onset_t) and np.min(np.abs(onset_t - c)) <= 0.12)
            out["sfx_on_cut_share"] = round(hits / len(cut_times), 3)
        out["onsets_per_min"] = round(len(peaks) / max(1e-6, len(x) / 16000 / 60), 1)
    if words:
        speech = np.zeros(n, dtype=bool)
        for w in words:
            speech[int(w["s"] * 100) : int(w["e"] * 100) + 1] = True
        gaps = ~speech
        if speech.any() and gaps.sum() > 50:
            out["music_db_below_voice"] = round(float(np.median(db[speech]) - np.median(db[gaps])), 1)
        dur_min = (words[-1]["e"] - words[0]["s"]) / 60 if len(words) > 1 else 0
        out["speech_wpm"] = round(len(words) / dur_min, 1) if dur_min else None
        long_gaps = [b["s"] - a["e"] for a, b in zip(words, words[1:]) if b["s"] - a["e"] > 0.35]
        out["silence_gaps_per_min"] = round(len(long_gaps) / max(1e-6, len(x) / 16000 / 60), 2)
    return out


def transcribe(path: Path) -> list[dict[str, Any]] | None:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return None
    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
    return [{"w": w.word.strip(), "s": float(w.start), "e": float(w.end)} for s in segs for w in (s.words or [])]


def analyze_video(path: Path, *, url: str = "", log: Callable[[str], None] = print) -> dict[str, Any]:
    info = _probe(path)
    dur = info["duration"]
    res: dict[str, Any] = {"url": url, "file": path.name, **info, "notes": []}
    # pacing
    ct = [c for c in cuts(path) if 0.05 < c < dur - 0.05]
    bounds = [0.0] + ct + [dur]
    shots = [b - a for a, b in zip(bounds, bounds[1:]) if b - a > 0.04]
    res["cuts"] = ct
    res["pacing"] = {
        "shots": len(shots), "shot_mean_s": round(float(np.mean(shots)), 3) if shots else None, "shot_median_s": round(float(np.median(shots)), 3) if shots else None,
        "shot_p10_s": round(float(np.percentile(shots, 10)), 3) if shots else None, "shot_p90_s": round(float(np.percentile(shots, 90)), 3) if shots else None,
        "cuts_per_min": round(len(ct) / max(1e-6, dur / 60), 1), "first_cut_s": ct[0] if ct else None,
    }
    # camera: scale change between frames at 10 fps, inside shots
    g, gt = frames(path, 10, width=160, gray=True)
    rates, punches, flashes = [], 0, 0
    lum = g.reshape(len(g), -1).mean(axis=1) if len(g) else np.zeros(0)
    shot_of = np.searchsorted(np.array(ct), np.array(gt), side="right") if len(gt) else np.zeros(0, int)
    lag = 5  # frames compared half a second apart: a slow push-in is ~1-3 % per half second
    for i in range(lag, len(g), 2):
        if shot_of[i] != shot_of[i - lag]:
            continue
        sc, err, ident = scale_change(g[i - lag], g[i])
        if sc != 1.0 and err < 0.6 * ident:  # the picture is the same, scaled: a zoom
            rates.append(np.log(sc) * 10 / lag)
        elif ident < 150:  # the same picture, not scaled: a static or panning-free hold
            rates.append(0.0)
        # else: action or camera move, not a zoom measurement
    for c in ct:  # a punch-in: the same picture, suddenly bigger, right at a "cut"; a flash: a white frame
        i = int(round(c * 10 - 0.5))
        if 1 <= i < len(g) - 1:
            sc, err, ident = scale_change(g[i - 1], g[i + 1])
            if sc >= 1.04 and err < 0.4 * ident:
                punches += 1
            if lum[i] - min(lum[i - 1], lum[i + 1]) > 60:
                flashes += 1
    rates_arr = np.array(rates) if rates else np.zeros(0)
    zooming = np.abs(rates_arr) > 0.01  # more than 1 % per second
    events = int(np.sum(zooming[1:] & ~zooming[:-1])) if len(zooming) > 1 else 0
    res["camera"] = {"zoom_events_per_min": round(events / max(1e-6, dur / 60), 2), "mean_zoom_rate_per_s": round(float(np.mean(np.abs(rates_arr[zooming]))), 4) if zooming.any() else 0.0,
                     "zooming_share": round(float(zooming.mean()), 3) if len(zooming) else 0.0, "punch_in_share_of_cuts": round(punches / max(1, len(ct)), 3)}
    res["transitions"] = {"flash_share": round(flashes / max(1, len(ct)), 3), "hard_cut_share": round(max(0.0, 1 - flashes / max(1, len(ct))), 3)}
    # captions and colour, 2 frames per second
    rgb, rt = frames(path, 2, width=AN_W)
    caps = [caption_stats(f) for f in rgb]
    present = [c for c in caps if c]
    changes = 0
    prev = None
    for c in caps:
        sig = (c["words"], round(c["center_y"] * 50)) if c else None
        if sig != prev:
            changes += 1
        prev = sig
    res["captions"] = {
        "present_share": round(len(present) / max(1, len(caps)), 3),
        "center_y": round(float(np.median([c["center_y"] for c in present])), 3) if present else None,
        "height_frac": round(float(np.median([c["height_frac"] for c in present])), 3) if present else None,
        "words_on_screen_median": float(np.median([c["words"] for c in present])) if present else None,
        "colored_text_share": round(float(np.mean([c["colored_share"] for c in present])), 3) if present else None,
        "changes_per_s": round(changes / max(1e-6, dur), 2),
    }
    if len(rgb):
        f = rgb.astype(np.float32)
        mx, mn = f.max(axis=3), f.min(axis=3)
        sat = (mx - mn) / np.maximum(mx, 1)
        luma = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
        lap = np.abs(4 * luma[:, 1:-1, 1:-1] - luma[:, :-2, 1:-1] - luma[:, 2:, 1:-1] - luma[:, 1:-1, :-2] - luma[:, 1:-1, 2:])
        res["color"] = {"saturation_mean": round(float(sat.mean()), 3), "contrast_std": round(float(luma.std(axis=(1, 2)).mean()), 1), "brightness_mean": round(float(luma.mean()), 1), "sharpness": round(float(lap.var()), 1)}
    # speech, audio, story
    try:
        words = transcribe(path) if info["has_audio"] else None
    except Exception as exc:  # model download blocked, broken audio ...: the rest of the measurements stand
        words = None
        res["notes"].append(f"transcription failed ({type(exc).__name__}: {str(exc)[:120]})")
    if words is None:
        res["notes"].append("no word timings (faster-whisper not installed or no audio): speech rate, music level and story timing not measured")
    res["audio"] = audio_stats(path, ct, words) if info["has_audio"] else {}
    story: dict[str, Any] = {"duration_s": round(dur, 2)}
    if words:
        nums = [w for w in words if NUMBER_RE.match(w["w"].strip().lower())]
        story["numbers_at"] = [round(w["s"], 2) for w in nums]
        story["hook_seconds"] = round(nums[0]["s"], 2) if nums else round(words[min(len(words) - 1, 12)]["e"], 2)
        story["hook_words"] = sum(1 for w in words if w["e"] <= story["hook_seconds"])
        if len(nums) > 1:
            story["number_interval_s"] = round(float(np.mean(np.diff([w["s"] for w in nums]))), 2)
        story["cut_on_number_share"] = round(sum(1 for w in nums if any(abs(c - w["s"]) <= 0.35 for c in ct)) / max(1, len(nums)), 3) if nums else None
        ends = [i for i, w in enumerate(words) if w["w"].rstrip().endswith(("?", "."))]
        last_start = words[ends[-2] + 1]["s"] if len(ends) >= 2 else words[0]["s"]
        story["cta_seconds"] = round(dur - last_start, 2)
        story["transcript"] = " ".join(w["w"] for w in words)[:600]
    res["story"] = story
    log(f"style: {path.name}: {len(ct)} cuts, shot median {res['pacing']['shot_median_s']}s, captions on {res['captions']['present_share']:.0%} of frames")
    return res


# ----------------------------------------------------------------------------- pooling and writing

def _pool(values: list[Any]) -> float | None:
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    return round(float(np.median(vals)), 4) if vals else None


def pool(videos: list[dict[str, Any]]) -> dict[str, Any]:
    groups = ("pacing", "camera", "transitions", "captions", "audio", "color", "story")
    out: dict[str, Any] = {}
    for g in groups:
        keys = {k for v in videos for k, val in (v.get(g) or {}).items() if isinstance(val, (int, float))}
        out[g] = {k: _pool([(v.get(g) or {}).get(k) for v in videos]) for k in sorted(keys)}
    return out


def analyze_channel(settings: Any, channel: str = REFERENCE_CHANNEL, *, videos: int = 8, files: list[Path] | None = None, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Measure the reference and write the profile and the report. ``files``: already downloaded videos."""
    folder = settings.root / "assets" / "reference_videos"
    targets: list[tuple[dict[str, Any], Path]] = []
    status_reason = ""
    if files:
        targets = [({"id": p.stem, "url": str(p), "title": p.stem}, p) for p in files]
    else:
        errors: list[str] = []
        try:
            listed = list_videos(channel, videos, log=log, errors=errors)
        except RuntimeError as exc:
            listed, status_reason = [], str(exc)
        for v in listed:
            p = download(v, folder, log=log)
            if p:
                targets.append((v, p))
        if not listed and not status_reason:
            status_reason = f"no video could be listed from {channel}" + (f" - {errors[0]}" if errors else " (offline, blocked, or the channel has no public videos)")
    measured = []
    for v, p in targets:
        try:
            m = analyze_video(p, url=v.get("url", ""), log=log)
            m["title"] = v.get("title", "")
            measured.append(m)
        except Exception as exc:
            log(f"style: {p.name}: analysis failed ({type(exc).__name__}: {exc})")
    if not measured:
        prof = default_profile(status_reason or "no reference video could be downloaded or analysed")
        prof["analysis_attempted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_profile(settings, prof, [])
        return prof
    pooled = pool(measured)
    # colour relative to this channel's own footage, so the grade moves our footage toward the reference
    own = own_footage_color(settings)
    if own and pooled.get("color", {}).get("saturation_mean"):
        pooled["color"]["saturation_ratio"] = round(pooled["color"]["saturation_mean"] / max(1e-6, own["saturation_mean"]), 3)
        pooled["color"]["contrast_ratio"] = round(pooled["color"]["contrast_std"] / max(1e-6, own["contrast_std"]), 3)
    prof = {
        "status": "measured",
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reference": {"channel": channel, "videos": [{"url": m["url"], "title": m.get("title", ""), "duration_s": round(m["duration"], 2), "fps": m["fps"], "size": f"{m['width']}x{m['height']}"} for m in measured]},
        "measurements": pooled,
        "per_video": measured,
        "editor": derive_editor(pooled),
    }
    write_profile(settings, prof, measured)
    return prof


def own_footage_color(settings: Any) -> dict[str, float] | None:
    """Mean saturation / contrast of a sample of this channel's GTA 6 clips (the grade's starting point)."""
    from ..footage.library import build_index, footage_dir

    idx = build_index(settings)
    vids = [footage_dir(settings) / v["file"] for v in idx.get("videos", [])][:6]
    sats, cons = [], []
    for p in vids:
        rgb, _ = frames(p, 0.5)
        if not len(rgb):
            continue
        f = rgb.astype(np.float32)
        mx, mn = f.max(axis=3), f.min(axis=3)
        sats.append(float(((mx - mn) / np.maximum(mx, 1)).mean()))
        cons.append(float((0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]).std(axis=(1, 2)).mean()))
    return {"saturation_mean": float(np.mean(sats)), "contrast_std": float(np.mean(cons))} if sats else None


def write_profile(settings: Any, prof: dict[str, Any], measured: list[dict[str, Any]]) -> None:
    p = profile_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prof, indent=2, ensure_ascii=False), encoding="utf-8")
    (p.parent / "style_reference_report.md").write_text(render_report(prof, measured), encoding="utf-8")


def render_report(prof: dict[str, Any], measured: list[dict[str, Any]]) -> str:
    ref = prof.get("reference") or {}
    out = [f"# Reference style report: {ref.get('channel', '')}", ""]
    if prof.get("status") != "measured":
        out += ["**Status: NOT MEASURED.** " + str(prof.get("reason") or ""), "",
                "No value in `style_profile.json` comes from the reference yet: the editor uses the channel's own defaults.",
                f"Run `{prof.get('how_to_measure', 'python -m gta_factory style analyze <channel>')}` on a machine with internet access and yt-dlp.", ""]
        return "\n".join(out) + "\n"
    out += [f"Measured {prof.get('measured_at')} on {len(measured)} video(s). Values are medians over the videos.", ""]
    m = prof.get("measurements") or {}
    for group, vals in m.items():
        out.append(f"## {group}")
        out += [f"- `{k}`: {v}" for k, v in vals.items()] or ["- (nothing measured)"]
        out.append("")
    out += ["## Editor settings derived from the measurements", "", "```json", json.dumps(prof.get("editor"), indent=2), "```", "", "## Per video", ""]
    for v in measured:
        out += [f"### [{v.get('title') or v['file']}]({v['url']})", f"- {v['duration']:.1f}s, {v['width']}x{v['height']} @ {v['fps']} fps",
                f"- cuts at: " + ", ".join(f"{c:.2f}s" for c in v["cuts"][:40]) + (" …" if len(v["cuts"]) > 40 else ""),
                f"- shots: median {v['pacing']['shot_median_s']}s, mean {v['pacing']['shot_mean_s']}s, {v['pacing']['cuts_per_min']} cuts/min, first cut {v['pacing']['first_cut_s']}s",
                f"- camera: {v['camera']}", f"- transitions: {v['transitions']}", f"- captions: {v['captions']}", f"- audio: {v.get('audio')}", f"- colour: {v.get('color')}", f"- story: { {k: s for k, s in v['story'].items() if k != 'transcript'} }"]
        if v["story"].get("transcript"):
            out.append(f"- transcript (start): “{v['story']['transcript'][:240]}…”")
        out += [f"- note: {n}" for n in v.get("notes") or []]
        out.append("")
    return "\n".join(out) + "\n"
