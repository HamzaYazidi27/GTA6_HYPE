"""The pictures and videos of an article: found on the page, sorted by who owns them, downloaded when allowed.

What an article page shows is often exactly what the video talks about (the save menu, a corner of
the map, a heist screen), so the factory keeps it for that video:

* ``find_media`` reads the page: the lead picture (OpenGraph), pictures inside the article
  (``<figure>``, ``srcset``, captions and credits), native ``<video>`` files, YouTube players and
  embedded posts (X, Instagram, TikTok).
* ``media_policy`` decides what may be used, and why:
    - credited to Rockstar / Take-Two, or posted by Rockstar's own accounts and channels: official;
    - a GTA picture published by a reputable outlet (primary or secondary tier), with its credit
      ("Image: GamesRadar+");
    - never a press-agency photo (Getty, AP, Reuters...: licensed to the outlet, not to you),
      never a picture that is not clearly about GTA, never someone else's post or channel
      (unless the job allows third-party media).
* ``download_article_media`` saves what is allowed into the footage library, under
  ``photos/articles/<job>/`` and ``videos/articles/<job>/``: those files are only used by that job's
  video, where they are shown on the scene that talks about them (see ``footage.select``).

Every downloaded file still goes through the re-upload checks of the library (logos, posts and
subtitles burned into the picture; the vision model's watermark and "is this a game picture" check).
"""
from __future__ import annotations

import html as html_lib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from ...app.core.utils import normalize_ws, write_json

MIN_WIDTH = 640  # smaller pictures are thumbnails, avatars and icons
MAX_PICTURES = 8  # per article
MAX_IMAGE_BYTES = 15_000_000
MAX_VIDEO_BYTES = 250_000_000

SKIP_URL = re.compile(r"(logo|avatar|icon|favicon|sprite|emoji|badge|flag|placeholder|pixel|tracking|/ads?/|banner|newsletter|author|headshot|profile)", re.I)
SKIP_CLASS = re.compile(r"(avatar|logo|icon|author|related|sidebar|newsletter|promo|advert|thumb|byline|social)", re.I)
AGENCY = re.compile(r"\b(getty|getty images|wireimage|afp|associated press|ap photo|\(ap\)|reuters|shutterstock|alamy|imago|epa|zuma|bloomberg via getty|photo by)\b", re.I)
OFFICIAL_CREDIT = re.compile(r"\b(rockstar( games)?|take[- ]?two( interactive)?|take 2)\b", re.I)
ABOUT_GTA = re.compile(r"(gta|grand[\s_-]*theft[\s_-]*auto|rockstar|leonida|vice[\s_-]*city|lucia|jason|screenshot|trailer|gameplay|heist|mission)", re.I)
OFFICIAL_ACCOUNTS = re.compile(r"(?:twitter\.com|x\.com|instagram\.com|tiktok\.com/@)/?(rockstargames|rockstarsupport|rockstar)\b", re.I)
CREDIT_IN_TEXT = re.compile(r"(?:image|photo|picture|screenshot|video)\s*(?:credit|courtesy|source)?\s*[:\-–]\s*([^)\]|]{2,60})", re.I)


def _best_from_srcset(srcset: str) -> tuple[str, int]:
    """(url, width) of the widest candidate of a ``srcset``."""
    best, width = "", 0
    for part in (srcset or "").split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = 0
        if len(bits) > 1 and bits[1].endswith("w") and bits[1][:-1].isdigit():
            w = int(bits[1][:-1])
        if w >= width:
            best, width = bits[0], w
    return best, width


class _PageReader(HTMLParser):
    """Collects pictures, players and embedded posts, with the figure caption around each picture."""

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.items: list[dict[str, Any]] = []
        self.meta: dict[str, str] = {}
        self.has_article = False
        self.depth_article = 0
        self.figure: dict[str, Any] | None = None
        self.in_caption = False
        self.in_ld = False
        self.ld: list[str] = []
        self.blockquote: dict[str, Any] | None = None
        self.video: dict[str, Any] | None = None
        self.picture_sources: list[tuple[str, int]] = []

    def _abs(self, u: str) -> str:
        u = html_lib.unescape((u or "").strip())
        return urljoin(self.base, u) if u and not u.startswith("data:") else ""

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs_list}
        cls = a.get("class", "")
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in ("og:image", "og:image:secure_url", "twitter:image", "og:video", "og:video:secure_url", "og:image:alt", "twitter:image:alt"):
                self.meta.setdefault(key, a.get("content", ""))
        elif tag == "article":
            self.has_article = True
            self.depth_article += 1
        elif tag == "script" and "ld+json" in a.get("type", ""):
            self.in_ld = True
            self.ld.append("")
        elif tag == "figure":
            self.figure = {"caption": "", "items": [], "class": cls}
        elif tag == "figcaption" and self.figure is not None:
            self.in_caption = True
        elif tag == "picture":
            self.picture_sources = []
        elif tag == "source" and self.video is not None:
            src = self._abs(a.get("src", ""))
            if src:
                self.video["url"] = self.video.get("url") or src
        elif tag == "source":
            url, w = _best_from_srcset(a.get("srcset", ""))
            if url:
                self.picture_sources.append((self._abs(url), w))
        elif tag == "img":
            url, w = _best_from_srcset(a.get("srcset") or a.get("data-srcset") or "")
            for key in ("data-original-mos", "data-src", "data-lazy-src", "data-original"):
                if not url and a.get(key):
                    url = a[key]
            url = self._abs(url or a.get("src", ""))
            for src_url, src_w in self.picture_sources:  # <picture>: the widest <source> wins
                if src_w > w:
                    url, w = src_url, src_w
            self.picture_sources = []
            try:
                w = max(w, int(re.sub(r"\D", "", a.get("width", "")) or 0))
            except ValueError:
                pass
            if not url:
                return
            item = {"kind": "image", "url": url, "width": w, "alt": normalize_ws(a.get("alt", "")), "class": cls, "in_article": self.depth_article > 0, "in_figure": self.figure is not None}
            if self.figure is not None:
                self.figure["items"].append(item)
            self.items.append(item)
        elif tag == "video":
            self.video = {"kind": "video", "url": self._abs(a.get("src", "")), "poster": self._abs(a.get("poster", "")), "in_article": self.depth_article > 0, "alt": normalize_ws(a.get("title", "") or a.get("aria-label", ""))}
        elif tag == "iframe":
            src = self._abs(a.get("src") or a.get("data-src") or "")
            m = re.search(r"(?:youtube(?:-nocookie)?\.com/embed/|youtu\.be/)([A-Za-z0-9_-]{6,})", src)
            if m:
                self.items.append({"kind": "youtube", "url": f"https://www.youtube.com/watch?v={m.group(1)}", "in_article": self.depth_article > 0, "alt": normalize_ws(a.get("title", ""))})
        elif tag == "blockquote" and re.search(r"twitter-tweet|instagram-media|tiktok-embed", cls):
            platform = "x" if "twitter" in cls else "instagram" if "instagram" in cls else "tiktok"
            self.blockquote = {"kind": "post", "platform": platform, "url": self._abs(a.get("cite") or a.get("data-instgrm-permalink") or ""), "in_article": self.depth_article > 0, "alt": ""}
        elif tag == "a" and self.blockquote is not None and not self.blockquote["url"]:
            href = self._abs(a.get("href", ""))
            if re.search(r"(twitter\.com|x\.com)/[^/]+/status/|instagram\.com/(p|reel)/|tiktok\.com/@", href):
                self.blockquote["url"] = href

    def handle_endtag(self, tag: str) -> None:
        if tag == "article" and self.depth_article:
            self.depth_article -= 1
        elif tag == "script":
            self.in_ld = False
        elif tag == "figcaption":
            self.in_caption = False
        elif tag == "figure" and self.figure is not None:
            cap = normalize_ws(self.figure["caption"])
            for it in self.figure["items"]:
                it["caption"] = cap
                it["figure_class"] = self.figure["class"]
            self.figure = None
            self.picture_sources = []
        elif tag == "video" and self.video is not None:
            if self.video.get("url"):
                self.items.append(self.video)
            self.video = None
        elif tag == "blockquote" and self.blockquote is not None:
            if self.blockquote["url"]:
                self.items.append(self.blockquote)
            self.blockquote = None

    def handle_data(self, data: str) -> None:
        if self.in_caption and self.figure is not None:
            self.figure["caption"] += " " + data
        elif self.in_ld and self.ld:
            self.ld[-1] += data
        elif self.blockquote is not None and len(self.blockquote["alt"]) < 280:
            self.blockquote["alt"] = normalize_ws(self.blockquote["alt"] + " " + data)


def _credit(text: str) -> str:
    m = CREDIT_IN_TEXT.search(text or "")
    return normalize_ws(m.group(1)).strip(" .") if m else ""


def _key(url: str) -> str:
    """The picture behind a URL, whatever its size variant ("x-1920-80.jpg.webp", "x-scaled.jpg", "x-640x360.jpg")."""
    name = Path(urlparse(url or "").path).name.lower()
    while re.search(r"\.(jpe?g|png|webp|avif|gif)$", name):
        name = re.sub(r"\.(jpe?g|png|webp|avif|gif)$", "", name)
    return re.sub(r"(-scaled|-\d{2,4}x\d{2,4}|-\d{2,4}(-\d{2,3})?|_full)$", "", name)


def main_content_pictures(html: str, page_url: str) -> set[str]:
    """Keys of the pictures inside the article's own text (trafilatura's main-content detection)."""
    try:
        import trafilatura

        xml = trafilatura.extract(html, url=page_url, include_images=True, output_format="xml", favor_precision=True) or ""
    except Exception:
        return set()
    return {_key(html_lib.unescape(m)) for m in re.findall(r'<graphic[^>]*\bsrc="([^"]+)"', xml)}


def find_media(html: str, page_url: str) -> list[dict[str, Any]]:
    """Pictures, videos, YouTube players and embedded posts of an article page (deduplicated, page order).

    Pictures are the lead picture and the pictures inside the article's own text: "related stories",
    sidebars and menus (also when they sit in their own ``<article>`` tags) are left out.
    """
    main = main_content_pictures(html, page_url)
    reader = _PageReader(page_url)
    try:
        reader.feed(html or "")
    except Exception:  # a broken page must not stop the article
        pass
    out: list[dict[str, Any]] = []
    lead = reader.meta.get("og:image") or reader.meta.get("og:image:secure_url") or reader.meta.get("twitter:image")
    if lead:
        out.append({"kind": "image", "url": urljoin(page_url, html_lib.unescape(lead)), "width": 1200, "alt": normalize_ws(reader.meta.get("og:image:alt") or reader.meta.get("twitter:image:alt") or ""), "caption": "", "role": "lead", "in_article": True, "in_figure": True})
    for blob in reader.ld:  # JSON-LD VideoObject / image
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        for node in data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []:
            if isinstance(node, dict) and node.get("@type") == "VideoObject":
                url = node.get("contentUrl") or node.get("embedUrl") or ""
                if url:
                    m = re.search(r"youtube(?:-nocookie)?\.com/embed/([A-Za-z0-9_-]{6,})", url)
                    out.append({"kind": "youtube", "url": f"https://www.youtube.com/watch?v={m.group(1)}", "alt": normalize_ws(str(node.get("name") or ""))} if m else
                               {"kind": "video", "url": url, "alt": normalize_ws(str(node.get("name") or "")), "in_article": True})
    for it in reader.items:
        if it["kind"] == "image" and _key(it["url"]) not in main:
            continue  # not in the article's text: menus, sidebars, related stories
        if reader.has_article and not it.get("in_article") and it["kind"] == "video":
            continue
        out.append(it)
    seen: set[str] = set()
    uniq = []
    for it in sorted(out, key=lambda x: -(x.get("width") or 0) if x["kind"] == "image" and x.get("role") != "lead" else 0):
        key = _key(it["url"]) if it["kind"] == "image" else it["url"]
        if not it["url"] or key in seen:
            continue
        seen.add(key)
        it.setdefault("caption", "")
        it["credit"] = _credit(it.get("caption", "")) or _credit(it.get("alt", ""))
        uniq.append(it)
    return uniq


def media_policy(item: dict[str, Any], *, article_tier: str, publisher: str, third_party_ok: bool = False) -> tuple[str, str, str]:
    """(decision, reason, on-screen credit). Decisions: "official", "article", "third_party", "skip"."""
    text = " ".join([item.get("alt", ""), item.get("caption", ""), item.get("credit", ""), urlparse(item.get("url", "")).path])
    if item["kind"] == "post":
        if OFFICIAL_ACCOUNTS.search(item.get("url", "")):
            return "official", "post by Rockstar's own account", "Rockstar Games"
        if third_party_ok:
            return "third_party", f"{item.get('platform', 'social')} post (allowed for this job)", publisher
        return "skip", f"someone else's {item.get('platform', 'social')} post: not re-uploaded", ""
    if item["kind"] == "youtube":
        return "check_channel", "YouTube player: used only if the channel is Rockstar's", ""
    if AGENCY.search(text):
        return "skip", "press-agency photo (licensed to the outlet, not to the channel)", ""
    if item["kind"] == "image":
        if item.get("role") != "lead" and item.get("width", 0) and item["width"] < MIN_WIDTH:
            return "skip", f"small picture ({item['width']} px wide)", ""
        if SKIP_URL.search(item.get("url", "")) or SKIP_CLASS.search(" ".join([item.get("class", ""), item.get("figure_class", "")])):
            return "skip", "logo, avatar or page decoration", ""
    if OFFICIAL_CREDIT.search(item.get("credit", "")):
        return "official", "credited to Rockstar Games", "Rockstar Games"
    if not ABOUT_GTA.search(text):
        return "skip", "not clearly a GTA 6 picture (nothing in its caption, text or file name says so)", ""
    if article_tier in ("primary", "secondary"):
        return "article", f"published by {publisher}", publisher
    if third_party_ok:
        return "third_party", "published by a site outside the reputable list (allowed for this job)", publisher
    return "skip", f"{publisher} is not on the reputable-outlet list", ""


def _slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:n] or "media"


def _download_file(url: str, dest: Path, *, referer: str, max_bytes: int) -> Path | None:
    import httpx

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36", "Referer": referer, "Accept": "image/avif,image/webp,image/*,video/*,*/*;q=0.8"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if not (ctype.startswith("image/") or ctype.startswith("video/") or ctype in ("application/octet-stream", "binary/octet-stream")):
                return None
            size = 0
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("too large")
                    fh.write(chunk)
            ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/avif": ".avif", "image/gif": ".gif", "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}.get(ctype.split(";")[0].strip(), dest.suffix or ".bin")
            final = dest.with_suffix(ext)
        tmp.rename(final)
        return final
    except Exception:
        tmp.unlink(missing_ok=True)
        return None


def _to_jpeg_if_needed(path: Path) -> Path | None:
    """Pictures the renderer and the vision model read everywhere (jpg / png / webp); avif and gif are converted."""
    import subprocess

    if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        return path
    if path.suffix.lower() not in (".avif", ".gif"):
        return None
    out = path.with_suffix(".jpg")
    proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-frames:v", "1", "-q:v", "2", str(out)], capture_output=True)
    path.unlink(missing_ok=True)
    return out if proc.returncode == 0 and out.exists() else None


def _image_width(path: Path) -> int:
    import subprocess

    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return int((proc.stdout or "0").strip().split(",")[0] or 0)
    except ValueError:
        return 0


def _youtube_uploader(url: str) -> str:
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return ""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        return str(info.get("channel") or info.get("uploader") or "")
    except Exception:
        return ""


def download_article_media(
    settings: Any,
    articles: list[Any],
    *,
    job_id: str,
    third_party_ok: bool = False,
    log: Callable[[str], None] = lambda m: None,
    download: Callable[..., Path | None] = _download_file,
    youtube_uploader: Callable[[str], str] = _youtube_uploader,
    fetch_videos: Callable[..., list[Path]] | None = None,
) -> dict[str, Any]:
    """Download the allowed pictures and videos of the job's articles. Returns the report (also saved by the caller)."""
    from ..footage.library import footage_dir, sidecar_path
    from ..footage.vocabulary import load_vocabulary

    root = footage_dir(settings)
    vocab = load_vocabulary(settings)
    used: list[dict[str, Any]] = []
    left: list[dict[str, Any]] = []
    for art in articles:
        media = list(getattr(art, "media", None) or [])
        pictures = 0
        for i, it in enumerate(media):
            decision, reason, credit = media_policy(it, article_tier=art.source_tier, publisher=art.publisher, third_party_ok=third_party_ok)
            row = {"article": art.url, "publisher": art.publisher, "kind": it["kind"], "url": it["url"], "alt": it.get("alt", ""), "caption": it.get("caption", ""), "reason": reason}
            if decision == "check_channel":
                who = youtube_uploader(it["url"])
                if re.search(r"rockstar", who, re.I):
                    decision, credit, row["reason"] = "official", "Rockstar Games", f"YouTube video by {who}"
                else:
                    left.append({**row, "reason": f"YouTube video by {who or 'an unknown channel'}: not re-uploaded"})
                    continue
            if decision == "skip":
                left.append(row)
                continue
            text = normalize_ws(" ".join([it.get("alt", ""), it.get("caption", "")]))
            tags = sorted({c for m in vocab.mentions(text) for c in m["concepts"]}) if text else []
            side = {
                "source_url": it["url"],
                "article_url": art.url,
                "publisher": art.publisher,
                "credit": f"{'Footage' if it['kind'] != 'image' else 'Image'}: {credit or art.publisher}",
                "caption": text[:300],
                "role": it.get("role", ""),
                "article_media": job_id,
                "tags": tags,
                **({"uploader": "Rockstar Games"} if decision == "official" else {"allow_third_party": True}),
            }
            if it["kind"] in ("youtube", "post"):  # a video player or a post: yt-dlp, into the job's folder
                if fetch_videos is None:
                    from ..footage.library import fetch as fetch_videos_default

                    got = _fetch_into(settings, it["url"], root / "videos" / "articles" / job_id, fetch_videos_default, log)
                else:
                    got = fetch_videos(settings, it["url"], root / "videos" / "articles" / job_id)
                for p in got:
                    write_json(sidecar_path(p), {**side, "source_url": it["url"]})
                    used.append({**row, "file": p.relative_to(root).as_posix(), "credit": side["credit"]})
                if not got:
                    left.append({**row, "reason": "already in the footage library, or could not be downloaded"})
                continue
            if it["kind"] == "image" and pictures >= MAX_PICTURES:
                left.append({**row, "reason": f"more than {MAX_PICTURES} pictures in the article"})
                continue
            folder = root / ("photos" if it["kind"] == "image" else "videos") / "articles" / job_id
            stem = f"{_slug(art.publisher, 16)}-{i:02d}-{_slug(text or Path(urlparse(it['url']).path).stem)}"
            p = download(it["url"], folder / f"{stem}.bin", referer=art.url, max_bytes=MAX_IMAGE_BYTES if it["kind"] == "image" else MAX_VIDEO_BYTES)
            if p is not None and it["kind"] == "image":
                p = _to_jpeg_if_needed(p)
                if p is not None and _image_width(p) < MIN_WIDTH:
                    p.unlink(missing_ok=True)
                    left.append({**row, "reason": "picture too small once downloaded"})
                    continue
            if p is None:
                left.append({**row, "reason": "could not be downloaded"})
                continue
            write_json(sidecar_path(p), side)
            pictures += it["kind"] == "image"
            used.append({**row, "file": p.relative_to(root).as_posix(), "credit": side["credit"]})
    if used or left:
        log(f"article media: {len(used)} picture(s) / video(s) kept for this video, {len(left)} left out")
    for row in left[:12]:
        log(f"  left out: {row['kind']} {row['url'][:80]} ({row['reason']})")
    return {"used": used, "left_out": left}


def _fetch_into(settings: Any, url: str, folder: Path, fetch: Callable[..., list[Path]], log: Callable[[str], None]) -> list[Path]:
    """yt-dlp download of one official video into ``folder`` (the job's article folder)."""
    import shutil

    from ...app.core.errors import FactoryError

    try:
        got = fetch(settings, [url], log=log)
    except FactoryError as exc:
        log(f"article media: {url}: {exc.message}")
        return []
    out = []
    folder.mkdir(parents=True, exist_ok=True)
    for p in got:
        dest = folder / p.name
        shutil.move(str(p), dest)
        side = p.with_name(p.name + ".json")
        if side.exists():
            side.unlink()
        out.append(dest)
    return out
