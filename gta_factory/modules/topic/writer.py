"""The narration of a comparison or a top list: hook, one section per item, verdict, closing question.

Same discipline as the news writer (``script_generation.writer``): only facts from the
fact sheet, attribution for anything not CONFIRMED, verified quotes only, then the
deterministic lint, the standards-editor audit and one revision. The facts about the
other game of a comparison come from its facts file (the released game) and may be
stated plainly.
"""
from __future__ import annotations

from typing import Any, Callable

from ...app.core.utils import normalize_ws
from ...providers.llm import LLMProvider
from ..script_generation.writer import AUDIT_PROMPT, AUDIT_SYSTEM, WRITER_SYSTEM, build_fact_sheet, lint_script, split_sentences, word_count

PROMPT = """Today's date: {today}
Channel: {channel}
Video: {kind}. Topic: {topic}
Working title: {working_title}
Thesis: {thesis}
Opening idea: {hook_idea}
Closing idea: {verdict_idea}
Total narration budget: {words} words (stay within {min_words}-{max_words}).

GTA 6 FACT SHEET - fact-checked; the only GTA 6 facts you may use
{fact_sheet}
{game_block}
VERIFIED QUOTES (verbatim; optional to use)
{quotes}

SECTIONS TO WRITE, in this order (word budget per section in brackets)
{sections}

How to write an item section
{item_rules}

Return a JSON object:
"title": final title (max 70 characters, accurate)
"sections": list in the same order, each {{"id": section id, "narration": the spoken text for that section, "claim_ids": [ids of every fact used in it{id_note}]}}
{revision}"""

ITEM_RULES = {
    "versus": (
        "- The item title is shown on screen as the section starts: open with a short spoken line that names the system naturally.\n"
        "- Then say how {other} did it, naming {other} or its main character in that sentence, using only its facts (G ids), plainly and in the past tense.\n"
        "- Then say what GTA 6 does, naming GTA 6, Jason, Lucia or Leonida in that sentence. Attribute every GTA 6 claim that is not CONFIRMED (\"IGN's preview says\", \"Rockstar's Rob Nelson told Edge\").\n"
        "- End the item on the real difference between the two games. Never claim a {other} detail that is not in its facts."
    ),
    "list": (
        "- The entry's number and title are shown on screen as the section starts: do not say the number; open with the entry itself, as advice or a statement.\n"
        "- Then the facts that make it worth it, each attributed when it is not CONFIRMED (\"according to IGN's preview\").\n"
        "- One practical tip or consequence per entry, from the facts. No filler between entries."
    ),
}
KIND = {"versus": "GTA 6 compared with {other}, system by system", "list": "a numbered top list ({n} entries)"}


def game_claims(game_facts: list[dict[str, Any]], other: dict[str, Any]) -> list[dict[str, Any]]:
    """The other game's facts in the shape of fact-checked claims (established: the released game)."""
    pub = f"{other['name']} ({str(other.get('released') or '')[:4] or 'released game'})".replace(" ()", "")
    return [
        {
            "id": f["id"], "claim": f["fact"], "type": "fact", "classification": "confirmed", "confidence": "high",
            "original_source": "", "original_publisher": pub, "attributed_to": "the released game", "basis": "official",
            "corroborating_sources": [], "contradicting_sources": [], "evidence": f["fact"], "evidence_verified": True,
            "reasons": ["Fact about a released game, from the operator's facts file."], "event_date": None,
            "is_old_news": False, "note": "", "importance": 1, "subject": f.get("about") or "", "game_fact": True,
        }
        for f in game_facts
    ]


def budgets(settings: Any, fmt: str, n_items: int) -> dict[str, int]:
    """Words per section: the length of the video follows the number of items."""
    vertical = settings.format(fmt).get("aspect") == "9:16"
    wpm = float(settings.get("script.words_per_minute", 165))
    per_item = float(settings.get("topic.seconds_per_item_short" if vertical else "topic.seconds_per_item_long", 9 if vertical else 42))
    intro = float(settings.get("topic.intro_seconds_short" if vertical else "topic.intro_seconds_long", 12 if vertical else 50))
    w = lambda s: max(4, int(round(s * wpm / 60)))  # noqa: E731
    return {"hook": w(intro * 0.42), "item": w(per_item), "verdict": w(intro * 0.4), "cta": w(intro * 0.18)}


def target_seconds(settings: Any, fmt: str, n_items: int) -> float:
    b = budgets(settings, fmt, n_items)
    wpm = float(settings.get("script.words_per_minute", 165))
    return round((b["hook"] + b["verdict"] + b["cta"] + n_items * b["item"]) / wpm * 60, 1)


def section_plan(outline: dict[str, Any], b: dict[str, int], other: dict[str, Any] | None, claims: dict[str, dict[str, Any]], scale: float = 1.0) -> list[dict[str, Any]]:
    style = outline["style"]
    oname = (other or {}).get("short", "")
    n = len(outline["items"])
    plan = [{"id": "hook", "label": "Intro", "words": b["hook"], "purpose": (
        f"Open on the most striking fact ({outline.get('hook_idea') or 'from the fact sheet'}). "
        + (f"Then say that the video puts GTA 6 and {oname} side by side, system by system." if style == "versus" else f"Then say what the {n} entries are about.")
        + " No greeting, no channel intro, no 'in this video'.")}]
    for it in outline["items"]:
        uses = ", ".join(f"{i} ({claims[i]['classification'].upper()})" for i in it["claim_ids"] if i in claims)
        if style == "versus":
            uses += "; " + oname + " facts: " + ", ".join(it.get("game_fact_ids") or [])
            purpose = f"Item {it['n']} of {n}: \"{it['title']}\". {oname}: {it.get('then')}. GTA 6: {it.get('now')}. Facts to use: {uses}."
        else:
            purpose = f"Entry {it['n']} of {n}: \"{it['title']}\"" + (f" ({it['kicker']})" if it.get("kicker") else "") + f". Facts to use: {uses}."
        plan.append({"id": f"item_{it['n']:02d}", "label": it["title"], "words": b["item"], "purpose": purpose, "item": it["n"]})
    plan.append({"id": "verdict", "label": "Verdict" if style == "versus" else "Wrap-up", "words": b["verdict"], "purpose": (
        f"The verdict: {outline.get('verdict_idea') or 'what GTA 6 keeps, changes and leaves out'}. Two or three sentences, from the facts, then what is still unknown."
        if style == "versus" else f"Wrap-up: {outline.get('verdict_idea') or 'the one thing to remember'}. Two or three sentences.")})
    plan.append({"id": "cta", "label": "Question", "words": b["cta"], "purpose": "One natural question for the comments tied to the topic. No begging for likes."})
    for p in plan:
        p["words"] = max(4, int(round(p["words"] * scale)))
    return plan


def write_topic_script(
    llm: LLMProvider,
    settings: Any,
    *,
    fmt: str,
    outline: dict[str, Any],
    fact_check: list[dict[str, Any]],
    game_facts: list[dict[str, Any]],
    other: dict[str, Any] | None,
    quotes: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    today: str,
    target: float | None = None,
    audit: bool = True,
    log: Callable[[str], None] = lambda m: None,
) -> dict[str, Any]:
    style = outline["style"]
    oname = (other or {}).get("short", "")
    wpm = float(settings.get("script.words_per_minute", 165))
    b = budgets(settings, fmt, len(outline["items"]))
    natural = target_seconds(settings, fmt, len(outline["items"]))
    scale = (float(target) / natural) if target else 1.0
    gclaims = game_claims(game_facts, other) if style == "versus" and other else []
    combined = fact_check + gclaims
    by_id = {c["id"]: c for c in combined}
    plan = section_plan(outline, b, other, by_id, scale)
    words = sum(p["words"] for p in plan)
    min_words, max_words = int(words * 0.86), int(words * 1.12)
    fact_sheet = build_fact_sheet(fact_check)
    game_block = ""
    if gclaims:
        game_block = (
            f"\n{other['name'].upper()} FACTS - the released game; state them plainly; the only {oname} facts you may use\n"
            + "\n".join(f"{f['id']} [{f['about']}] {f['fact']}" for f in game_facts)
            + "\n"
        )
    quotes_txt = "\n".join(f"- \"{q['text']}\" - {q['speaker']} (via {q['publisher']})" for q in quotes) or "(none - do not use direct quotations)"
    sections_txt = "\n".join(f"- {p['id']} [{p['words']} words]: {p['purpose']}" for p in plan)
    ids = [p["id"] for p in plan]
    allowed_text = " ".join([c["claim"] + " " + c.get("evidence", "") for c in combined] + [q["text"] for q in quotes] + [f"{t.get('date')} {t.get('event')}" for t in timeline] + [today, str(len(outline["items"]))])
    audit_sheet = fact_sheet + (game_block or "")
    tl_txt = "\n".join(f"- {t.get('date')}: {t.get('event')} (source: {t.get('source')})" for t in timeline) or "(none)"

    def validate(d: Any) -> str | None:
        secs = d.get("sections")
        if not isinstance(secs, list):
            return "sections must be a list"
        got = [s.get("id") for s in secs if isinstance(s, dict)]
        if got != ids:
            return f"sections must be exactly these ids in order: {ids}; got {got}"
        for s in secs:
            if not isinstance(s.get("narration"), str) or len(s["narration"].split()) < 4:
                return f"section {s.get('id')} has no narration"
        return None

    def call(revision: str) -> dict[str, Any]:
        data = llm.complete_json(
            WRITER_SYSTEM,
            PROMPT.format(
                today=today,
                channel=settings.get("channel.name"),
                kind=KIND[style].format(other=(other or {}).get("name", ""), n=len(outline["items"])),
                topic=outline["topic"],
                working_title=outline["working_title"],
                thesis=outline["thesis"],
                hook_idea=outline.get("hook_idea") or "-",
                verdict_idea=outline.get("verdict_idea") or "-",
                words=words,
                min_words=min_words,
                max_words=max_words,
                fact_sheet=fact_sheet,
                game_block=game_block,
                quotes=quotes_txt,
                sections=sections_txt,
                item_rules=ITEM_RULES[style].format(other=oname),
                id_note=f": GTA 6 claim ids and {oname} fact ids" if gclaims else "",
                revision=revision,
            ),
            max_tokens=12000,
            label="write_topic_script",
            required_keys=("title", "sections"),
            validate=validate,
        )
        for s in data["sections"]:
            s["narration"] = normalize_ws(s["narration"]).replace("—", ", ").replace(" - ", ", ")
            s["claim_ids"] = [i for i in (s.get("claim_ids") or []) if i in by_id]
        return data

    history: list[dict[str, Any]] = []
    script = call("")
    lint = lint_script(script["sections"], combined, quotes, allowed_text, min_words=min_words, max_words=max_words)
    audit_result: dict[str, Any] = {"verdict": "skipped", "issues": []}
    for round_no in range(2):
        audit_issues: list[dict[str, Any]] = []
        sentences = [(s["id"], sent) for s in script["sections"] for sent in split_sentences(s["narration"])]
        if audit:
            numbered = "\n".join(f"{i}. [{sid}] {sent}" for i, (sid, sent) in enumerate(sentences, 1))
            audit_result = llm.complete_json(
                AUDIT_SYSTEM,
                AUDIT_PROMPT.format(fact_sheet=audit_sheet, quotes=quotes_txt, context="(none)", timeline=tl_txt, today=today, sentences=numbered),
                max_tokens=9000,
                label=f"audit_topic_script_r{round_no + 1}",
                required_keys=("issues", "verdict"),
            )
            audit_issues = [i for i in audit_result.get("issues") or [] if isinstance(i, dict) and i.get("type") not in ("filler",)]
        history.append({"round": round_no + 1, "lint_issues": lint, "audit_issues": audit_issues, "words": word_count(" ".join(s["narration"] for s in script["sections"]))})
        if (not lint and not audit_issues) or round_no == 1:
            break
        log(f"script revision: {len(lint)} lint issue(s), {len(audit_issues)} audit issue(s)")
        problems = [f"- {i['detail']}" + (f" (section {i['section']})" if i.get("section") else "") for i in lint]
        for i in audit_issues:
            n = i.get("n")
            sent = sentences[n - 1][1] if isinstance(n, int) and 1 <= n <= len(sentences) else ""
            problems.append(f"- [{i.get('type')}] \"{sent[:140]}\": {i.get('detail')} Fix: {i.get('fix')}")
        previous = "\n".join(f"[{s['id']}] {s['narration']}" for s in script["sections"])
        script = call("\nREVISION REQUIRED. Your previous draft is below, followed by the problems the standards editor found. Rewrite the full script, fixing every problem and keeping what worked.\n\nPREVIOUS DRAFT\n" + previous + "\n\nPROBLEMS\n" + "\n".join(problems))
        lint = lint_script(script["sections"], combined, quotes, allowed_text, min_words=min_words, max_words=max_words)

    by_plan = {p["id"]: p for p in plan}
    items = {it["n"]: it for it in outline["items"]}
    for s in script["sections"]:
        p = by_plan[s["id"]]
        s["label"] = p["label"]
        s["purpose"] = p["purpose"]
        s["words"] = word_count(s["narration"])
        if p.get("item"):
            s["item"] = p["item"]
            # the item's own facts always count as used: the screen shows its status and source
            s["claim_ids"] = list(dict.fromkeys(s["claim_ids"] + items[p["item"]]["claim_ids"] + items[p["item"]].get("game_fact_ids", [])))
    total_words = sum(s["words"] for s in script["sections"])
    remaining_audit = history[-1]["audit_issues"] if history else []
    return {
        "kind": "topic",
        "style": style,
        "vs": (other or {}).get("id"),
        "title": normalize_ws(script["title"])[:100],
        "format": fmt,
        "language": "en",
        "target_seconds": round(words / wpm * 60, 1),
        "word_budget": {"target": words, "min": min_words, "max": max_words, "actual": total_words},
        "estimated_seconds": round(total_words / wpm * 60, 1),
        "sections": script["sections"],
        "items": outline["items"],
        "thumb_lines": outline.get("thumb_lines") or [],
        "quality": {
            "rounds": history,
            "remaining_lint_issues": lint,
            "remaining_audit_issues": remaining_audit,
            "audit_verdict": "pass" if not remaining_audit else audit_result.get("verdict", "revise"),
            "sentence_map": audit_result.get("sentences") or [],
        },
        "angle": {"thesis": outline["thesis"]},
    }


def script_markdown(script: dict[str, Any], facts: list[dict[str, Any]]) -> str:
    """script.md: each section with the facts it rests on (status and source of every one)."""
    by_id = {c["id"]: c for c in facts}
    label = {"confirmed": "CONFIRMÉ", "reported": "RAPPORTÉ", "rumor": "RUMEUR", "speculation": "SPÉCULATION"}
    kind = f"comparaison avec {script.get('vs')}" if script.get("style") == "versus" else "top liste"
    out = [f"# {script['title']}", "", f"- **Format :** {script['format']} ({kind})", f"- **Mots :** {script['word_budget']['actual']} (cible {script['word_budget']['target']})", f"- **Angle :** {(script.get('angle') or {}).get('thesis', '')}", ""]
    for s in script["sections"]:
        head = f"#{s['item']} {s['label']}" if s.get("item") else s["label"]
        out += [f"## {head}", "", s["narration"], ""]
        rows = []
        for i in s.get("claim_ids") or []:
            c = by_id.get(i)
            if not c:
                continue
            if c.get("game_fact"):
                rows.append(f"- {i} · jeu sorti ({c['original_publisher']}) : {c['claim']}")
            else:
                rows.append(f"- {i} · {label.get(c['classification'], c['classification'])} ({c['original_publisher']}) : {c['claim']}")
        if rows:
            out += ["_Faits utilisés :_", *rows, ""]
    return "\n".join(out)
