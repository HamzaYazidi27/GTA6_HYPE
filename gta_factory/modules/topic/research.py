"""Topic research: read the operator's sources, find more with the news search, extract every useful GTA 6 claim.

Claims follow exactly the news pipeline's discipline (``news_research.verify_evidence``):
every claim needs a passage copied verbatim from its article, quotes are kept only when
they are found word for word, and the fact-check that follows classifies each claim
with fixed rules. A topic video is made from several articles at once, so each article
corroborates the others (a source never corroborates itself).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from ...app.core.errors import ExtractionError
from ...app.core.utils import canonical_url, jaccard, normalize_ws, tokens
from ...providers.llm import LLMProvider
from ...providers.search import get_search_providers
from ..article_extraction.extractor import Article, article_from_text, extract_article
from ..news_research.research import ANALYST_SYSTEM, _article_block, verify_evidence

TOPIC_PROMPT = """Today's date: {today}

VIDEO TOPIC: {topic}
{style_line}

SOURCE ARTICLES
{articles}

Read the articles for everything they establish about GTA 6 (Grand Theft Auto VI) that is useful for this video:
systems, mechanics, features, activities, characters, places, numbers, dates and what Rockstar staff said.
Ignore what is not about GTA 6 (other games are covered separately).

Produce a JSON object with exactly these keys:

"headline": neutral one-line description of what the sources establish about the topic (max 110 characters, no hype).
"claims": list of the {lo}-{hi} most useful distinct claims. For each:
    "id": "C1", "C2", ...
    "subject": the system or feature it is about, 1-4 words (e.g. "weapon limit", "wanted system", "gym", "fuel")
    "claim": one self-contained factual sentence in your own words (include the number/date if there is one).
    "type": "fact" | "number" | "date" | "quote" | "plan" | "opinion" | "prediction"
    "source_index": number of the article it comes from (1-based)
    "evidence": a sentence or passage copied VERBATIM from that article that supports the claim (max 320 characters)
    "attributed_to": who the article says is the origin (e.g. "Rockstar Games", "Rob Nelson, Rockstar North, to IGN", "IGN preview", "unnamed leaker")
    "basis": "official" (statement/document from Rockstar, Take-Two or a platform holder, or a Rockstar developer on record) |
             "named_source" (a named publication's first-hand reporting, e.g. a hands-on preview) |
             "relayed_report" (the article repeats another outlet or social post) |
             "anonymous_leak" (unnamed sources, leaked footage, datamines) |
             "analysis" (estimate, interpretation or opinion)
    "event_date": ISO date the claim is about if stated, else null
    "importance": 1-5 (5 = central to this video's topic)
"quotes": up to 6 notable direct quotations about the topic: {{"text": verbatim quote without the quote marks, "speaker": name and role, "source_index": n}}.
    Only quotes that appear inside quotation marks in the article. [] if none.
"""

STYLE_LINE = {
    "versus": "Kind of video: a comparison of GTA 6 with {other}, system by system. Collect every GTA 6 system or feature the sources describe precisely.",
    "list": "Kind of video: a numbered list ({count} entries). Collect every GTA 6 fact that could support an entry of this list.",
}


def _topic_tokens(topic: str, extra: str = "") -> set[str]:
    return set(tokens(f"{topic} {extra} gta 6 grand theft auto vi"))


def search_queries(topic: str, style: str, other: dict[str, Any] | None) -> list[str]:
    qs = [topic]
    if style == "versus" and other:
        qs += [f"GTA 6 vs {other['short']}", f"GTA 6 {other['short']} systems"]
    qs += ["GTA 6 preview hands-on", "GTA 6 gameplay details Rockstar interview"]
    out: list[str] = []
    for q in qs:
        q = normalize_ws(q)
        if q and q.lower() not in (x.lower() for x in out):
            out.append(q)
    return out[:5]


def find_sources(
    settings: Any,
    *,
    topic: str,
    style: str,
    urls: list[str],
    text_files: dict[str, str] | None = None,
    other: dict[str, Any] | None = None,
    search: bool = True,
    log: Callable[[str], None] = lambda m: None,
) -> dict[str, Any]:
    """The operator's articles first, then the best ones the news search finds (reputable outlets first)."""
    text_files = text_files or {}
    limit = int(settings.get("topic.max_articles", 8))
    articles: list[Article] = []
    failures: list[dict[str, str]] = []
    seen: set[str] = set()
    for url in urls:
        try:
            if url in text_files:
                p = Path(text_files[url]).expanduser()
                if not p.exists():
                    raise ExtractionError(f"Text file for {url} not found: {p}")
                art = article_from_text(url, p.read_text(encoding="utf-8"), tier_of=settings.source_tier)
            else:
                art = extract_article(url, tier_of=settings.source_tier)
        except ExtractionError as exc:
            failures.append({"url": url, "error": exc.message, "hint": exc.hint})
            log(f"could not read {url}: {exc.message}")
            continue
        seen |= {canonical_url(art.url), canonical_url(art.final_url)}
        articles.append(art)
        log(f"source: {art.publisher} | {art.title[:90]} | {art.word_count} words")
    queries = search_queries(topic, style, other) if search else []
    found = 0
    if queries and len(articles) < limit:
        want = _topic_tokens(topic, (other or {}).get("short", ""))
        hits: dict[str, Any] = {}
        for q in queries:
            for p in get_search_providers(settings):
                try:
                    res = p.search(q)
                except Exception as exc:  # a broken feed must not stop the research
                    log(f"search provider {p.name} failed for '{q}': {type(exc).__name__}: {exc}")
                    continue
                for h in res:
                    key = canonical_url(h.url)
                    if h.direct and key not in seen and key not in hits:
                        hits[key] = h
        def rank(h: Any) -> float:
            tier = settings.source_tier(h.domain)
            return {"primary": 3.0, "secondary": 2.0, "other": 0.3}[tier] + 5.0 * jaccard(tokens(h.title + " " + h.snippet), want)
        per_domain: dict[str, int] = {}
        for h in sorted(hits.values(), key=lambda h: -rank(h)):
            if len(articles) >= limit:
                break
            text = f"{h.title} {h.snippet}".lower()
            if not re.search(r"\bgta ?(6|vi)\b|grand theft auto (6|vi)\b", text) or per_domain.get(h.domain, 0) >= 2:
                continue
            try:
                art = extract_article(h.url, tier_of=settings.source_tier, allow_render=False)
            except Exception as exc:
                failures.append({"url": h.url, "error": getattr(exc, "message", str(exc))[:200], "hint": ""})
                continue
            if art.word_count < 150:
                continue
            per_domain[h.domain] = per_domain.get(h.domain, 0) + 1
            articles.append(art)
            found += 1
            log(f"source found: {art.publisher} | {art.title[:90]}")
    return {"articles": articles, "failures": failures, "queries": queries, "found_by_search": found}


def analyze_topic(llm: LLMProvider, articles: list[Article], *, topic: str, style: str, other: dict[str, Any] | None, count: int, today: str) -> dict[str, Any]:
    """Claims about GTA 6 useful for the topic, each with verbatim evidence (or marked unverified)."""
    lo, hi = (12, 40) if count > 6 else (6, 18)

    def validate(data: Any) -> str | None:
        if not isinstance(data, dict) or not isinstance(data.get("claims"), list) or len(data["claims"]) < 2:
            return "claims must be a list with at least 2 items"
        for c in data["claims"]:
            if not isinstance(c, dict) or not c.get("claim"):
                return "every claim needs a claim sentence"
            idx = c.get("source_index")
            if not isinstance(idx, int) or not 1 <= idx <= len(articles):
                return f"claim {c.get('id')} has an invalid source_index"
        return None

    style_line = STYLE_LINE[style].format(other=(other or {}).get("name", "another game"), count=count)
    max_words = max(700, min(1800, 9000 // max(1, len(articles))))
    data = llm.complete_json(
        ANALYST_SYSTEM,
        TOPIC_PROMPT.format(today=today, topic=topic, style_line=style_line, articles=_article_block(articles, max_words=max_words), lo=lo, hi=hi),
        max_tokens=9000,
        label="analyze_topic",
        required_keys=("claims",),
        validate=validate,
    )
    data = verify_evidence(data, articles)
    data["story_headline"] = normalize_ws(str(data.get("headline") or topic))[:140]
    data["topic"] = topic
    return data


def drop_self_support(verification: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Every article is a corroborating source for the others: an article never confirms its own claims."""
    origin = {c["id"]: canonical_url(c["original_source"]) for c in claims}
    for chk in verification.get("checks") or []:
        own = origin.get(chk.get("id"))
        chk["sources"] = [s for s in chk.get("sources") or [] if canonical_url(str(s.get("url") or "")) != own]
    return verification
