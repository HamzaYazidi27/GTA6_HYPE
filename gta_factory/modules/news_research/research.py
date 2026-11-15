"""News intelligence, stage 1: understand the submitted articles and gather corroborating coverage.

``analyze_sources`` asks the LLM for a structured reading of the input
articles (claims, entities, dates, quotes) and then *verifies every evidence
quote against the article text*. A claim whose evidence cannot be found
verbatim is kept but marked, and its quote is discarded, so an invented
quotation can never travel further down the pipeline.

``gather_corroboration`` runs the search adapters, ranks candidates by source
tier and topical overlap and extracts the best ones as full articles.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from ...app.core.utils import canonical_url, contains_quote, domain_of, jaccard, normalize_ws, tokens
from ...providers.llm import LLMProvider
from ...providers.search import SearchHit, get_search_providers
from ..article_extraction.extractor import Article, extract_article

ANALYST_SYSTEM = """You are the research desk of a video-game news channel that covers Grand Theft Auto VI.
You read source articles and produce a precise, sceptical, structured reading of what they actually say.
You never add facts from memory. You never upgrade a rumour into a fact. When an article relays another
outlet's reporting, you attribute the claim to the original outlet. Quotes must be copied character for character."""

ANALYZE_PROMPT = """Today's date: {today}

SOURCE ARTICLES
{articles}

Produce a JSON object with exactly these keys:

"story_headline": neutral one-line description of the story (max 110 characters, no hype).
"summary": 3-5 sentences, plain language, what the sources establish and what remains unconfirmed.
"topic_tags": 3-8 short lowercase tags (e.g. "release date", "pre-orders", "gameplay", "take-two earnings").
"claims": list of the 6-14 most important distinct claims. For each:
    "id": "C1", "C2", ...
    "claim": one self-contained factual sentence in your own words (include the number/date if there is one).
    "type": "fact" | "number" | "date" | "quote" | "plan" | "opinion" | "prediction"
    "source_index": number of the article it comes from (1-based)
    "evidence": a sentence or passage copied VERBATIM from that article that supports the claim (max 320 characters)
    "attributed_to": who the article says is the origin (e.g. "Rockstar Games", "Take-Two CEO Strauss Zelnick", "The Guardian preview", "unnamed leaker", "the article's author")
    "basis": "official" (statement/document from Rockstar, Take-Two or a platform holder) |
             "named_source" (on-record person or named publication's first-hand reporting) |
             "relayed_report" (the article repeats another outlet or social post) |
             "anonymous_leak" (unnamed sources, leaks, datamines) |
             "analysis" (analyst estimate, author's interpretation or opinion)
    "event_date": ISO date the claim is about if stated or clearly implied, else null
    "importance": 1-5 (5 = the core of the story for GTA players)
"quotes": list of up to 6 notable direct quotations: {{"text": verbatim quote without the quote marks, "speaker": name and role, "source_index": n}}.
    Only quotes that appear inside quotation marks in the article. If there are none, return [].
"entities": {{"people": [], "organizations": [], "games": [], "places": [], "products": []}}
"key_dates": list of {{"date": ISO date or null, "label": what happens / happened, "kind": "past" | "upcoming"}}
"search_queries": 3-4 short web-news search queries that would find independent or official confirmation of the core claims.
"sensitivities": list of strings flagging anything that needs care on YouTube (leaked material, unverified numbers, mature themes); [] if none.
"""


def _article_block(articles: list[Article], max_words: int = 1800) -> str:
    blocks = []
    for i, a in enumerate(articles, 1):
        words = a.text.split()
        body = " ".join(words[:max_words]) + (" [...]" if len(words) > max_words else "")
        blocks.append(
            f"[ARTICLE {i}]\nPublisher: {a.publisher} ({a.publisher_domain}; tier: {a.source_tier})\n"
            f"Title: {a.title}\nAuthor: {a.author or 'not stated'}\nPublished: {a.published_at or 'not stated'}\nURL: {a.url}\n\n{body}"
        )
    return "\n\n".join(blocks)


def analyze_sources(llm: LLMProvider, articles: list[Article], today: str) -> dict[str, Any]:
    """LLM reading of the input articles with verbatim-evidence verification."""

    def validate(data: Any) -> str | None:
        if not isinstance(data, dict) or not isinstance(data.get("claims"), list) or len(data["claims"]) < 3:
            return "claims must be a list with at least 3 items"
        for c in data["claims"]:
            if not isinstance(c, dict) or not c.get("claim") or not c.get("id"):
                return "every claim needs id and claim"
            idx = c.get("source_index")
            if not isinstance(idx, int) or not 1 <= idx <= len(articles):
                return f"claim {c.get('id')} has an invalid source_index"
        return None

    data = llm.complete_json(
        ANALYST_SYSTEM,
        ANALYZE_PROMPT.format(today=today, articles=_article_block(articles)),
        max_tokens=7000,
        label="analyze_sources",
        required_keys=("story_headline", "summary", "claims", "entities", "search_queries"),
        validate=validate,
    )

    claims = []
    for n, c in enumerate(data["claims"], 1):
        art = articles[c["source_index"] - 1]
        evidence = normalize_ws(str(c.get("evidence") or ""))
        verified = contains_quote(art.text, evidence, min_len=20) if evidence else False
        if not verified and evidence:
            # the model sometimes stitches two sentences: accept when both halves are verbatim
            parts = [p for p in re.split(r"(?<=[.!?])\s+|\s*\[\.\.\.\]\s*|\s*\.\.\.\s*", evidence) if len(p) > 25]
            verified = bool(parts) and all(contains_quote(art.text, p, min_len=20) for p in parts)
        claims.append(
            {
                "id": f"C{n}",
                "claim": normalize_ws(c["claim"]),
                "type": c.get("type") or "fact",
                "original_source": art.url,
                "original_publisher": art.publisher,
                "original_tier": art.source_tier,
                "evidence": evidence if verified else "",
                "evidence_verified": verified,
                "attributed_to": c.get("attributed_to") or art.publisher,
                "basis": c.get("basis") or "relayed_report",
                "event_date": c.get("event_date"),
                "importance": int(c.get("importance") or 3),
            }
        )
    quotes = []
    for q in data.get("quotes") or []:
        idx = q.get("source_index")
        if not isinstance(idx, int) or not 1 <= idx <= len(articles):
            continue
        text = normalize_ws(str(q.get("text") or "")).strip('"“”')
        if contains_quote(articles[idx - 1].text, text, min_len=15):
            quotes.append({"text": text, "speaker": q.get("speaker") or "", "source_url": articles[idx - 1].url, "publisher": articles[idx - 1].publisher})
    data["claims"] = claims
    data["quotes"] = quotes
    data["dropped_unverifiable_quotes"] = len(data.get("quotes_raw", [])) if "quotes_raw" in data else None
    return data


def _rank(hit: SearchHit, claim_tokens: set[str], tier_of: Callable[[str], str], input_domains: set[str]) -> float:
    tier = tier_of(hit.domain)
    score = {"primary": 3.0, "secondary": 2.0, "other": 0.4}[tier]
    score += 4.0 * jaccard(tokens(hit.title + " " + hit.snippet), claim_tokens)
    if hit.domain in input_domains:
        score -= 1.2  # prefer independent outlets over more pages of the same site
    if not hit.direct:
        score -= 0.8
    return score


def gather_corroboration(
    settings: Any,
    analysis: dict[str, Any],
    articles: list[Article],
    *,
    log: Callable[[str], None] = lambda m: None,
) -> dict[str, Any]:
    """Search for other coverage and extract the strongest independent articles."""
    providers = get_search_providers(settings)
    input_urls = {canonical_url(a.url) for a in articles} | {canonical_url(a.final_url) for a in articles}
    input_domains = {a.publisher_domain for a in articles}
    claim_tokens = set(tokens(analysis.get("story_headline", "") + " " + " ".join(c["claim"] for c in analysis["claims"])))
    queries = [q for q in (analysis.get("search_queries") or []) if isinstance(q, str) and q.strip()][:4]
    if not queries:
        queries = [analysis.get("story_headline", "GTA 6")]

    seen: dict[str, SearchHit] = {}
    signals: list[SearchHit] = []
    for q in queries:
        for p in providers:
            try:
                hits = p.search(q)
            except Exception as exc:  # a broken feed must not stop the research
                log(f"search provider {p.name} failed for '{q}': {type(exc).__name__}: {exc}")
                continue
            for h in hits:
                if not h.direct:
                    signals.append(h)
                    continue
                key = canonical_url(h.url)
                if key in input_urls or key in seen:
                    continue
                seen[key] = h
    ranked = sorted(seen.values(), key=lambda h: -_rank(h, claim_tokens, settings.source_tier, input_domains))
    limit = int(settings.get("providers.search.max_corroborating_articles", 6))
    extracted: list[Article] = []
    failures: list[dict[str, str]] = []
    per_domain: dict[str, int] = {}
    for h in ranked:
        if len(extracted) >= limit:
            break
        if jaccard(tokens(h.title + " " + h.snippet), claim_tokens) < 0.04:
            continue
        if per_domain.get(h.domain, 0) >= 2:
            continue
        try:
            art = extract_article(h.url, tier_of=settings.source_tier, allow_render=False)
        except Exception as exc:
            failures.append({"url": h.url, "error": getattr(exc, "message", str(exc))[:200]})
            continue
        if art.word_count < 120:
            continue
        per_domain[h.domain] = per_domain.get(h.domain, 0) + 1
        extracted.append(art)
        log(f"corroborating source: {art.publisher} - {art.title[:80]}")

    # coverage signals: who else is reporting the story (aggregator links, not fetched)
    coverage: dict[str, dict[str, Any]] = {}
    for h in list(seen.values()) + signals:
        if not h.publisher:
            continue
        if jaccard(tokens(h.title), claim_tokens) < 0.05:
            continue
        entry = coverage.setdefault(h.publisher, {"publisher": h.publisher, "domain": h.domain, "tier": settings.source_tier(h.domain), "headlines": [], "earliest": h.published_at})
        if len(entry["headlines"]) < 2 and h.title not in entry["headlines"]:
            entry["headlines"].append(h.title)
        if h.published_at and (not entry["earliest"] or h.published_at < entry["earliest"]):
            entry["earliest"] = h.published_at
    return {
        "queries": queries,
        "providers": [p.name for p in providers],
        "candidates_found": len(seen),
        "articles": extracted,
        "extraction_failures": failures,
        "coverage_signals": sorted(coverage.values(), key=lambda c: ({"primary": 0, "secondary": 1, "other": 2}[c["tier"]], c["earliest"] or "9999")),
    }
