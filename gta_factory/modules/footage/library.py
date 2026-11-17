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
     "exclude": [[0, 4.5], [151, 165]]}

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
        if ext in PHOTO_EXT:
            photo = {"id": p.stem, "file": rel, "size": st.st_size, "tags": tags, "credit": credit, "source_url": side.get("source_url", "")}
            if side.get("from_video"):  # a still pulled from a trailer: same picture as that shot
                photo.update(from_video=str(side["from_video"]), time=float(side.get("time") or 0), video_tags=canonical_tags(side.get("video_tags"), vocab))
            photos.append(photo)
            continue
        prev = old_videos.get(rel)
        if prev and not force and prev.get("size") == st.st_size and prev.get("mtime") == int(st.st_mtime) and prev.get("sidecar") == side and prev.get("tags") == tags:
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
        seg_tags = [seg for seg in side.get("segments") or [] if isinstance(seg, dict)]
        exclude = [x for x in side.get("exclude") or [] if isinstance(x, list) and len(x) == 2]
        for sh in shots:
            sh["ok"] = _usable(sh, info["duration"], cfg, exclude)
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
            "shots": shots,
        })
        if not same_file:
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


def load_items(settings: Any, index: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every usable shot and photo with its weighted tags, plus the vocabulary that knows all of them.

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

    shots, photos = [], []
    for v in index.get("videos", []):
        for sh in v.get("shots", []):
            if not sh.get("ok"):
                continue
            tags: dict[str, float] = {}
            add(tags, v.get("tags"), W_VIDEO)
            add_auto(tags, shot_key(v["file"], v.get("size", 0), sh))
            add(tags, sh.get("tags"), W_ITEM)
            shots.append({"kind": "shot", "video": v, "file": v["file"], "path": str(root / v["file"]), "s": sh["s"], "e": sh["e"], "vdur": v.get("duration", sh["e"]), "luma": sh.get("luma", 0), "tags": tags, "key": f"{v['file']}@{sh['s']}"})
    for p in index.get("photos", []):
        tags = {}
        add(tags, p.get("video_tags"), W_VIDEO)
        own = photo_key(p["file"], p.get("size", 0))
        add_auto(tags, own if own in auto else (still_shot_key(index, p) or own))
        add(tags, p.get("tags"), W_ITEM)
        photos.append({"kind": "photo", **p, "path": str(root / p["file"]), "tags": tags, "key": p["file"]})
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
            side = sidecar_path(p)
            if not side.exists():
                write_json(side, {"source_url": url, "credit": str(settings.get("footage.credit") or DEFAULT_CREDIT), "tags": list(tags or [])})
            log(f"footage: added videos/{p.name}")
        done += new
        if not new:
            log("footage: already downloaded")
    if failed and len(failed) == len(urls):
        raise FactoryError(
            f"No video could be downloaded ({len(failed)} URL(s) failed).",
            hint="Check your internet connection and update yt-dlp (`pip install -U yt-dlp`). You can also download the files yourself and run `python -m gta_factory footage add <folder>`.",
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
            with yt_dlp.YoutubeDL({"extract_flat": "in_playlist", "playlistend": limit, "quiet": True, "no_warnings": True}) as ydl:
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


def add_files(settings: Any, paths: list[Any], *, tags: list[str] | None = None, credit: str = "", log: Callable[[str], None] = print) -> list[Path]:
    """Add trailers / screenshots to the library: local files or folders (copied), or URLs (downloaded)."""
    root = footage_dir(settings)
    added: list[Path] = []
    for item in paths:
        if isinstance(item, str) and re.match(r"^https?://", item):
            added += _download_url(settings, item, tags=tags, credit=credit, log=log)
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
            if tags or credit:
                write_json(sidecar_path(dest), {**_sidecar(dest), **({"tags": sorted(set(_sidecar(dest).get("tags") or []) | set(tags or []))} if tags else {}), **({"credit": credit} if credit else {})})
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
            proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", str(footage_dir(settings) / v["file"]), "-frames:v", "1", "-q:v", "2", str(dest)], capture_output=True, text=True)
            if proc.returncode == 0 and dest.exists():
                write_json(sidecar_path(dest), {"tags": sorted(sh.get("tags", [])), "video_tags": sorted(v.get("tags", [])), "credit": v.get("credit") or DEFAULT_CREDIT, "source_url": v.get("source_url", ""), "from_video": v["file"], "time": t})
                made.append(dest)
    log(f"footage: {len(made)} still(s) extracted into {out_dir}")
    return made
