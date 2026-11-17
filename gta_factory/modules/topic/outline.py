"""The outline: which items the video covers, in which order, and what each one rests on.

The model proposes; deterministic rules keep it honest: every item must rest on at least
one fact-checked GTA 6 claim, every comparison item on at least one fact of the other
game's facts file, a claim leads at most one item, and titles are short and distinct.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from ...app.core.errors import FactoryError
from ...app.core.utils import normalize_ws
from ...providers.llm import LLMProvider

OUTLINE_SYSTEM = """You are the editor of an independent GTA VI channel. You plan comparison videos and top lists
that are specific, well ordered and honest: every item rests on facts from the fact sheet, never on memory."""

OUTLINE_PROMPT = """Today's date: {today}
VIDEO TOPIC: {topic}
{kind}

GTA 6 FACT SHEET (fact-checked; id [status] subject: claim (source))
{claims}
{game_block}
Plan exactly {count} items (fewer only if the facts cannot support {count} good ones).
{rules}

Return a JSON object:
"working_title": the video title (max 70 characters, accurate, no hype words)
"thesis": one sentence: what the video shows
"hook_idea": the opening idea, built on the most striking fact (cite its id)
"items": list in video order, each:
    "title": 2-4 words for the screen and the chapter list (e.g. "Weapon limit", "Steal a classic car")
    "claim_ids": ids of the GTA 6 facts this item uses (at least one; the first one is the main fact)
{item_extra}"verdict_idea": {verdict}
"thumb_lines": 1-3 thumbnail lines, max 12 characters each (e.g. {thumb_example})"""

KIND = {
    "versus": "Kind of video: GTA 6 compared with {other}, one system per item: what {other} did, then what GTA 6 does.",
    "list": "Kind of video: a numbered top list, one entry per item.",
}
RULES = {
    "versus": (
        "Rules\n- Each item pairs one GTA 6 system with how {other} handled the same thing (game_fact_ids from the {other} facts below).\n"
        "- Prefer systems where both sides have precise facts; skip a system the {other} facts do not cover.\n"
        "- Order: start with the most visible system, end with the most surprising one."
    ),
    "list": (
        "Rules\n- Each entry is concrete and useful (something to do, to know or to try), with a clear title in the imperative or as a noun.\n"
        "- Entries must not overlap. Order them in a logical sequence for the viewer; keep a strong entry for the end."
    ),
}
ITEM_EXTRA = {
    "versus": (
        '    "game_fact_ids": ids of the {other} facts this item uses (at least one)\n'
        '    "then": how {other} did it, max 6 words (e.g. "Two guns, rest on the horse")\n'
        '    "now": how GTA 6 does it, max 6 words (e.g. "Two guns, rest in the trunk")\n'
    ),
    "list": '    "kicker": what the entry gives the viewer, max 6 words (e.g. "Health and stamina boost")\n',
}
VERDICT = {
    "versus": '"one sentence: the overall conclusion of the comparison, from the facts"',
    "list": '"one sentence: the closing thought, from the facts"',
}


def _short(text: Any, n: int) -> str:
    t = normalize_ws(str(text or "")).strip(" .\"“”")
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0].rstrip(",;:-")


def plan_items(
    llm: LLMProvider,
    *,
    topic: str,
    style: str,
    count: int,
    fact_check: list[dict[str, Any]],
    game_facts: list[dict[str, Any]] | None,
    other: dict[str, Any] | None,
    today: str,
    log: Callable[[str], None] = lambda m: None,
) -> dict[str, Any]:
    """{"working_title", "thesis", "hook_idea", "verdict_idea", "thumb_lines", "items": [...]}."""
    claims = [c for c in fact_check if c.get("evidence_verified") or c["classification"] != "rumor"]
    by_id = {c["id"]: c for c in claims}
    facts = {f["id"]: f for f in game_facts or []}
    oname = (other or {}).get("short", "")
    claims_txt = "\n".join(f"{c['id']} [{c['classification'].upper()}] {c.get('subject') or '-'}: {c['claim']} ({c['original_publisher']})" for c in claims)
    game_block = ""
    if style == "versus":
        game_block = f"\n{(other or {}).get('name', '').upper()} FACTS (the released game; id [about]: fact)\n" + "\n".join(f"{f['id']} [{f['about']}]: {f['fact']}" for f in game_facts or []) + "\n"

    def validate(d: Any) -> str | None:
        items = d.get("items") if isinstance(d, dict) else None
        if not isinstance(items, list) or not items:
            return "items must be a non-empty list"
        good = [it for it in items if isinstance(it, dict) and str(it.get("title") or "").strip() and any(i in by_id for i in it.get("claim_ids") or [])]
        if len(good) < min(2, count):
            return "every item needs a title and at least one claim id from the fact sheet"
        if style == "versus" and not any(any(i in facts for i in it.get("game_fact_ids") or []) for it in good):
            return f"every item needs game_fact_ids from the {oname} facts"
        return None

    data = llm.complete_json(
        OUTLINE_SYSTEM,
        OUTLINE_PROMPT.format(
            today=today,
            topic=topic,
            kind=KIND[style].format(other=oname),
            claims=claims_txt,
            game_block=game_block,
            count=count,
            rules=RULES[style].format(other=oname),
            item_extra=ITEM_EXTRA[style].format(other=oname),
            verdict=VERDICT[style],
            thumb_example='["GTA 6", "VS ' + oname.upper()[:9] + '"]' if style == "versus" else '["12 THINGS", "TO DO FIRST"]',
        ),
        max_tokens=5000,
        label="topic_outline",
        required_keys=("items",),
        validate=validate,
    )
    items: list[dict[str, Any]] = []
    lead_used: set[str] = set()
    titles: set[str] = set()
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        title = _short(raw.get("title"), 34)
        ids = list(dict.fromkeys(i for i in raw.get("claim_ids") or [] if i in by_id))
        gids = list(dict.fromkeys(i for i in raw.get("game_fact_ids") or [] if i in facts))
        why = ""
        if not title:
            why = "no title"
        elif not ids:
            why = "no fact-checked GTA 6 claim"
        elif style == "versus" and not gids:
            why = f"no {oname} fact"
        elif ids[0] in lead_used:
            why = f"its main claim {ids[0]} already leads another item"
        elif title.lower() in titles:
            why = "same title as another item"
        if why:
            log(f"outline: item '{title or '?'}' dropped ({why})")
            continue
        lead_used.add(ids[0])
        titles.add(title.lower())
        item = {"n": len(items) + 1, "title": title, "claim_ids": ids, "subject": by_id[ids[0]].get("subject") or ""}
        if style == "versus":
            item.update(game_fact_ids=gids, then=_short(raw.get("then"), 40) or _short(facts[gids[0]]["about"], 40), now=_short(raw.get("now"), 40) or _short(item["subject"] or title, 40))
        else:
            item["kicker"] = _short(raw.get("kicker"), 40)
        items.append(item)
        if len(items) >= count:
            break
    if len(items) < 2:
        raise FactoryError(
            f"Only {len(items)} item(s) could be built from the sources ({len(claims)} usable claim(s)).",
            hint="Add sources with more detail (--source URL: previews, interviews), or ask for fewer items (--count).",
        )
    if len(items) < count:
        log(f"outline: {len(items)} item(s) instead of {count} (not enough distinct facts in the sources)")
    status_rank = {"rumor": 0, "speculation": 1, "reported": 2, "confirmed": 3}
    for it in items:  # the most cautious status among an item's claims is the one shown on screen
        it["status"] = min((by_id[i]["classification"] for i in it["claim_ids"]), key=lambda s: status_rank.get(s, 0))
    thumbs = [re.sub(r"\s+", " ", str(t)).strip().upper()[:12] for t in data.get("thumb_lines") or [] if str(t).strip()][:3]
    return {
        "style": style,
        "topic": topic,
        "vs": (other or {}).get("id"),
        "working_title": _short(data.get("working_title"), 90) or topic,
        "thesis": _short(data.get("thesis"), 200) or topic,
        "hook_idea": _short(data.get("hook_idea"), 200),
        "verdict_idea": _short(data.get("verdict_idea"), 200),
        "thumb_lines": thumbs,
        "items": items,
    }
