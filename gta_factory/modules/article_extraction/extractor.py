"""Fetch a news article and normalise it: title, publisher, author, dates, clean body text.

Extraction strategy (first one that yields enough text wins):

1. ``trafilatura`` on the fetched HTML (precision-oriented main-content extraction).
2. Structured data embedded by the publisher (JSON-LD ``NewsArticle`` / OpenGraph).
3. Optional headless-browser render for JavaScript-only pages (needs ``playwright``).

The module never invents fields: anything a page does not expose stays ``None``.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from ...app.core.errors import ExtractionError
from ...app.core.utils import canonical_url, domain_of, normalize_ws, now_iso, sha1

USER_AGENT = "Mozilla/5.0 (compatible; GTA6VideoFactory/1.0; article reader for editorial research)"
MIN_WORDS = 120

PUBLISHER_NAMES = {
    "rockstargames.com": "Rockstar Games",
    "take2games.com": "Take-Two Interactive",
    "ir.take2games.com": "Take-Two Interactive (Investor Relations)",
    "ign.com": "IGN",
    "gamespot.com": "GameSpot",
    "videogameschronicle.com": "VGC",
    "eurogamer.net": "Eurogamer",
    "pcgamer.com": "PC Gamer",
    "kotaku.com": "Kotaku",
    "gamesradar.com": "GamesRadar+",
    "polygon.com": "Polygon",
    "theverge.com": "The Verge",
    "gamesindustry.biz": "GamesIndustry.biz",
    "bloomberg.com": "Bloomberg",
    "reuters.com": "Reuters",
    "variety.com": "Variety",
    "bbc.com": "BBC",
    "bbc.co.uk": "BBC",
    "pushsquare.com": "Push Square",
    "rockpapershotgun.com": "Rock Paper Shotgun",
    "gamesbeat.com": "GamesBeat",
    "venturebeat.com": "VentureBeat",
    "insider-gaming.com": "Insider Gaming",
}


@dataclass
class Article:
    url: str
    final_url: str
    publisher: str
    publisher_domain: str
    source_tier: str
    title: str
    author: str | None
    published_at: str | None
    modified_at: str | None
    text: str
    word_count: int
    language: str | None
    description: str | None
    extraction_method: str
    fetched_at: str
    content_hash: str
    warnings: list[str] = field(default_factory=list)
    media: list[dict[str, Any]] = field(default_factory=list)  # pictures, videos and embedded posts of the page (media.find_media)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_html(url: str, timeout: float = 30.0) -> tuple[str, str]:
    """Return ``(html, final_url)`` or raise :class:`ExtractionError` with a useful hint."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise ExtractionError(
            f"Could not fetch {url}: {type(exc).__name__}: {exc}",
            hint="Check the URL and your connection. You can also paste the article text with --text-file.",
        ) from exc
    if r.status_code in (401, 402, 403, 451):
        raise ExtractionError(
            f"{domain_of(url)} refused the request (HTTP {r.status_code}).",
            hint="The publisher blocks automated readers or the article is paywalled. Save the article text to a file and pass it with --text-file URL=path.",
        )
    if r.status_code >= 400:
        raise ExtractionError(f"{url} answered HTTP {r.status_code}.", hint="Check that the article URL is correct and still online.")
    return r.text, str(r.url)


def render_html(url: str, timeout_ms: int = 45000) -> str | None:
    """Render a JavaScript-only page with a real headless browser. Returns None when unavailable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    import glob
    import os

    exe = os.environ.get("GTA_FACTORY_CHROMIUM")
    if not exe:
        found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))
        exe = found[-1] if found else None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            html = page.content()
            browser.close()
            return html
    except Exception:
        return None


def _iso(value: Any) -> str | None:
    """Normalise many date spellings to ISO-8601 (UTC when a zone is known)."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                from dateutil import parser as dateparser

                dt = dateparser.parse(s)
            except (ValueError, OverflowError, ImportError):
                return None
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
        return dt.isoformat(timespec="seconds")
    return dt.isoformat(timespec="seconds") if (dt.hour or dt.minute) else dt.date().isoformat()


def _json_ld(html: str) -> dict[str, Any]:
    """Pull the article node out of JSON-LD blocks."""
    best: dict[str, Any] = {}
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            if "@graph" in node:
                stack.extend(node["@graph"] if isinstance(node["@graph"], list) else [node["@graph"]])
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if any(t in ("NewsArticle", "Article", "BlogPosting", "ReportageNewsArticle", "LiveBlogPosting") for t in types):
                if len(str(node.get("articleBody") or "")) >= len(str(best.get("articleBody") or "")):
                    best = node
    return best


def _meta(html: str, *names: str) -> str | None:
    for name in names:
        for pat in (
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']',
        ):
            m = re.search(pat, html, re.I)
            if m:
                import html as html_lib

                return html_lib.unescape(m.group(1)).strip()
    return None


def _author_from(node: Any) -> str | None:
    if isinstance(node, dict):
        return node.get("name")
    if isinstance(node, list):
        names = [_author_from(n) for n in node]
        return ", ".join(n for n in names if n) or None
    if isinstance(node, str):
        return node
    return None


def _clean_text(text: str) -> str:
    lines = [normalize_ws(ln) for ln in (text or "").splitlines()]
    return "\n\n".join(ln for ln in lines if ln)


def parse_article(html: str, url: str, final_url: str | None = None, *, tier_of=None) -> Article:
    """Turn HTML into an :class:`Article`. ``tier_of`` maps a domain to primary/secondary/other."""
    import trafilatura

    final_url = final_url or url
    domain = domain_of(final_url)
    warnings: list[str] = []
    ld = _json_ld(html)

    tr: dict[str, Any] = {}
    raw = trafilatura.extract(html, url=final_url, output_format="json", with_metadata=True, include_comments=False, include_tables=True, favor_precision=True)
    if raw:
        try:
            tr = json.loads(raw)
        except json.JSONDecodeError:
            tr = {}
    text = _clean_text(tr.get("text") or "")
    method = "trafilatura"
    if len(text.split()) < MIN_WORDS:
        loose = trafilatura.extract(html, url=final_url, include_comments=False, favor_recall=True) or ""
        if len(loose.split()) > len(text.split()):
            text, method = _clean_text(loose), "trafilatura_recall"
    body_ld = _clean_text(str(ld.get("articleBody") or ""))
    if len(body_ld.split()) > max(MIN_WORDS, int(len(text.split()) * 1.3)):
        text, method = body_ld, "json_ld"
    if method.startswith("trafilatura"):
        text = restore_headings(html, text)

    title = normalize_ws(str(ld.get("headline") or "")) or _meta(html, "og:title", "twitter:title") or normalize_ws(tr.get("title") or "")
    title = re.sub(r"\s+[|\-–—]\s+[^|\-–—]{2,40}$", "", title) if title.count("|") or " - " in title[-45:] else title
    author = _author_from(ld.get("author")) or tr.get("author") or _meta(html, "author", "article:author", "parsely-author")
    if author and (author.startswith("http") or len(author) > 120):
        author = None
    published = _iso(ld.get("datePublished")) or _iso(_meta(html, "article:published_time", "parsely-pub-date", "date", "pubdate")) or _iso(tr.get("date"))
    modified = _iso(ld.get("dateModified")) or _iso(_meta(html, "article:modified_time", "og:updated_time"))
    publisher_ld = ld.get("publisher")
    publisher = PUBLISHER_NAMES.get(domain)
    if not publisher:
        for known, name in PUBLISHER_NAMES.items():
            if domain.endswith("." + known):
                publisher = name
    publisher = publisher or (publisher_ld.get("name") if isinstance(publisher_ld, dict) else None) or _meta(html, "og:site_name") or tr.get("sitename") or domain
    description = _meta(html, "og:description", "description") or normalize_ws(str(ld.get("description") or "")) or None
    language = (re.search(r"<html[^>]+lang=[\"']([a-zA-Z\-]+)", html) or [None, None])[1]

    if not published:
        warnings.append("No publication date exposed by the page; recency cannot be verified from the source itself.")
    if not author:
        warnings.append("No author exposed by the page.")
    words = len(text.split())
    if words < MIN_WORDS:
        warnings.append(f"Only {words} words of body text were extracted; the page may be JavaScript-rendered or paywalled.")

    return Article(
        url=canonical_url(url),
        final_url=final_url,
        publisher=str(publisher),
        publisher_domain=domain,
        source_tier=tier_of(domain) if tier_of else "other",
        title=title or "(untitled)",
        author=author,
        published_at=published,
        modified_at=modified,
        text=text,
        word_count=words,
        language=language,
        description=description,
        extraction_method=method,
        fetched_at=now_iso(),
        content_hash=sha1(text)[:16],
        warnings=warnings,
        media=_media(html, final_url),
    )


def _media(html: str, page_url: str) -> list[dict[str, Any]]:
    try:
        from .media import find_media

        return find_media(html, page_url)
    except Exception:  # pictures are a bonus: never lose the article over them
        return []


def restore_headings(html: str, text: str) -> str:
    """Put back the section headings the text extraction dropped (a guide's "How Do You Fast Travel?").

    Each ``<h2>``-``<h4>`` heading of the page goes right before the paragraph that follows it on the page,
    when that paragraph is in the text and the heading is not. Headings with no paragraph of the article
    after them (related stories, newsletters) are left out.
    """
    import html as html_lib

    out = text
    for m in re.finditer(r"<h([2-4])[^>]*>(.*?)</h\1>(.*?)<p[^>]*>(.*?)</p>", html, re.S | re.I):
        heading = normalize_ws(html_lib.unescape(re.sub(r"<[^>]+>", " ", m.group(2))))
        para = normalize_ws(html_lib.unescape(re.sub(r"<[^>]+>", " ", m.group(4))))
        if not heading or len(heading) > 120 or len(para) < 30 or heading in out or len(m.group(3)) > 2000:
            continue
        anchor = para[:60]
        at = out.find(anchor)
        if at < 0:
            continue
        out = out[:at] + heading + "\n\n" + out[at:]
    return out


def extract_article(url: str, *, tier_of=None, allow_render: bool = True) -> Article:
    """Fetch + parse one URL. Raises :class:`ExtractionError` when no usable text is found."""
    html, final_url = fetch_html(url)
    art = parse_article(html, url, final_url, tier_of=tier_of)
    if art.word_count < MIN_WORDS and allow_render:
        rendered = render_html(final_url)
        if rendered:
            art2 = parse_article(rendered, url, final_url, tier_of=tier_of)
            if art2.word_count > art.word_count:
                art2.extraction_method += "+rendered"
                art = art2
    if art.word_count < 40:
        raise ExtractionError(
            f"No article text could be extracted from {url} ({art.word_count} words).",
            hint="The page is probably JavaScript-only or paywalled. Install the optional browser fallback (`pip install playwright && playwright install chromium`) or pass the text with --text-file URL=path.",
        )
    return art


def article_from_text(url: str, text: str, *, title: str | None = None, tier_of=None) -> Article:
    """Build an article from text supplied by the operator (paywalled / blocked sources)."""
    domain = domain_of(url)
    clean = _clean_text(text)
    first = clean.split("\n\n")[0] if clean else ""
    return Article(
        url=canonical_url(url),
        final_url=url,
        publisher=PUBLISHER_NAMES.get(domain, domain),
        publisher_domain=domain,
        source_tier=tier_of(domain) if tier_of else "other",
        title=title or first[:140],
        author=None,
        published_at=None,
        modified_at=None,
        text=clean,
        word_count=len(clean.split()),
        language=None,
        description=None,
        extraction_method="operator_supplied_text",
        fetched_at=now_iso(),
        content_hash=sha1(clean)[:16],
        warnings=["Text supplied manually by the operator; publication date and author were not verified against the page."],
    )
