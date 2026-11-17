"""The real GTA 6 footage library: official trailers (video) and official screenshots (photos).

Layout (``footage.dir``, default ``assets/gta6_footage``)::

    videos/   official trailers (mp4/mov/webm/mkv), e.g. downloaded with `footage fetch`
    photos/   official screenshots (jpg/png/webp), or stills pulled from the trailers
    index.json  built automatically: duration, resolution and the list of usable shots

Optional sidecar next to any file, ``<file>.json``::

    {"tags": ["lucia", "jason", "vice city", "beach"],
     "credit": "Footage: Rockstar Games",
     "segments": [{"start": 12.0, "end": 19.5, "tags": ["car", "police"]}],
     "exclude": [[0, 4.5], [151, 165]]}

Tags let the selector match footage to what the narration is talking about
(file names count as tags too: ``lucia_beach_01.jpg`` -> lucia, beach).

Shots are found with ffmpeg's scene-change detector. Dark shots (title cards,
fades, black frames), the first seconds (logos) and the last seconds (end card,
rating screen) are left out automatically.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from ...app.core.errors import ConfigError, FactoryError
from ...app.core.utils import read_json, tokens, which, write_json

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
INDEX_VERSION = 2
DEFAULT_CREDIT = "Footage: Rockstar Games"

# official Rockstar Games uploads (edit footage.sources in config.yaml to add newer trailers)
DEFAULT_SOURCES = [
    "https://www.youtube.com/watch?v=QdBZY2fkU-0",  # Grand Theft Auto VI Trailer 1 (Dec 2023)
    "https://www.youtube.com/watch?v=VQRLujxTm3c",  # Grand Theft Auto VI Trailer 2 (May 2025)
]


def footage_dir(settings: Any) -> Path:
    d = Path(str(settings.get("footage.dir", "assets/gta6_footage")))
    return d if d.is_absolute() else settings.root / d


def _sidecar(path: Path) -> dict[str, Any]:
    side = path.with_name(path.name + ".json")
    if not side.exists():
        return {}
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def name_tags(path: Path) -> list[str]:
    """``Lucia_beach-01 [QdBZY2fkU-0].jpg`` -> ['lucia', 'beach']"""
    stem = re.sub(r"\[[^\]]*\]", " ", path.stem)
    return [t for t in tokens(stem.replace("_", " ").replace("-", " ")) if not t.isdigit() and len(t) > 2 and t not in {"gta", "grand", "theft", "auto", "official", "trailer", "rockstar", "games"}]


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
    index_path = root / "index.json"
    old = read_json(index_path, {}) or {}
    if old.get("version") != INDEX_VERSION:
        old = {}
    old_videos = {v["file"]: v for v in old.get("videos", [])}
    videos, photos, skipped = [], [], []
    if not root.exists():
        return {"version": INDEX_VERSION, "videos": [], "photos": [], "skipped": []}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext not in VIDEO_EXT and ext not in PHOTO_EXT:
            continue
        rel = p.relative_to(root).as_posix()
        side = _sidecar(p)
        hit = _blocked(" ".join([p.name, " ".join(side.get("tags") or []), str(side.get("source_url") or "")]), blocked)
        if hit:
            skipped.append({"file": rel, "reason": f"blocked keyword '{hit}' (leaked or re-uploaded material is never used)"})
            continue
        tags = sorted(set(name_tags(p)) | {t for tag in side.get("tags") or [] for t in tokens(str(tag))})
        credit = str(side.get("credit") or cfg.get("credit") or DEFAULT_CREDIT)
        st = p.stat()
        if ext in PHOTO_EXT:
            photos.append({"id": p.stem, "file": rel, "tags": tags, "credit": credit, "source_url": side.get("source_url", "")})
            continue
        prev = old_videos.get(rel)
        if prev and not force and prev.get("size") == st.st_size and prev.get("mtime") == int(st.st_mtime) and prev.get("sidecar") == side:
            videos.append(prev)
            continue
        if not which("ffmpeg") or not which("ffprobe"):
            raise ConfigError("ffmpeg / ffprobe are needed to index the GTA 6 footage.", hint="macOS: `brew install ffmpeg`.")
        log(f"footage: indexing {p.name} ...")
        info = _probe(p)
        shots = detect_shots(p, info["duration"], threshold=float(cfg.get("scene_threshold", 0.3)))
        shots = split_long_shots(shots, float(cfg.get("max_clip_seconds") or settings.get("rights_review.max_seconds_per_official_clip", 6)))
        seg_tags = side.get("segments") or []
        exclude = [x for x in side.get("exclude") or [] if isinstance(x, list) and len(x) == 2]
        for sh in shots:
            sh["ok"] = _usable(sh, info["duration"], cfg, exclude)
            extra = {t for seg in seg_tags if isinstance(seg, dict) and float(seg.get("start", 0)) < sh["e"] and float(seg.get("end", 0)) > sh["s"] for tag in seg.get("tags") or [] for t in tokens(str(tag))}
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
            "shots": shots,
        })
        log(f"footage: {p.name}: {info['duration']:.0f}s, {sum(1 for s in shots if s['ok'])}/{len(shots)} usable shots")
    index = {"version": INDEX_VERSION, "videos": videos, "photos": photos, "skipped": skipped}
    if index != old:
        write_json(index_path, index)
    return index


def library_summary(index: dict[str, Any]) -> dict[str, Any]:
    usable = [s for v in index.get("videos", []) for s in v.get("shots", []) if s.get("ok")]
    return {
        "videos": len(index.get("videos", [])),
        "photos": len(index.get("photos", [])),
        "usable_shots": len(usable),
        "usable_seconds": round(sum(s["e"] - s["s"] for s in usable), 1),
        "skipped": index.get("skipped", []),
    }


# ----------------------------------------------------------------------------- fetch / import

def fetch(settings: Any, urls: list[str] | None = None, *, max_height: int = 1080, log: Callable[[str], None] = print) -> list[Path]:
    """Download official trailers with yt-dlp into ``videos/`` (skips files already there)."""
    urls = urls or list(settings.get("footage.sources") or DEFAULT_SOURCES)
    blocked = [str(w) for w in settings.get("rights_review.blocked_keywords", []) or []]
    out_dir = footage_dir(settings) / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        yt_dlp = None
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
                log(f"footage: download failed for {url}: {str(exc).splitlines()[0][:300]}")
                continue
        else:
            proc = subprocess.run(["yt-dlp", "-f", fmt, "-o", template, "--no-playlist", "--download-archive", str(out_dir / ".downloaded.txt"), url], capture_output=True, text=True)
            if proc.returncode != 0:
                failed.append(url)
                log(f"footage: download failed for {url}: {proc.stderr.strip()[-400:]}")
                continue
        new = [p for p in set(out_dir.iterdir()) - before if p.suffix.lower() in VIDEO_EXT]
        for p in new:
            side = p.with_name(p.name + ".json")
            if not side.exists():
                write_json(side, {"source_url": url, "credit": str(settings.get("footage.credit") or DEFAULT_CREDIT), "tags": []})
        done += new
        if not new:
            log("footage: already downloaded")
    if failed and len(failed) == len(urls):
        raise FactoryError(
            f"No trailer could be downloaded ({len(failed)} URL(s) failed).",
            hint="Check your internet connection and update yt-dlp (`pip install -U yt-dlp`). You can also download the trailers yourself and run `python -m gta_factory footage add <folder>`.",
        )
    return done


def add_files(settings: Any, paths: list[Path], *, tags: list[str] | None = None, credit: str = "", log: Callable[[str], None] = print) -> list[Path]:
    """Copy local trailers / screenshots (files or folders) into the library."""
    root = footage_dir(settings)
    added = []
    files = [f for p in paths for f in ([p] if p.is_file() else sorted(p.rglob("*")))]
    for f in files:
        ext = f.suffix.lower()
        if ext not in VIDEO_EXT and ext not in PHOTO_EXT:
            continue
        dest = root / ("videos" if ext in VIDEO_EXT else "photos") / f.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.resolve() != f.resolve():
            shutil.copyfile(f, dest)
        if tags or credit:
            write_json(dest.with_name(dest.name + ".json"), {**_sidecar(dest), **({"tags": tags} if tags else {}), **({"credit": credit} if credit else {})})
        added.append(dest)
        log(f"footage: added {dest.relative_to(root)}")
    return added


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
            proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", str(footage_dir(settings) / v["file"]), "-frames:v", "1", "-q:v", "2", str(dest)], capture_output=True, text=True)
            if proc.returncode == 0 and dest.exists():
                write_json(dest.with_name(dest.name + ".json"), {"tags": sorted(set(v.get("tags", [])) | set(sh.get("tags", []))), "credit": v.get("credit") or DEFAULT_CREDIT, "source_url": v.get("source_url", ""), "from_video": v["file"], "time": t})
                made.append(dest)
    log(f"footage: {len(made)} still(s) extracted into {out_dir}")
    return made
