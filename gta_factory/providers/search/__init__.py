"""News search adapters used to find corroborating coverage.

Every adapter returns :class:`SearchHit` objects. Only syndication endpoints the
publishers and search engines expose for machines (RSS/Atom) are used: no HTML
result pages are scraped.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from ...app.core.utils import canonical_url, domain_of, normalize_ws, tokens

UA = {"User-Agent": "GTA6VideoFactory/1.0 (news research; feed reader)", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"}


@dataclass
class SearchHit:
    title: str
    url: str
    publisher: str
    domain: str
    published_at: str | None
    snippet: str
    provider: str
    direct: bool  # False when the link is an aggregator redirect that cannot be fetched as the article

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _strip_html(text: str) -> str:
    return normalize_ws(re.sub(r"<[^>]+>", " ", text or ""))


def _feed(url: str, params: dict[str, str] | None, timeout: float):
    import feedparser

    r = httpx.get(url, params=params, headers=UA, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return feedparser.parse(r.content)


class SearchProvider:
    name = "base"

    def __init__(self, settings: Any):
        self.settings = settings
        self.timeout = float(settings.get("providers.search.request_timeout_seconds", 25))
        self.max_results = int(settings.get("providers.search.max_results_per_query", 8))

    def search(self, query: str) -> list[SearchHit]:  # pragma: no cover - interface
        raise NotImplementedError


class SiteFeedsProvider(SearchProvider):
    """Reads the configured first-party publisher feeds and keeps entries that match the query."""

    name = "site_feeds"
    _cache: dict[str, Any] = {}

    def search(self, query: str) -> list[SearchHit]:
        q = set(tokens(query))
        hits: list[tuple[float, SearchHit]] = []
        for feed in self.settings.get("sources.feeds", []) or []:
            url = feed["url"]
            if url not in self._cache:
                try:
                    self._cache[url] = _feed(url, None, self.timeout)
                except (httpx.HTTPError, ValueError):
                    self._cache[url] = None
            parsed = self._cache[url]
            if parsed is None:
                continue
            for e in parsed.entries:
                title = normalize_ws(e.get("title", ""))
                snippet = _strip_html(e.get("summary", ""))[:400]
                overlap = len(q & set(tokens(title + " " + snippet)))
                if overlap < max(2, len(q) // 3):
                    continue
                link = e.get("link") or ""
                hits.append(
                    (
                        overlap,
                        SearchHit(title, canonical_url(link), feed["name"], domain_of(link), _date(e.get("published") or e.get("updated")), snippet, self.name, True),
                    )
                )
        hits.sort(key=lambda h: -h[0])
        return [h for _, h in hits[: self.max_results]]


class BingNewsRSSProvider(SearchProvider):
    """Bing News RSS. Result links carry the publisher URL in the ``url`` query parameter."""

    name = "bing_news_rss"

    def search(self, query: str) -> list[SearchHit]:
        try:
            parsed = _feed("https://www.bing.com/news/search", {"q": query, "format": "rss"}, self.timeout)
        except (httpx.HTTPError, ValueError):
            return []
        out = []
        for e in parsed.entries[: self.max_results]:
            link = e.get("link") or ""
            real = parse_qs(urlparse(link).query).get("url", [None])[0]
            target = real or link
            source = e.get("news_source") or e.get("source", {}).get("title") if isinstance(e.get("source"), dict) else e.get("news_source")
            out.append(
                SearchHit(
                    normalize_ws(e.get("title", "")),
                    canonical_url(target),
                    str(source or domain_of(target)),
                    domain_of(target),
                    _date(e.get("published")),
                    _strip_html(e.get("summary", ""))[:400],
                    self.name,
                    bool(real),
                )
            )
        return out


class GoogleNewsRSSProvider(SearchProvider):
    """Google News RSS. Links are aggregator redirects, so hits are coverage signals (``direct=False``)."""

    name = "google_news_rss"

    def search(self, query: str) -> list[SearchHit]:
        try:
            parsed = _feed("https://news.google.com/rss/search", {"q": f"{query} when:30d", "hl": "en-US", "gl": "US", "ceid": "US:en"}, self.timeout)
        except (httpx.HTTPError, ValueError):
            return []
        out = []
        for e in parsed.entries[: self.max_results * 2]:
            src = e.get("source") if isinstance(e.get("source"), dict) else {}
            publisher = src.get("title") or ""
            home = src.get("href") or ""
            title = normalize_ws(e.get("title", ""))
            if publisher and title.endswith(" - " + publisher):
                title = title[: -len(publisher) - 3]
            out.append(SearchHit(title, e.get("link") or "", publisher, domain_of(home) if home else "", _date(e.get("published")), "", self.name, False))
        return out


PROVIDERS: dict[str, type[SearchProvider]] = {
    "site_feeds": SiteFeedsProvider,
    "bing_news_rss": BingNewsRSSProvider,
    "google_news_rss": GoogleNewsRSSProvider,
}


def get_search_providers(settings: Any) -> list[SearchProvider]:
    names = settings.get("providers.search.providers", list(PROVIDERS)) or []
    return [PROVIDERS[n](settings) for n in names if n in PROVIDERS]
