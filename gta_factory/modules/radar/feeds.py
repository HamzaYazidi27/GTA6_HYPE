"""Reading the monitored sources. Only machine-readable endpoints that the publishers offer are used:

* ``rss``          any RSS / Atom feed (IGN, GameSpot, PC Gamer, GamesRadar, Eurogamer, GTA news sites ...),
* ``reddit``       a subreddit's public RSS feed (``https://www.reddit.com/r/<sub>/<listing>/.rss``),
* ``youtube``      a channel's official Atom feed (``https://www.youtube.com/feeds/videos.xml?channel_id=...``),
* ``news_search``  Bing News RSS for a query (links point to the publishers' own articles).

Requests are conditional (ETag / Last-Modified): an unchanged feed costs one tiny ``304``.
No HTML page is scraped; the only exception is turning a YouTube ``@handle`` into its channel
id once (the public channel page names it), and a channel id can be given directly instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

import httpx

from ...app.core.errors import ProviderError
from ...app.core.utils import canonical_url, domain_of, normalize_ws, sha1

USER_AGENT = "GhostPlatesContentRadar/1.0 (GTA VI news research; feed reader; +local)"
ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"
CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")


@dataclass
class Entry:
    uid: str
    url: str
    title: str
    summary: str
    author: str | None
    published_at: str | None
    updated_at: str | None
    content_hash: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    status: int                      # HTTP status (304 = unchanged)
    entries: list[Entry]
    etag: str | None = None
    last_modified: str | None = None
    feed_url: str = ""
    options: dict[str, Any] | None = None   # updated source options (e.g. resolved YouTube channel id)


def _date(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, tuple):  # feedparser's *_parsed struct_time
        try:
            return datetime(*value[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            return None
    s = str(value)
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"), ("&#x27;", "'"), ("&lt;", "<"), ("&gt;", ">")):
        text = text.replace(a, b)
    return normalize_ws(text)


def feed_url(source: dict[str, Any]) -> str:
    """The URL a source is read from. A target that already is an http(s) URL is used as is."""
    kind, target = source["kind"], str(source["target"]).strip()
    opts = source.get("options") or {}
    if target.startswith(("http://", "https://")):
        return target
    if kind == "reddit":
        sub = target.removeprefix("r/").strip("/")
        listing = opts.get("listing", "new")
        url = f"https://www.reddit.com/r/{sub}/{listing}/.rss"
        return url + (f"?t={opts.get('t', 'day')}" if listing == "top" else "")
    if kind == "youtube":
        cid = target if CHANNEL_ID.match(target) else opts.get("channel_id")
        if not cid:
            raise ProviderError(f"YouTube channel '{target}' has no channel id yet.")
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
    if kind == "news_search":
        return f"https://www.bing.com/news/search?q={quote_plus(target)}&format=rss"
    raise ProviderError(f"Source kind '{kind}' needs a feed URL as target.")


def resolve_youtube_channel(handle_or_url: str, timeout: float) -> str:
    """Channel id of a YouTube ``@handle`` or channel URL (read once from the public channel page)."""
    t = handle_or_url.strip()
    m = re.search(r"(UC[\w-]{22})", t)
    if m:
        return m.group(1)
    handle = t if t.startswith("@") else "@" + t.rsplit("/@", 1)[-1].strip("/")
    r = httpx.get(f"https://www.youtube.com/{handle}", headers={"User-Agent": USER_AGENT, "Accept-Language": "en"}, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', r.text) or re.search(r'youtube\.com/channel/(UC[\w-]{22})', r.text)
    if not m:
        raise ProviderError(f"Could not find the channel id of {handle}.", hint="Add the source with its channel id (UC...), shown in the channel's About > Share channel > Copy channel ID.")
    return m.group(1)


def _entry_url(kind: str, e: Any) -> str:
    link = e.get("link") or ""
    if kind == "news_search":  # Bing wraps the publisher link: keep the publisher's own URL
        real = parse_qs(urlparse(link).query).get("url", [None])[0]
        link = real or link
    return link


def _entries(kind: str, source_id: int, parsed: Any) -> list[Entry]:
    out: list[Entry] = []
    for e in parsed.entries:
        url = _entry_url(kind, e)
        title = strip_html(e.get("title", ""))
        if not url or not title:
            continue
        summary = ""
        if e.get("summary"):
            summary = strip_html(e["summary"])
        elif e.get("content"):
            summary = strip_html(" ".join(c.get("value", "") for c in e["content"] if isinstance(c, dict)))
        if kind == "youtube":
            md = e.get("media_description") or ""
            summary = strip_html(md) if md else summary
        summary = summary[:1500]
        guid = e.get("id") or e.get("guid") or url
        meta: dict[str, Any] = {}
        if kind == "youtube":
            vid = e.get("yt_videoid")
            if vid:
                meta["video_id"] = vid
            stats = e.get("media_statistics") or {}
            if isinstance(stats, dict) and stats.get("views"):
                meta["views"] = int(stats["views"]) if str(stats["views"]).isdigit() else stats["views"]
        if kind == "news_search":
            src = e.get("news_source") or (e.get("source") or {}).get("title") if isinstance(e.get("source"), dict) else e.get("news_source")
            if src:
                meta["publisher"] = str(src)
        if kind == "reddit":
            meta["community"] = True
        keys = set(dict.keys(e))  # real keys only: feedparser maps a missing "updated" to "published" with a warning
        updated = (_date(e.get("updated_parsed")) or _date(e.get("updated"))) if "updated" in keys or "updated_parsed" in keys else None
        published = (_date(e.get("published_parsed")) or _date(e.get("published"))) if "published" in keys or "published_parsed" in keys else updated
        out.append(
            Entry(
                uid=sha1(f"{source_id}|{guid}")[:24],
                url=url,
                title=title[:300],
                summary=summary,
                author=strip_html(e.get("author", "")) or None,
                published_at=published,
                updated_at=updated,
                content_hash=sha1(title + "\n" + summary)[:16],
                meta=meta,
            )
        )
    return out


def fetch_source(source: dict[str, Any], *, timeout: float = 25.0) -> FetchResult:
    """Read one source. Raises :class:`ProviderError` with a readable message on failure."""
    import feedparser

    options = dict(source.get("options") or {})
    if source["kind"] == "youtube" and not CHANNEL_ID.match(str(source["target"])) and not str(source["target"]).startswith("http") and not options.get("channel_id"):
        try:
            options["channel_id"] = resolve_youtube_channel(str(source["target"]), timeout)
        except httpx.HTTPError as exc:
            raise ProviderError(f"YouTube channel page unreachable: {type(exc).__name__}") from exc
    url = feed_url({**source, "options": options})
    headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT}
    if source.get("etag"):
        headers["If-None-Match"] = source["etag"]
    if source.get("last_modified"):
        headers["If-Modified-Since"] = source["last_modified"]
    try:
        r = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise ProviderError(f"{type(exc).__name__}: {str(exc)[:160] or 'network error'}") from exc
    if r.status_code == 304:
        return FetchResult(304, [], source.get("etag"), source.get("last_modified"), url, options)
    if r.status_code == 429:
        raise ProviderError("HTTP 429: the site asks to slow down (rate limited).")
    if r.status_code >= 400:
        raise ProviderError(f"HTTP {r.status_code} from {domain_of(url)}.")
    parsed = feedparser.parse(r.content)
    if parsed.bozo and not parsed.entries:
        raise ProviderError(f"Not a valid RSS/Atom feed ({type(parsed.get('bozo_exception')).__name__}).", hint="Check the URL: it must be the feed itself, not the web page.")
    return FetchResult(r.status_code, _entries(source["kind"], int(source["id"]), parsed), r.headers.get("ETag"), r.headers.get("Last-Modified"), url, options)


def item_trust(source: dict[str, Any], entry_url: str, tier_of: Any) -> str:
    """How much an entry can be trusted: a Rockstar / Take-Two page is official wherever it was found;
    in a news search, a known outlet is press and any other site is 'web'; otherwise the trust of the
    source itself (Rockstar's own YouTube channel is official, r/GTA6 is community ...)."""
    tier = tier_of(domain_of(entry_url))
    if tier == "primary":
        return "official"
    if source["kind"] == "news_search":
        return "press" if tier == "secondary" else "web"
    return str(source["trust"])


def canonical(entry_url: str) -> str:
    return canonical_url(entry_url)
