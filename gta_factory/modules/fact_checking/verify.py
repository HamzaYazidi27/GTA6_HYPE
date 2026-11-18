"""Fact checking: cross-check every claim against corroborating coverage, then classify it.

The LLM only does the *reading* (does source X support, contradict or not mention
claim Y, and which sentence shows it). Evidence sentences are verified verbatim
against the source text. The final ``classification`` and ``confidence`` are
computed by deterministic rules from source tiers, so the model cannot talk a
rumour up to "confirmed".
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ...app.core.utils import contains_quote, normalize_ws, tokens
from ...providers.llm import LLMProvider
from ..article_extraction.extractor import Article

VERIFY_SYSTEM = """You are a meticulous fact-checker for a GTA VI news channel.
You compare claims with what other sources say. You judge only from the provided text, never from memory.
"supports" means the source states the same thing (or the official figure/date itself). A source that merely
repeats the same original outlet is still marked "supports" but with independent=false."""

VERIFY_PROMPT = """Today's date: {today}

CLAIMS TO CHECK
{claims}

KNOWN BACKGROUND TIMELINE (previously verified, for detecting old news)
{timeline}

CORROBORATING SOURCES
{sources}

Return a JSON object:
"checks": list with one item per claim id:
    "id": claim id
    "sources": list, one item for each corroborating source that mentions the claim's subject:
        "source_index": n (1-based, from CORROBORATING SOURCES)
        "verdict": "supports" | "contradicts" | "partially_supports"
        "evidence": sentence copied VERBATIM from that source (max 300 characters)
        "independent": true if the source has its own sourcing or cites an official statement/document; false if it only relays the same outlet as the claim
        "cites_official": true if the source explicitly attributes this to Rockstar, Take-Two, Sony, Microsoft or an official document/statement
    "is_old_news": true if the provided material shows this was already publicly known more than 10 days before today, else false
    "first_known_date": ISO date when it first became public according to the provided material, or null
    "note": one short sentence a script writer must know (nuance, caveat, wording to avoid); "" if none
"contradictions": list of {{"claim_ids": [...], "description": "...", "sources": [source indexes]}} for genuinely conflicting reporting; [] if none.
"additional_facts": up to 6 facts found ONLY in the corroborating sources that add context to THIS story (never other news from the same page):
    {{"fact": "...", "source_index": n, "evidence": verbatim sentence, "cites_official": true|false}}
"""


def _relevant_excerpt(article: Article, claim_tokens: set[str], max_words: int = 650) -> str:
    """Keep the paragraphs of a corroborating article that talk about the claims."""
    paras = [p for p in article.text.split("\n\n") if p.strip()]
    scored = sorted(((len(claim_tokens & set(tokens(p))), i, p) for i, p in enumerate(paras)), key=lambda x: (-x[0], x[1]))
    keep: list[tuple[int, str]] = []
    words = 0
    for score, i, p in scored:
        if score == 0 and keep:
            break
        n = len(p.split())
        if words + n > max_words and keep:
            continue
        keep.append((i, p))
        words += n
    keep.sort()
    return "\n".join(p for _, p in keep)


def verify_claims(
    llm: LLMProvider,
    analysis: dict[str, Any],
    corroborating: list[Article],
    timeline: list[dict[str, Any]],
    today: str,
) -> dict[str, Any]:
    claims = analysis["claims"]
    if not corroborating:
        return {"checks": [{"id": c["id"], "sources": [], "is_old_news": False, "first_known_date": None, "note": ""} for c in claims], "contradictions": [], "additional_facts": []}
    claim_tokens = set(tokens(" ".join(c["claim"] for c in claims)))
    sources = "\n\n".join(
        f"[SOURCE {i}] {a.publisher} ({a.publisher_domain}; tier: {a.source_tier}); published {a.published_at or 'not stated'}\nTitle: {a.title}\n{_relevant_excerpt(a, claim_tokens)}"
        for i, a in enumerate(corroborating, 1)
    )
    claim_lines = "\n".join(f"{c['id']}: {c['claim']} (from {c['original_publisher']}; attributed to {c['attributed_to']}; basis {c['basis']})" for c in claims)
    tl = "\n".join(f"- {t.get('date')}: {t.get('event')}" for t in timeline) or "(none)"
    data = llm.complete_json(
        VERIFY_SYSTEM,
        VERIFY_PROMPT.format(today=today, claims=claim_lines, timeline=tl, sources=sources),
        max_tokens=7000,
        label="verify_claims",
        required_keys=("checks",),
        validate=lambda d: None if isinstance(d.get("checks"), list) else "checks must be a list",
    )
    # verbatim verification of every evidence sentence
    for chk in data.get("checks", []):
        clean = []
        for s in chk.get("sources") or []:
            idx = s.get("source_index")
            if not isinstance(idx, int) or not 1 <= idx <= len(corroborating):
                continue
            art = corroborating[idx - 1]
            ev = normalize_ws(str(s.get("evidence") or ""))
            s["evidence_verified"] = contains_quote(art.text, ev, min_len=20)
            s["url"], s["publisher"], s["tier"] = art.url, art.publisher, art.source_tier
            if s["evidence_verified"]:
                clean.append(s)
        chk["sources"] = clean
    facts = []
    for f in data.get("additional_facts") or []:
        idx = f.get("source_index")
        if isinstance(idx, int) and 1 <= idx <= len(corroborating) and contains_quote(corroborating[idx - 1].text, str(f.get("evidence") or ""), min_len=20):
            art = corroborating[idx - 1]
            facts.append({"fact": normalize_ws(f.get("fact", "")), "evidence": normalize_ws(f["evidence"]), "source_url": art.url, "publisher": art.publisher, "tier": art.source_tier, "cites_official": bool(f.get("cites_official"))})
    data["additional_facts"] = facts
    return data


def classify(claim: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    """Deterministic classification + confidence for one claim."""
    sources = check.get("sources") or []
    supports = [s for s in sources if s.get("verdict") in ("supports", "partially_supports")]
    contradicts = [s for s in sources if s.get("verdict") == "contradicts"]
    primary_support = [s for s in supports if s.get("tier") == "primary"]
    secondary_support = [s for s in supports if s.get("tier") == "secondary"]
    independent = [s for s in supports if s.get("independent")]
    official_citers = [s for s in supports if s.get("cites_official") and s.get("tier") in ("primary", "secondary")]
    basis, ctype = claim.get("basis"), claim.get("type")
    from_primary = claim.get("original_tier") == "primary"
    reputable_origin = claim.get("original_tier") in ("primary", "secondary")
    reasons: list[str] = []

    if ctype in ("opinion", "prediction") or basis == "analysis":
        classification = "speculation"
        reasons.append("Analysis, estimate or opinion rather than a statement of fact.")
    elif from_primary or primary_support:
        classification = "confirmed"
        reasons.append("Stated by an official primary source." if from_primary else "Corroborated by an official primary source.")
    elif basis == "official" and reputable_origin and (len(official_citers) >= 1 or len(secondary_support) >= 2):
        classification = "confirmed"
        reasons.append("Official statement reported consistently by multiple reputable outlets (primary document not retrieved).")
    elif basis == "anonymous_leak":
        classification = "rumor"
        reasons.append("Rests on unnamed sources or leaked material.")
    elif basis == "official" and reputable_origin:
        classification = "reported"
        reasons.append("An outlet reports an official statement, but no second source or primary document was retrieved.")
    elif reputable_origin or secondary_support:
        classification = "reported"
        reasons.append("Reported by a reputable outlet; not officially confirmed.")
    else:
        classification = "rumor"
        reasons.append("Only found in sources outside the reputable-outlet list.")

    if not claim.get("evidence_verified"):
        reasons.append("The supporting passage could not be matched verbatim in the original article.")
    if contradicts:
        reasons.append(f"Contradicted by {', '.join(sorted({s['publisher'] for s in contradicts}))}.")

    if classification == "confirmed" and not contradicts and claim.get("evidence_verified"):
        confidence = "high"
    elif classification == "confirmed":
        confidence = "medium"
    elif classification == "reported" and not contradicts and claim.get("evidence_verified") and (len(independent) >= 1 or len(secondary_support) >= 2):
        confidence = "high" if len(secondary_support) >= 2 and len(independent) >= 1 else "medium"
    elif classification == "reported" and not contradicts and claim.get("evidence_verified"):
        confidence = "medium"
    elif classification == "speculation":
        confidence = "medium" if reputable_origin and claim.get("evidence_verified") else "low"
    else:
        confidence = "low"
    if contradicts and confidence == "high":
        confidence = "medium"

    return {
        "id": claim["id"],
        "claim": claim["claim"],
        "type": ctype,
        "original_source": claim["original_source"],
        "original_publisher": claim["original_publisher"],
        "attributed_to": claim.get("attributed_to"),
        "basis": basis,
        "corroborating_sources": [{"url": s["url"], "publisher": s["publisher"], "tier": s["tier"], "verdict": s["verdict"], "evidence": s["evidence"], "independent": bool(s.get("independent"))} for s in supports],
        "contradicting_sources": [{"url": s["url"], "publisher": s["publisher"], "tier": s["tier"], "evidence": s["evidence"]} for s in contradicts],
        "evidence": claim.get("evidence") or "",
        "evidence_verified": bool(claim.get("evidence_verified")),
        "confidence": confidence,
        "classification": classification,
        "reasons": reasons,
        "event_date": claim.get("event_date"),
        "is_old_news": bool(check.get("is_old_news")),
        "first_known_date": check.get("first_known_date"),
        "note": check.get("note") or "",
        "importance": claim.get("importance", 3),
        **({"relevance": claim["relevance"]} if claim.get("relevance") else {}),
    }


def build_fact_check(analysis: dict[str, Any], verification: dict[str, Any]) -> list[dict[str, Any]]:
    checks = {c.get("id"): c for c in verification.get("checks", [])}
    return [classify(c, checks.get(c["id"], {})) for c in analysis["claims"]]


def days_since(iso: str | None, today: str) -> int | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            d = date.fromisoformat(iso[:10])
        except ValueError:
            return None
    return (date.fromisoformat(today[:10]) - d).days


LABEL = {"confirmed": "CONFIRMED", "reported": "REPORTED", "rumor": "RUMOR", "speculation": "SPECULATION"}


def render_fact_check_report(headline: str, fact_check: list[dict[str, Any]], verification: dict[str, Any], articles: list[Article], corroborating: list[Article], today: str) -> str:
    counts = {k: sum(1 for c in fact_check if c["classification"] == k) for k in LABEL}
    out = [f"# Fact-check report", "", f"**Story:** {headline}", f"**Checked on:** {today}", ""]
    out += ["## Verdict summary", "", "| Classification | Claims |", "|---|---|"] + [f"| {LABEL[k]} | {v} |" for k, v in counts.items()] + [""]
    out += ["## Sources read", ""]
    for a in articles:
        out.append(f"- **Input** - {a.publisher} ({a.source_tier}) - [{a.title}]({a.url}) - published {a.published_at or 'not stated'}")
    for a in corroborating:
        out.append(f"- Corroboration - {a.publisher} ({a.source_tier}) - [{a.title}]({a.url}) - published {a.published_at or 'not stated'}")
    out += ["", "## Claims", ""]
    for c in fact_check:
        out += [f"### {c['id']} - {LABEL[c['classification']]} (confidence: {c['confidence']})", "", c["claim"], ""]
        out.append(f"- **Original source:** {c['original_publisher']} - {c['original_source']}")
        out.append(f"- **Attributed to:** {c.get('attributed_to') or 'n/a'} (basis: {c.get('basis')})")
        if c["evidence"]:
            out.append(f"- **Evidence (verbatim):** \"{c['evidence']}\"")
        else:
            out.append("- **Evidence:** no verbatim passage could be matched in the source text")
        for s in c["corroborating_sources"]:
            out.append(f"- **{s['verdict'].replace('_', ' ').capitalize()}:** {s['publisher']} ({s['tier']}) - \"{s['evidence']}\" - {s['url']}")
        for s in c["contradicting_sources"]:
            out.append(f"- **Contradicts:** {s['publisher']} ({s['tier']}) - \"{s['evidence']}\" - {s['url']}")
        if not c["corroborating_sources"] and not c["contradicting_sources"]:
            out.append("- **Corroboration:** none found in the retrieved coverage")
        out.append(f"- **Why this label:** {' '.join(c['reasons'])}")
        if c["is_old_news"]:
            out.append(f"- **Old news:** first public around {c.get('first_known_date') or 'an earlier date'}")
        if c["note"]:
            out.append(f"- **Writer note:** {c['note']}")
        out.append("")
    contra = verification.get("contradictions") or []
    out += ["## Contradictory reporting", ""]
    out += [f"- {x.get('description')} (claims: {', '.join(x.get('claim_ids') or [])})" for x in contra] or ["None detected in the retrieved coverage."]
    out += ["", "## Additional verified context", ""]
    out += [f"- {f['fact']} - {f['publisher']} - \"{f['evidence']}\"" for f in verification.get("additional_facts") or []] or ["None."]
    out += ["", "---", "Classification and confidence are computed by fixed rules from source tiers; evidence passages are matched verbatim against the fetched text."]
    return "\n".join(out) + "\n"
