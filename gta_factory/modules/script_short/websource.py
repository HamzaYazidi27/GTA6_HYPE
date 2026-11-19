"""Footage from the internet for the beats the footage library cannot show.

When a beat is still missing after matching, the factory looks for it on the web by itself,
instead of waiting for the operator to add footage:

1. **Videos** (YouTube search through yt-dlp): "<game> <what the beat shows> gameplay".
   Rockstar's own uploads first, then short gameplay videos whose title names the game.
   Fan-made concepts, mods, reactions, leaks and other games are skipped by title. The best few
   are downloaded (720p, length-capped) into the job's own folder of the game's library
   (``videos/web/<job>/``).
2. **Pictures**, for when no video shows it (Bing images, then DuckDuckGo images): pictures whose
   title or page names the game, at least 640 px wide (``photos/web/<job>/``).

Nothing is used just because a search returned it: every file goes through the same checks as
the library (re-upload logo / subtitle scan, CLIP's vote against look-alikes, the vision model).
What ends up on screen is marked ``web_found`` in the rights review: it was found automatically
and its permission must be checked before publishing.

Every network call is a parameter of :func:`source_missing`, so tests run offline.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus, urlparse

from ...app.core.utils import write_json

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
# titles that are not real footage of the game (or not of THIS game)
EXCLUDE_TITLE = (
    r"fan[- ]?made|concept|\bmods?\b|modded|modding|remake|remaster|unreal engine|\bue ?5\b|\bfake\b|ai[- ]generated|\bai\b|"
    r"reaction|\breacts?\b|theor(?:y|ies)|predictions?|wish ?list|\bleak|datamine|re-?upload|fivem|\brp\b|roleplay|"
    r"minecraft|roblox|\blego\b|\bps2\b|vice city stories|san andreas|definitive edition|\bmeme|parody"
)
STOP = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "for", "with", "your", "you", "can", "get", "it", "its", "is", "are", "be",
        "this", "that", "these", "those", "into", "from", "by", "as", "while", "when", "then", "plus", "just", "even", "every", "some", "any",
        "will", "now", "back", "number", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "never", "let", "do"}
_LEAD = re.compile(r"^(?:number\s+)?(?:#?\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*[:.)–—-]\s*", re.I)


def defaults(settings: Any) -> dict[str, Any]:
    cfg = dict(settings.get("script_short.web_footage") or {})
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "videos_per_beat": int(cfg.get("videos_per_beat", 2)),
        "photos_per_beat": int(cfg.get("photos_per_beat", 3)),
        "max_videos_per_job": int(cfg.get("max_videos_per_job", 12)),
        "max_video_seconds": float(cfg.get("max_video_seconds", 600)),
        "max_height": int(cfg.get("max_height", 720)),
        "search_results": int(cfg.get("search_results", 12)),
        "photo_fallback": bool(cfg.get("photo_fallback", True)),
        "exclude_words": [str(w) for w in cfg.get("exclude_words") or []],
    }


# ----------------------------------------------------------------------------- what to search for
def keywords(text: str, limit: int = 7) -> str:
    """The words of a phrase that say what to show ("Six: grab a ball and shoot hoops." -> "grab ball shoot hoops")."""
    text = _LEAD.sub("", str(text or "").strip())
    toks = [t for t in re.findall(r"[A-Za-z0-9'-]+", text) if t.lower() not in STOP and len(t) > 1]
    return " ".join(toks[:limit])


def queries(beat: dict[str, Any], game_short: str) -> list[str]:
    """Searches for one beat, most precise first: the narration's own words, then the planned picture."""
    out: list[str] = []
    for src in (beat.get("text"), beat.get("concept"), *(beat.get("prompts") or [])[:1]):
        k = keywords(src or "")
        if k:
            q = f"{game_short} {k}"
            if q.lower() not in (x.lower() for x in out):
                out.append(q)
    return out[:3] or [game_short + " gameplay"]


def game_pattern(settings: Any, gid: str) -> re.Pattern[str]:
    """A title or a page must name the game: its title filter, its names and nicknames."""
    from ..footage.games import game, game_settings
    from ..footage.library import DEFAULT_TITLE_FILTER

    g = game(settings, gid)
    gs = game_settings(settings, gid)
    parts = [str(gs.get("footage.title_filter") or DEFAULT_TITLE_FILTER)]
    for name in [g["name"], g["short"], *(x for x in g.get("say") or [] if re.search(r"\d|gta|grand theft|red dead|rdr", x, re.I))]:
        words = [re.escape(w) for w in re.findall(r"[A-Za-z0-9]+", name)]
        if words:
            parts.append(r"\b" + r"[\s_-]*".join(words) + r"\b")
    return re.compile("|".join(f"(?:{p})" for p in parts), re.I)


def other_games(settings: Any, gid: str) -> re.Pattern[str] | None:
    """Titles that name another game more precisely (a GTA 6 search must not bring GTA 5 videos)."""
    from ..footage.games import games

    names = []
    for oid, g in games(settings).items():
        if oid == gid:
            continue
        for name in [g["short"], g["name"]]:
            words = [re.escape(w) for w in re.findall(r"[A-Za-z0-9]+", name)]
            if words:
                names.append(r"\b" + r"[\s_-]*".join(words) + r"\b")
    return re.compile("|".join(names), re.I) if names else None


def keep_title(title: str, *, game_rx: re.Pattern[str], other_rx: re.Pattern[str] | None, blocked: list[str]) -> str:
    """"" when a search result may be used, else why not."""
    if not game_rx.search(title):
        return "does not name the game"
    m = re.search(EXCLUDE_TITLE + ("|" + "|".join(re.escape(w) for w in blocked) if blocked else ""), title, re.I)
    if m:
        return f"not real footage ({m.group(0).strip()!r})"
    if other_rx is not None:
        own = {x.group(0).lower() for x in game_rx.finditer(title)}
        other = [x.group(0) for x in other_rx.finditer(title) if x.group(0).lower() not in own and not any(x.group(0).lower() in o or o in x.group(0).lower() for o in own)]
        if other:
            return f"names another game ({other[0]!r})"
    return ""


def relevance(title: str, query: str) -> float:
    words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", query)} - STOP
    have = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", title)}
    return len(words & have) / max(1, len(words))


# ----------------------------------------------------------------------------- the searches (network)
def youtube_search(query: str, n: int, *, cookies: str = "") -> list[dict[str, Any]]:
    """YouTube results without downloading: [{"id", "url", "title", "duration", "channel"}]."""
    import yt_dlp  # type: ignore

    opts: dict[str, Any] = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True}
    if cookies:
        opts["cookiesfrombrowser"] = (cookies,)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{n}:{query}", download=False) or {}
    out = []
    for e in info.get("entries") or []:
        vid = str(e.get("id") or "")
        if not vid:
            continue
        out.append({"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": str(e.get("title") or ""), "duration": e.get("duration"),
                    "channel": str(e.get("channel") or e.get("uploader") or ""), "channel_url": str(e.get("channel_url") or e.get("uploader_url") or "")})
    return out


def youtube_download(url: str, folder: Path, *, max_height: int, max_seconds: float, cookies: str = "") -> tuple[Path | None, dict[str, Any]]:
    """One video into ``folder`` (video only, mp4 when there is one). Returns (file, info)."""
    import yt_dlp  # type: ignore

    folder.mkdir(parents=True, exist_ok=True)
    before = set(folder.iterdir())
    opts: dict[str, Any] = {
        "format": f"bv*[height<={max_height}][ext=mp4]/bv*[height<={max_height}]/b[height<={max_height}]/b",
        "outtmpl": str(folder / "%(title).70B [%(id)s].%(ext)s"),
        "noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True,
        "max_filesize": 400_000_000,
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <= {int(max_seconds)}"),
    }
    if cookies:
        opts["cookiesfrombrowser"] = (cookies,)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True) or {}
    from ..footage.library import VIDEO_EXT

    new = [p for p in set(folder.iterdir()) - before if p.suffix.lower() in VIDEO_EXT]
    if not new:  # already there from an earlier try
        vid = str(info.get("id") or "")
        new = [p for p in folder.iterdir() if vid and f"[{vid}]" in p.name and p.suffix.lower() in VIDEO_EXT]
    meta = {k: str(info.get(k)) for k in ("uploader", "channel", "channel_url", "title") if info.get(k)}
    return (new[0] if new else None), meta


def _get(url: str, *, params: dict[str, str] | None = None, referer: str = "") -> str:
    import httpx

    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", **({"Referer": referer} if referer else {})}
    r = httpx.get(url, params=params, headers=headers, follow_redirects=True, timeout=20)
    r.raise_for_status()
    return r.text


def parse_bing(page: str) -> list[dict[str, str]]:
    """Bing image results: the ``m`` attribute of each result holds the picture, its title and its page."""
    out = []
    for raw in re.findall(r'\sm="(\{[^"]*?murl[^"]*?\})"', page):
        try:
            d = json.loads(html.unescape(raw))
        except ValueError:
            continue
        if d.get("murl"):
            out.append({"url": d["murl"], "title": html.unescape(str(d.get("t") or "")), "page": str(d.get("purl") or "")})
    return out


def bing_images(query: str, n: int) -> list[dict[str, str]]:
    page = _get("https://www.bing.com/images/search", params={"q": query, "form": "HDRSC2", "first": "1", "qft": "+filterui:imagesize-large"})
    return parse_bing(page)[:n]


def ddg_images(query: str, n: int) -> list[dict[str, str]]:
    first = _get("https://duckduckgo.com/", params={"q": query, "iax": "images", "ia": "images"})
    m = re.search(r"vqd=['\"]?([\d-]+)", first)
    if not m:
        return []
    data = json.loads(_get("https://duckduckgo.com/i.js", params={"l": "us-en", "o": "json", "q": query, "vqd": m.group(1), "f": ",,,,,", "p": "1"}, referer="https://duckduckgo.com/"))
    return [{"url": r["image"], "title": str(r.get("title") or ""), "page": str(r.get("url") or "")} for r in data.get("results") or [] if r.get("image")][:n]


def image_search(query: str, n: int) -> list[dict[str, str]]:
    """Bing images, DuckDuckGo images when Bing gives nothing."""
    for engine in (bing_images, ddg_images):
        try:
            hits = engine(query, n)
        except Exception:
            hits = []
        if hits:
            return hits
    return []


def image_download(url: str, dest: Path, *, referer: str) -> Path | None:
    from ..article_extraction.media import MAX_IMAGE_BYTES, MIN_WIDTH, _download_file, _image_width, _to_jpeg_if_needed

    p = _download_file(url, dest, referer=referer or url, max_bytes=MAX_IMAGE_BYTES)
    if p is None or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"):
        if p is not None:
            p.unlink(missing_ok=True)
        return None
    p = _to_jpeg_if_needed(p)
    if p is not None and _image_width(p) < MIN_WIDTH:
        p.unlink(missing_ok=True)
        return None
    return p


def playable(path: Path) -> bool:
    from ...app.core.errors import FactoryError
    from ..footage.library import _probe

    try:
        return _probe(path)["duration"] > 0.5
    except (FactoryError, KeyError, TypeError, ValueError):
        return False


# ----------------------------------------------------------------------------- the step
def _slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:n] or "web"


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def source_missing(
    settings: Any,
    beats: list[dict[str, Any]],
    *,
    job: str,
    log: Callable[[str], None] = print,
    search_videos: Callable[..., list[dict[str, Any]]] | None = None,
    download_video: Callable[..., tuple[Path | None, dict[str, Any]]] | None = None,
    search_images: Callable[[str, int], list[dict[str, str]]] | None = None,
    download_image: Callable[..., Path | None] | None = None,
) -> dict[str, Any]:
    """Search and download web footage for ``beats`` (the missing ones). Returns the report.

    Files land in the job's folders of each game's library, with a sidecar that keeps them for this
    job only (``article_media``) and marks them ``web_found``.
    """
    from ..footage.games import game, game_settings
    from ..footage.library import _cookies, footage_dir, sidecar_path

    cfg = defaults(settings)
    cookies = _cookies(settings)
    search_videos = search_videos or (lambda q, n: youtube_search(q, n, cookies=cookies))
    download_video = download_video or (lambda url, folder: youtube_download(url, folder, max_height=cfg["max_height"], max_seconds=cfg["max_video_seconds"], cookies=cookies))
    search_images = search_images or image_search
    download_image = download_image or image_download
    blocked = [str(w) for w in settings.get("rights_review.blocked_keywords", []) or []] + cfg["exclude_words"]
    report: dict[str, Any] = {"beats": [], "videos": 0, "photos": 0, "errors": []}
    taken: set[str] = set()
    n_videos = 0
    for b in beats:
        gid = b.get("game") or "gta6"
        g = game(settings, gid)
        root = footage_dir(game_settings(settings, gid))
        game_rx, other_rx = game_pattern(settings, gid), other_games(settings, gid)
        qs = queries(b, g["short"])
        row: dict[str, Any] = {"beat": b.get("index"), "text": b.get("text", ""), "game": gid, "queries": qs, "videos": [], "photos": [], "skipped": []}
        # --- videos
        cands: dict[str, dict[str, Any]] = {}
        for q in qs:
            try:
                hits = search_videos(q + " gameplay", cfg["search_results"])
            except Exception as exc:
                report["errors"].append(f"video search '{q}': {type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
                break  # the search engine is not reachable: the other queries would fail too
            for h in hits:
                if h["id"] in cands or h["id"] in taken:
                    continue
                why = keep_title(h["title"], game_rx=game_rx, other_rx=other_rx, blocked=blocked)
                dur = h.get("duration")
                if not why and dur and float(dur) > cfg["max_video_seconds"]:
                    why = f"too long ({float(dur) / 60:.0f} min)"
                if why:
                    row["skipped"].append({"url": h["url"], "title": h["title"][:90], "why": why})
                    continue
                official = "rockstar games" in h.get("channel", "").lower()
                cands[h["id"]] = {**h, "query": q, "score": (2.0 if official else 0.0) + relevance(h["title"], q) - (float(dur or 300) / 3600.0)}
        ranked = sorted(cands.values(), key=lambda c: -c["score"])
        for c in ranked:
            if len(row["videos"]) >= cfg["videos_per_beat"] or n_videos >= cfg["max_videos_per_job"]:
                break
            log(f"web footage: beat “{b.get('text', '')[:40]}” <- {c['channel'] or 'YouTube'}: {c['title'][:70]}")
            try:
                path, meta = download_video(c["url"], root / "videos" / "web" / job)
            except Exception as exc:
                row["skipped"].append({"url": c["url"], "title": c["title"][:90], "why": f"download failed: {str(exc).splitlines()[0][:120]}"})
                continue
            if path is None:
                row["skipped"].append({"url": c["url"], "title": c["title"][:90], "why": "not downloaded (too long or too big)"})
                continue
            if not playable(path):  # a broken file would stop the indexing of the whole library
                path.unlink(missing_ok=True)
                row["skipped"].append({"url": c["url"], "title": c["title"][:90], "why": "the downloaded file cannot be read"})
                continue
            who = meta.get("channel") or meta.get("uploader") or c.get("channel") or ""
            official = "rockstar games" in who.lower()
            write_json(sidecar_path(path), {
                "source_url": c["url"], "uploader": meta.get("uploader") or who, "channel": who, "channel_url": meta.get("channel_url") or c.get("channel_url", ""),
                "title": c["title"], "credit": "Footage: Rockstar Games" if official else f"Footage: {who or 'YouTube'} (YouTube)",
                "tags": [], "article_media": job, "query": c["query"],
                **({} if official else {"web_found": True, "allow_third_party": True}),
            })
            taken.add(c["id"])
            n_videos += 1
            row["videos"].append({"file": path.relative_to(root).as_posix(), "url": c["url"], "title": c["title"], "channel": who})
        # --- pictures: a still that shows it, for when no video does (cheap, always fetched)
        if cfg["photo_fallback"] and cfg["photos_per_beat"] > 0:
            for q in qs:
                if len(row["photos"]) >= cfg["photos_per_beat"]:
                    break
                try:
                    hits = search_images(q + " screenshot", cfg["search_results"])
                except Exception as exc:
                    report["errors"].append(f"image search '{q}': {type(exc).__name__}: {str(exc)[:120]}")
                    break
                for k, h in enumerate(hits):
                    if len(row["photos"]) >= cfg["photos_per_beat"]:
                        break
                    if h["url"] in taken:
                        continue
                    why = keep_title(" ".join([h.get("title", ""), h.get("page", "")]), game_rx=game_rx, other_rx=other_rx, blocked=blocked)
                    if why:
                        row["skipped"].append({"url": h["url"], "title": h.get("title", "")[:90], "why": why})
                        continue
                    stem = f"beat{int(b.get('index') or 0) + 1:02d}-{_slug(q, 30)}-{k:02d}"
                    path = download_image(h["url"], root / "photos" / "web" / job / f"{stem}.bin", referer=h.get("page") or "")
                    taken.add(h["url"])
                    if path is None:
                        row["skipped"].append({"url": h["url"], "title": h.get("title", "")[:90], "why": "could not be downloaded, or too small"})
                        continue
                    host = _host(h.get("page") or h["url"])
                    write_json(sidecar_path(path), {
                        "source_url": h["url"], "page_url": h.get("page") or "", "title": h.get("title", ""), "uploader": host,
                        "credit": f"Image: {host}" if host else "Image: web", "tags": [], "article_media": job, "query": q,
                        "web_found": True, "allow_third_party": True,
                    })
                    row["photos"].append({"file": path.relative_to(root).as_posix(), "url": h["url"], "page": h.get("page") or ""})
        report["videos"] += len(row["videos"])
        report["photos"] += len(row["photos"])
        report["beats"].append(row)
        log(f"web footage: beat “{b.get('text', '')[:40]}”: {len(row['videos'])} video(s), {len(row['photos'])} picture(s) downloaded")
    return report


def search_url(query: str) -> str:
    """Where the operator can look by hand (shown in the report of a beat that stays missing)."""
    return "https://www.youtube.com/results?search_query=" + quote_plus(query + " gameplay")
