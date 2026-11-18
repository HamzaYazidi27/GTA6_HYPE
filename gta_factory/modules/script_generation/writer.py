"""Original script generation with built-in fact discipline.

Flow: write -> deterministic lint -> LLM audit against the fact-check -> one revision if needed.

The writer never sees raw article prose as something to paraphrase line by
line; it receives the fact-checked claims, the chosen angle and verified
context, and writes a narration *for the ear*.
"""
from __future__ import annotations

import re
from typing import Any

from ...app.core.utils import contains_quote, normalize_ws
from ...providers.llm import LLMProvider

SECTION_PLANS: dict[str, list[tuple[str, str, float]]] = {
    # (section id, purpose, share of the word budget)
    "long": [
        ("hook", "Open on the single most interesting VERIFIED point OF THE MAIN STORY (never a side story). No greeting, no channel intro, no 'in this video'.", 0.08),
        ("context", "What happened and why it matters right now. Orient a viewer who missed the news.", 0.13),
        ("story", "The main story in a logical order. One idea per sentence. Attribute every reported claim.", 0.27),
        ("evidence", "Where the information comes from and what is actually confirmed versus reported versus rumoured.", 0.15),
        ("analysis", "Original analysis: consequences for GTA 6, comparisons, what follows logically. Mark inference as inference.", 0.18),
        ("community", "Why players should care: what changes for them in practice.", 0.09),
        ("conclusion", "The two or three things to remember, plus what is still unknown.", 0.07),
        ("cta", "One natural question for the comments tied to the story. No begging for likes.", 0.03),
    ],
    "short": [
        ("hook", "The most striking verified fact OF THE MAIN STORY, in the first sentence: the thing the title promises. Never open on a side story. No greeting.", 0.22),
        ("story", "Deliver the title's promise with concrete facts and attribution: who said what, which tools, numbers or dates. Tight.", 0.48),
        ("payoff", "One concrete consequence for players, in plain words, built on the facts above. No abstract wrap-up line.", 0.22),
        ("cta", "One short question (12 words max) about the main story.", 0.08),
    ],
}
SECTION_PLANS["announcement"] = SECTION_PLANS["analysis"] = SECTION_PLANS["long"]
SECTION_PLANS["breaking"] = [
    ("hook", "The news itself, verified, in the first sentence.", 0.12),
    ("context", "What happened and when.", 0.16),
    ("story", "The details with attribution.", 0.32),
    ("evidence", "What is confirmed and what is not.", 0.16),
    ("analysis", "The immediate consequence for GTA 6 players.", 0.16),
    ("conclusion", "What to watch next.", 0.05),
    ("cta", "One natural question for the comments.", 0.03),
]
SECTION_PLANS["recap"] = [
    ("hook", "The biggest verified development of the week, stated plainly.", 0.07),
    ("context", "How the week fits into the road to launch.", 0.08),
    ("story", "Each story in turn, most important first, with attribution and a one-line 'why it matters'.", 0.45),
    ("evidence", "Scorecard: what is confirmed, reported, rumoured across the week's stories.", 0.13),
    ("analysis", "The pattern across the stories.", 0.14),
    ("community", "What players should do or watch for next week.", 0.07),
    ("conclusion", "Three takeaways.", 0.04),
    ("cta", "One question for the comments.", 0.02),
]
SECTION_PLANS["guide"] = [
    ("hook", "The practical promise of the guide, grounded in a verified fact.", 0.08),
    ("context", "What is known and from where, so viewers can trust the steps.", 0.12),
    ("story", "The explanation or steps in order. Concrete and specific.", 0.40),
    ("evidence", "Which parts are confirmed and which are provisional until hands-on verification.", 0.12),
    ("analysis", "Tips, trade-offs and common misunderstandings.", 0.16),
    ("community", "Who benefits most from this.", 0.06),
    ("conclusion", "Checklist recap.", 0.04),
    ("cta", "One question for the comments.", 0.02),
]

BANNED_PHRASES = [
    "in this video", "in today's video", "hey guys", "what's up guys", "welcome back", "without further ado", "let's dive in",
    "let's dive into", "buckle up", "smash that like", "smash the like", "hit that like", "don't forget to subscribe",
    "make sure to subscribe", "hit the bell", "stay tuned", "it's worth noting", "it is worth noting", "in conclusion",
    "as an ai", "game-changer", "game changer", "delve", "mind-blowing", "you won't believe", "in the ever-evolving", "let me know what you think in the comments below",
    # empty wrap-up lines: they sound like a conclusion and say nothing
    "innovation with tradition", "innovation and tradition", "tradition and innovation", "only time will tell", "time will tell",
    "remains to be seen", "shaping how future", "shaping the future", "embracing new technolog", "the series' legacy", "the future of gaming",
    "a new era", "raises the bar", "sets a new standard", "exciting times ahead",
]
SHORT_CTA_MAX_WORDS = 14
CERTAINTY_WORDS = re.compile(r"\b(confirmed|confirms|officially|official|it's official|verified fact|100%)\b", re.I)
HEDGE_WORDS = re.compile(r"\b(reportedly|report|reports|reported|according to|says|said|claims?|claimed|alleged|allegedly|rumou?r|rumou?red|leak|leaked|suggests?|appears?|seems?|could|may|might|if (that|this)|unconfirmed|not confirmed|hasn't confirmed|has not confirmed|estimate[sd]?|projects?|expect(s|ed)?|analyst|told|tells|per |writes|wrote|preview|interview)\b", re.I)

WRITER_SYSTEM = """You are the head writer of an independent YouTube channel covering Grand Theft Auto VI.
Voice: an experienced gaming-news commentator talking to fellow GTA fans - energetic, clear, natural, professional.
You write for the ear: short sentences, contractions, concrete nouns, smooth spoken transitions.

Non-negotiable editorial rules
- Use ONLY the facts in the fact sheet. Nothing from memory. If it is not in the fact sheet, it does not exist.
- CONFIRMED claims may be stated plainly. REPORTED claims need attribution in the same sentence ("IGN reports", "according to ...").
  RUMOR claims must be called a rumour or a leak and framed with caution. SPECULATION must be framed as an estimate or opinion and attributed.
- Never use the words "confirmed" or "official" for anything that is not CONFIRMED in the fact sheet.
- Direct quotations: only from the VERIFIED QUOTES list, word for word, with the speaker named. Otherwise paraphrase.
- Numbers and dates: only those in the fact sheet. Do not round them differently, do not compute new ones except simple day counts from today's date.
- Your analysis is welcome, but label it as your reading ("my read", "that suggests", "the logical consequence").
- Never rewrite an article sentence by sentence. Build your own structure around the chosen angle.
- One story per video: every sentence serves the MAIN STORY. Never open on, or wander into, a different news item, even a GTA one.
- The title is a promise. The narration must deliver it plainly: a title about "what Rockstar uses" names what Rockstar uses.
- Every sentence carries a concrete fact, name, number or consequence. No empty abstractions such as "this approach balances innovation with tradition" or "shaping the future".
- No greeting, no channel intro, no "in this video", no filler, no fake excitement, no repeated points, no engagement bait.
- Do not imitate any existing creator's catchphrases.
- Plain spoken text only: no markdown, no bullet symbols, no parentheses, no URLs, no emoji, no stage directions."""

WRITER_PROMPT = """Today's date: {today}
Channel: {channel}
Format: {format_label}. Total narration budget: {words} words (stay within {min_words}-{max_words}).
MAIN STORY: {headline}

EDITORIAL ANGLE (this is what makes the video original)
Working title: {working_title}
Thesis: {thesis}
Why it is original: {why_original}
Opening idea: {hook_idea}
Editor notes: {editor_notes}
{update_block}
FACT SHEET - the only facts you may use
{fact_sheet}

VERIFIED QUOTES (verbatim; optional to use)
{quotes}

ADDITIONAL VERIFIED CONTEXT
{context}

BACKGROUND TIMELINE (verified)
{timeline}

HONEST UNKNOWNS to acknowledge
{unknowns}

SECTIONS TO WRITE, in this order (word budget per section in brackets)
{sections}

Return a JSON object:
"title": final working title (max 70 characters, accurate)
"sections": list in the same order, each {{"id": section id, "narration": the spoken text for that section, "claim_ids": [ids of every claim used in it]}}
{revision}"""

AUDIT_SYSTEM = """You are a standards editor. You audit a narration script sentence by sentence against a fact sheet.
You are strict: a factual sentence that the fact sheet does not support is an issue, even if it is probably true."""

AUDIT_PROMPT = """FACT SHEET
{fact_sheet}

VERIFIED QUOTES
{quotes}

ADDITIONAL VERIFIED CONTEXT
{context}

BACKGROUND TIMELINE (verified)
{timeline}

Today's date: {today}

MAIN STORY: {headline}
VIDEO TITLE (the promise made to the viewer): {title}

SCRIPT (numbered sentences)
{sentences}

Return a JSON object:
"sentences": for EVERY sentence number: {{"n": number, "kind": "fact" | "attributed_report" | "rumor" | "analysis" | "context" | "transition" | "cta", "claim_ids": [supporting claim ids or []]}}
"issues": list of problems, each {{"n": sentence number, "type": "unsupported_fact" | "overstated_certainty" | "invented_quote" | "wrong_number_or_date" | "missing_attribution" | "repetition" | "filler" | "misleading" | "off_topic" | "title_not_delivered", "detail": what is wrong, "fix": how to fix it in one sentence}}
    "off_topic": the sentence is about a different story than the MAIN STORY (always an issue for sentence 1).
    "filler": the sentence carries no concrete fact, name, number or consequence (generic wrap-up, vague "this approach" lines).
    "title_not_delivered": the script never gives what the VIDEO TITLE promises (use n = the last sentence of the story).
Return "issues": [] if the script is clean. Do not flag clearly labelled analysis or simple day-count arithmetic from today's date.
"verdict": "pass" | "revise"
"""

_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"'”’]?\s+(?=[A-Z0-9\"'“‘$])")
_ABBR = re.compile(r"\b(Mr|Mrs|Ms|Dr|St|vs|No|Inc|Corp|Co|Ltd|U\.S|U\.K|e\.g|i\.e|Jr|Sr|approx)\.$")


def split_sentences(text: str) -> list[str]:
    """Sentence splitter tuned for narration (keeps abbreviations and decimals together)."""
    text = normalize_ws(text)
    if not text:
        return []
    parts, buf = [], ""
    for chunk in _SENT_SPLIT.split(text):
        cand = (buf + " " + chunk).strip() if buf else chunk
        if _ABBR.search(cand) or len(cand.split()) < 3:
            buf = cand
            continue
        parts.append(cand)
        buf = ""
    if buf:
        if parts:
            parts[-1] = parts[-1] + " " + buf
        else:
            parts.append(buf)
    return parts


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9$%'’-]+", text or ""))


def build_fact_sheet(fact_check: list[dict[str, Any]], new_ids: list[str] | None = None) -> str:
    lines = []
    for c in fact_check:
        tag = f"{c['classification'].upper()} / confidence {c['confidence']}"
        flags = []
        if new_ids is not None:
            flags.append("NEW" if c["id"] in new_ids else "ALREADY COVERED IN AN EARLIER VIDEO")
        if c.get("is_old_news"):
            flags.append("OLD NEWS")
        if c.get("relevance") == "core":
            flags.append("MAIN STORY")
        line = f"{c['id']} [{tag}{'; ' + ', '.join(flags) if flags else ''}] {c['claim']}\n    source: {c['original_publisher']}; attributed to: {c.get('attributed_to')}"
        also = sorted({s["publisher"] for s in c.get("corroborating_sources", [])})
        if also:
            line += f"; also reported by: {', '.join(also)}"
        if c.get("contradicting_sources"):
            line += f"; DISPUTED by: {', '.join(sorted({s['publisher'] for s in c['contradicting_sources']}))}"
        if c.get("note"):
            line += f"\n    note: {c['note']}"
        lines.append(line)
    return "\n".join(lines)


def _numbers(text: str) -> set[str]:
    out = set()
    for m in re.finditer(r"\$?\d[\d,]*(?:\.\d+)?", text or ""):
        n = m.group(0).replace(",", "").replace("$", "").rstrip(".")
        if n:
            out.add(n)
    return out


_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def lint_script(sections: list[dict[str, Any]], fact_check: list[dict[str, Any]], quotes: list[dict[str, Any]], allowed_text: str, *, min_words: int, max_words: int, short: bool = False) -> list[dict[str, Any]]:
    """Cheap deterministic checks. Returns issue dicts (empty list = clean)."""
    issues: list[dict[str, Any]] = []
    by_id = {c["id"]: c for c in fact_check}
    full = " ".join(s["narration"] for s in sections)
    total = word_count(full)
    if total < min_words or total > max_words:
        issues.append({"type": "length", "detail": f"Narration is {total} words; it must be between {min_words} and {max_words}.", "section": None})
    low = full.lower()
    for phrase in BANNED_PHRASES:
        if phrase in low:
            issues.append({"type": "banned_phrase", "detail": f"Remove the generic phrase '{phrase}'.", "section": None})
    if re.search(r"[\[\]\(\)\*#_]|https?://", full):
        issues.append({"type": "format", "detail": "Narration must be plain spoken text (no brackets, parentheses, markdown or URLs).", "section": None})
    allowed_numbers = _numbers(allowed_text) | {str(v) for v in _WORD_NUM.values()} | {"6", "5", "2026", "2027", "2025"}
    for s in sections:
        text = s["narration"]
        for m in re.finditer(r"[\"“]([^\"”]{12,})[\"”]", text):
            if len(m.group(1).split()) >= 4 and not any(contains_quote(q["text"], m.group(1)) or contains_quote(m.group(1), q["text"]) for q in quotes):
                issues.append({"type": "invented_quote", "section": s["id"], "detail": f"Quotation not in the verified quotes list: \"{m.group(1)[:90]}\". Paraphrase it or use a verified quote verbatim."})
        ids = [i for i in s.get("claim_ids") or [] if i in by_id]
        for sent in split_sentences(text):
            if CERTAINTY_WORDS.search(sent) and ids and not any(by_id[i]["classification"] == "confirmed" for i in ids):
                if not re.search(r"\b(not|n't|isn't|hasn't|no|un)\s*(been\s+)?(officially\s+)?(confirmed|official)", sent, re.I):
                    issues.append({"type": "overstated_certainty", "section": s["id"], "detail": f"Sentence uses certainty wording but the section cites no CONFIRMED claim: \"{sent[:120]}\""})
        for n in _numbers(text):
            if n not in allowed_numbers and len(n.replace(".", "")) >= 2:
                # tolerate day counts and small ordinals produced by simple arithmetic
                if n.isdigit() and int(n) <= 31:
                    continue
                issues.append({"type": "unverified_number", "section": s["id"], "detail": f"The number {n} does not appear in the fact sheet or verified context. Remove it or use the sourced figure."})
        rumor_ids = [i for i in ids if by_id[i]["classification"] in ("rumor", "speculation")]
        if rumor_ids and not HEDGE_WORDS.search(text):
            issues.append({"type": "missing_hedge", "section": s["id"], "detail": f"Section uses {', '.join(rumor_ids)} (rumour/speculation) without any cautious or attributing wording."})
    hook = sections[0]["narration"].strip().lower() if sections else ""
    if re.match(r"^(hey|hi|hello|what's up|welcome|so,? today|today we|today i)", hook):
        issues.append({"type": "weak_hook", "section": sections[0]["id"], "detail": "The opening is a greeting or generic intro. Start with the verified news itself."})
    if sections and sections[0]["id"] == "hook" and any(c.get("relevance") == "core" for c in fact_check):
        cited = [by_id[i] for i in sections[0].get("claim_ids") or [] if i in by_id]
        if not any(c.get("relevance") == "core" for c in cited):
            core = ", ".join(c["id"] for c in fact_check if c.get("relevance") == "core")
            issues.append({"type": "off_topic_hook", "section": "hook", "detail": f"The hook must open on the main story: build it on one of the core claims ({core}) and list that id in claim_ids."})
    cta = next((s for s in sections if s["id"] == "cta"), None)
    if cta and short and word_count(cta["narration"]) > SHORT_CTA_MAX_WORDS:
        issues.append({"type": "cta_too_long", "section": "cta", "detail": f"The question for the comments is {word_count(cta['narration'])} words; keep it to {SHORT_CTA_MAX_WORDS} or fewer."})
    return issues


def _format_plan(fmt: str) -> list[tuple[str, str, float]]:
    return SECTION_PLANS.get(fmt, SECTION_PLANS["long"])


def write_script(
    llm: LLMProvider,
    settings: Any,
    *,
    fmt: str,
    target_seconds: float,
    analysis: dict[str, Any],
    fact_check: list[dict[str, Any]],
    verification: dict[str, Any],
    story: dict[str, Any],
    timeline: list[dict[str, Any]],
    today: str,
    audit: bool = True,
    log=lambda m: None,
) -> dict[str, Any]:
    wpm = float(settings.get("script.words_per_minute", 165))
    words = int(round(target_seconds * wpm / 60))
    min_words, max_words = int(words * 0.86), int(words * 1.12)
    plan = _format_plan(fmt)
    short = fmt == "short" or settings.format(fmt).get("aspect") == "9:16"
    headline = analysis.get("story_headline") or story.get("working_title") or ""
    sections_txt = "\n".join(f"- {sid} [{int(round(words * share))} words]: {purpose}" for sid, purpose, share in plan)
    quotes = analysis.get("quotes") or []
    quotes_txt = "\n".join(f"- \"{q['text']}\" - {q['speaker']} (via {q['publisher']})" for q in quotes) or "(none - do not use direct quotations)"
    ctx = verification.get("additional_facts") or []
    ctx_txt = "\n".join(f"- {f['fact']} (source: {f['publisher']})" for f in ctx) or "(none)"
    tl_txt = "\n".join(f"- {t.get('date')}: {t.get('event')} (source: {t.get('source')})" for t in timeline) or "(none)"
    is_update = story.get("decision") == "produce_update"
    fact_sheet = build_fact_sheet(fact_check, story.get("new_claim_ids") if is_update else None)
    update_block = ""
    if is_update:
        prev = "; ".join(f"\"{s['headline']}\" (angle: {s.get('angle')})" for s in story.get("similar_stories", [])[:2])
        update_block = (
            f"\nTHIS IS A FOLLOW-UP VIDEO. The channel already covered: {prev}.\n"
            "Lead with the claims marked NEW. Claims marked ALREADY COVERED may only appear as a one-sentence recap, never as the substance.\n"
            "Say plainly that this is an update to earlier coverage. Do not reuse the earlier video's angle.\n"
        )
    angle = story.get("angle") or {}
    allowed_text = " ".join(
        [c["claim"] + " " + c.get("evidence", "") for c in fact_check]
        + [f["fact"] + " " + f.get("evidence", "") for f in ctx]
        + [q["text"] for q in quotes]
        + [f"{t.get('date')} {t.get('event')}" for t in timeline]
        + [today]
    )
    section_ids = [sid for sid, _, _ in plan]

    def validate(d: Any) -> str | None:
        secs = d.get("sections")
        if not isinstance(secs, list):
            return "sections must be a list"
        got = [s.get("id") for s in secs if isinstance(s, dict)]
        if got != section_ids:
            return f"sections must be exactly these ids in order: {section_ids}; got {got}"
        for s in secs:
            if not isinstance(s.get("narration"), str) or len(s["narration"].split()) < 6:
                return f"section {s.get('id')} has no narration"
        return None

    def call(revision: str) -> dict[str, Any]:
        data = llm.complete_json(
            WRITER_SYSTEM,
            WRITER_PROMPT.format(
                today=today,
                channel=settings.get("channel.name"),
                format_label=settings.format(fmt).get("label", fmt),
                headline=headline,
                words=words,
                min_words=min_words,
                max_words=max_words,
                working_title=story.get("working_title"),
                thesis=angle.get("thesis"),
                why_original=angle.get("why_original"),
                hook_idea=story.get("hook_idea"),
                editor_notes=(story.get("editor_notes") or "") + (" Risk to avoid: " + angle["risk"] if angle.get("risk") else ""),
                update_block=update_block,
                fact_sheet=fact_sheet,
                quotes=quotes_txt,
                context=ctx_txt,
                timeline=tl_txt,
                unknowns="\n".join(f"- {q}" for q in story.get("open_questions") or []) or "(none listed)",
                sections=sections_txt,
                revision=revision,
            ),
            max_tokens=9000,
            label="write_script",
            required_keys=("title", "sections"),
            validate=validate,
        )
        valid = {c["id"] for c in fact_check}
        for s in data["sections"]:
            s["narration"] = normalize_ws(s["narration"]).replace("—", ", ").replace(" - ", ", ")
            s["claim_ids"] = [i for i in (s.get("claim_ids") or []) if i in valid]
        return data

    history: list[dict[str, Any]] = []
    script = call("")
    lint = lint_script(script["sections"], fact_check, quotes, allowed_text, min_words=min_words, max_words=max_words, short=short)
    audit_result: dict[str, Any] = {"verdict": "skipped", "issues": []}
    for round_no in range(2):
        audit_issues: list[dict[str, Any]] = []
        sentences = [(s["id"], sent) for s in script["sections"] for sent in split_sentences(s["narration"])]
        if audit:
            numbered = "\n".join(f"{i}. [{sid}] {sent}" for i, (sid, sent) in enumerate(sentences, 1))
            audit_result = llm.complete_json(
                AUDIT_SYSTEM,
                AUDIT_PROMPT.format(fact_sheet=fact_sheet, quotes=quotes_txt, context=ctx_txt, timeline=tl_txt, today=today, sentences=numbered, headline=headline, title=script.get("title") or story.get("working_title") or ""),
                max_tokens=8000,
                label=f"audit_script_r{round_no + 1}",
                required_keys=("issues", "verdict"),
            )
            audit_issues = [i for i in audit_result.get("issues") or [] if isinstance(i, dict)]
        history.append({"round": round_no + 1, "lint_issues": lint, "audit_issues": audit_issues, "words": word_count(" ".join(s["narration"] for s in script["sections"]))})
        if not lint and not audit_issues:
            break
        if round_no == 1:
            break
        log(f"script revision: {len(lint)} lint issue(s), {len(audit_issues)} audit issue(s)")
        problems = [f"- {i['detail']}" + (f" (section {i['section']})" if i.get("section") else "") for i in lint]
        for i in audit_issues:
            n = i.get("n")
            sent = sentences[n - 1][1] if isinstance(n, int) and 1 <= n <= len(sentences) else ""
            problems.append(f"- [{i.get('type')}] \"{sent[:140]}\": {i.get('detail')} Fix: {i.get('fix')}")
        previous = "\n".join(f"[{s['id']}] {s['narration']}" for s in script["sections"])
        script = call("\nREVISION REQUIRED. Your previous draft is below, followed by the problems the standards editor found. Rewrite the full script, fixing every problem and keeping what worked.\n\nPREVIOUS DRAFT\n" + previous + "\n\nPROBLEMS\n" + "\n".join(problems))
        lint = lint_script(script["sections"], fact_check, quotes, allowed_text, min_words=min_words, max_words=max_words, short=short)

    labels = {sid: purpose for sid, purpose, _ in plan}
    for s in script["sections"]:
        s["label"] = s["id"].replace("_", " ").title()
        s["purpose"] = labels.get(s["id"], "")
        s["words"] = word_count(s["narration"])
    total_words = sum(s["words"] for s in script["sections"])
    remaining_audit = history[-1]["audit_issues"] if history else []
    return {
        "title": normalize_ws(script["title"])[:100],
        "format": fmt,
        "language": "en",
        "target_seconds": target_seconds,
        "word_budget": {"target": words, "min": min_words, "max": max_words, "actual": total_words},
        "estimated_seconds": round(total_words / wpm * 60, 1),
        "sections": script["sections"],
        "quality": {
            "rounds": history,
            "remaining_lint_issues": lint,
            "remaining_audit_issues": remaining_audit,
            "audit_verdict": "pass" if not remaining_audit else audit_result.get("verdict", "revise"),
            "sentence_map": audit_result.get("sentences") or [],
        },
        "angle": angle,
        "is_update": is_update,
    }


REWRITE_PROMPT = """You are revising ONE section of an existing narration script.

FACT SHEET - the only facts you may use
{fact_sheet}

VERIFIED QUOTES
{quotes}

FULL SCRIPT FOR CONTEXT (do not rewrite the other sections)
{script}

SECTION TO REWRITE: {section_id} (about {words} words)
Instruction from the producer: {instruction}

Return a JSON object: {{"narration": the new spoken text for this section, "claim_ids": [ids used]}}"""


def rewrite_section(llm: LLMProvider, *, script: dict[str, Any], section_id: str, instruction: str, fact_check: list[dict[str, Any]], quotes: list[dict[str, Any]]) -> dict[str, Any]:
    """Regenerate a single section (used by the dashboard's script editor)."""
    target = next((s for s in script["sections"] if s["id"] == section_id), None)
    if target is None:
        raise KeyError(f"unknown section '{section_id}'")
    data = llm.complete_json(
        WRITER_SYSTEM,
        REWRITE_PROMPT.format(
            fact_sheet=build_fact_sheet(fact_check),
            quotes="\n".join(f"- \"{q['text']}\" - {q['speaker']}" for q in quotes) or "(none)",
            script="\n".join(f"[{s['id']}] {s['narration']}" for s in script["sections"]),
            section_id=section_id,
            words=target.get("words") or word_count(target["narration"]),
            instruction=instruction or "Improve clarity and flow without adding facts.",
        ),
        max_tokens=2500,
        label="rewrite_section",
        required_keys=("narration",),
    )
    valid = {c["id"] for c in fact_check}
    target["narration"] = normalize_ws(data["narration"])
    target["claim_ids"] = [i for i in data.get("claim_ids") or [] if i in valid]
    target["words"] = word_count(target["narration"])
    script["word_budget"]["actual"] = sum(s["words"] for s in script["sections"])
    return script


def render_script_markdown(script: dict[str, Any], story: dict[str, Any], fact_check: list[dict[str, Any]]) -> str:
    by_id = {c["id"]: c for c in fact_check}
    out = [f"# {script['title']}", "", f"- **Format:** {script['format']}", f"- **Words:** {script['word_budget']['actual']} (target {script['word_budget']['target']})", f"- **Angle:** {script['angle'].get('thesis', '')}", ""]
    for s in script["sections"]:
        out += [f"## {s['label']}", "", s["narration"], ""]
        if s.get("claim_ids"):
            out.append("_Sources: " + "; ".join(f"{i} ({by_id[i]['classification']}, {by_id[i]['original_publisher']})" for i in s["claim_ids"] if i in by_id) + "_")
            out.append("")
    return "\n".join(out)
