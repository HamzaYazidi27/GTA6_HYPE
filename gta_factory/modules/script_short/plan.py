"""Script understanding: a pasted script becomes an editing plan of visual beats.

The narration is never rewritten. Python cuts the words into beats (a sentence, or a clause that
names another thing to show: "race jet skis, | or paddle a kayak."), finds the list numbers, the
hook, the "number one" teaser and the call to action. Every word belongs to exactly one beat, in
order, so the voice, the captions and the cuts can never drift from the script.

The model (one short call; a local 14B model answers in about a minute) then only says what each
beat must SHOW:

* ``concept``: what must be on screen, in plain words ("a jet ski racing on the ocean")
* ``prompts``: 1-3 picture descriptions for the frame search (CLIP)
* ``distractors``: look-alike scenes that must NOT be taken for it ("a motorboat", "a sea turtle")
* ``game``: gta6, or gta5 for a comparison shot ("GTA 5 stopped at five")
* ``specific``: True when only the right footage can show it; False for hooks, teasers and the CTA
* ``claim``: the factual statement about the game the beat makes, if any (fact-checked)

When the model is missing or fails, the rule plan is used as it is (``method: rules``).
"""
from __future__ import annotations

import re
from typing import Any

from ...app.core.utils import normalize_ws

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}
_LEAD_NUMBER = re.compile(r"^(?:number\s+)?(#?\d{1,2}|" + "|".join(NUMBER_WORDS) + r")\s*(?:[:.)–—,](?!\d)|-(?!\w))", re.I)  # "Six:" yes, "Six-star" no
_TEASE = re.compile(r"\bnumber\s+(" + "|".join(NUMBER_WORDS) + r"|\d{1,2})\b", re.I)
_CTA = re.compile(r"\b(comment|subscribe|let me know|tell me|which one|follow for|like if|drop a)\b", re.I)
_SENT = re.compile(r"(?<=[.!?])\s+(?=\S)")
ROLES = ("hook", "number", "item", "claim", "comparison", "suspense", "cta")
GENERIC_DISTRACTORS = [  # the usual scenes of GTA footage: a specific beat must look more like its concept than like any of these
    "a car driving down a city street", "people talking indoors", "a close-up portrait of a character", "a city skyline",
    "a beach with palm trees", "a boat on the water", "an underwater scene with fish", "a game menu screen", "a black screen with text",
    "a person standing outside", "a group of people at a party", "a street at night with neon lights", "a police car", "a gas station",
    "a swamp with trees", "a house interior", "a store interior", "a highway from above", "a sunset over the ocean", "a crowd on a sidewalk",
    "a person holding a gun", "a motorcycle", "an animal in the wild", "a character in a car",
]


def words_of(script: str) -> list[str]:
    """The script's words as spoken (whitespace tokens, punctuation attached)."""
    return normalize_ws(script).split(" ") if normalize_ws(script) else []


def sentences_of(words: list[str]) -> list[tuple[int, int]]:
    """Word ranges [first, last] of each sentence (a sentence ends on . ! ? or a closing quote after them)."""
    out, start = [], 0
    for i, w in enumerate(words):
        if re.search(r"[.!?][\"'”’)]*$", w) or i == len(words) - 1:
            out.append((start, i))
            start = i + 1
    return out


def lead_number(text: str) -> int | None:
    m = _LEAD_NUMBER.match(text.strip())
    if not m:
        return None
    tok = m.group(1).lstrip("#").lower()
    return int(tok) if tok.isdigit() else NUMBER_WORDS.get(tok)


_ALT = re.compile(r"^(or|and|then|plus)$", re.I)


def clauses_of(words: list[str], a: int, b: int) -> list[tuple[int, int]]:
    """A sentence cut where it names a second thing to show (race jet skis, | or paddle a kayak)."""
    out, start = [], a
    for i in range(a, b):
        if words[i].endswith(",") and _ALT.match(words[i + 1].strip(",.")) and i - start >= 1 and b - i >= 2:
            out.append((start, i))
            start = i + 1
    out.append((start, b))
    return out


def _gta5_only(text: str) -> bool:
    return bool(re.search(r"\bGTA\s*(5|V)\b", text)) and not re.search(r"\bGTA\s*(6|VI)\b", text)


def segment_script(script: str) -> dict[str, Any]:
    """The rule-based plan: a beat per sentence (or per clause naming another thing), roles by pattern. Never fails."""
    words = words_of(script)
    beats = []
    sents = sentences_of(words)
    seen_number = False
    teased: int | None = None  # "And number one changes everything." -> the next sentence is number one
    for k, (a, b) in enumerate(sents):
        text = " ".join(words[a : b + 1])
        n = lead_number(text)
        if n is None and teased is not None:
            n, teased = teased, None
        if n is not None:
            seen_number = True
            role = "item"
        elif k == len(sents) - 1 and _CTA.search(text):
            role = "cta"
        elif _TEASE.search(text):  # "And number one changes everything."
            role = "suspense"
            tok = _TEASE.search(text).group(1).lower()
            teased = int(tok) if tok.isdigit() else NUMBER_WORDS.get(tok)
        elif not seen_number and k == 0:
            role = "hook"
        elif _gta5_only(text):
            role = "comparison"
        else:
            role = "claim"
        specific = role in ("item", "claim", "comparison")
        for j, (c0, c1) in enumerate(clauses_of(words, a, b) if role in ("item", "claim") else [(a, b)]):
            part = " ".join(words[c0 : c1 + 1])
            concept = re.sub(r"^(?:number\s+)?\S+:\s*", "", part) if (n and j == 0) else part
            concept = re.sub(r"^(or|and|then|plus)\s+", "", concept.strip(" ,.!?"), flags=re.I)
            beats.append({
                "words": [c0, c1], "text": part, "role": role, "number": n if j == 0 else None,
                "concept": concept or part,
                "prompts": [concept or part],
                "distractors": [], "game": "gta5" if role == "comparison" else "gta6",
                "specific": specific, "claim": text if specific and j == 0 else None, "emphasis": [],
            })
    return {"words": words, "beats": beats, "method": "rules"}


PLAN_SYSTEM = """You are the editor of a fast GTA VI YouTube Shorts channel. For each phrase of a narration you say
which exact footage must be on screen while it is spoken. You never change the words."""

PLAN_PROMPT = """The narration is already cut into numbered BEATS (one visual idea each):
{beats}

For EVERY beat, anchor the image to the people, objects and actions actually named in the narration. Do not invent screens, scenery, props or actions. For abstract commentary describe a visible subject from the surrounding narration. Return JSON:
{{"title": short video title (max 70 chars),
 "beats": [ {{
  "i": the beat number above,
  "role": hook | item | claim | comparison | suspense | cta,
  "headline": a short, contiguous phrase from this beat suitable as an on-screen title (up to five words),
  "concept": what the viewer must SEE, concretely (subject + action + place),
  "prompts": 1-3 short picture descriptions for an image search ("a person riding a jet ski on the ocean"),
  "distractors": 2-4 look-alike scenes that would be WRONG ("a motorboat", "a person swimming"),
  "game": "gta6", or "gta5" when the beat is about GTA 5 (a comparison),
  "specific": true when only footage of exactly this fits; false for the hook, a teaser ("number one changes everything") and the call to action,
  "claim": the factual statement about the game the beat makes, or null,
  "emphasis": an array of 1-2 words from the beat to highlight
 }} ]
}}
One entry per beat, {n} entries, in order. Short values: this is a plan, not prose."""


def validate_beats(beats: Any, n_words: int) -> str | None:
    if not isinstance(beats, list) or not beats:
        return "beats must be a non-empty list"
    cursor = 0
    for i, b in enumerate(beats):
        rng = b.get("words") if isinstance(b, dict) else None
        if not (isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, int) for x in rng)):
            return f"beat {i} needs words: [first, last]"
        if rng[0] != cursor or rng[1] < rng[0]:
            return f"beat {i} must start at word {cursor} (got {rng})"
        cursor = rng[1] + 1
    if cursor != n_words:
        return f"beats stop at word {cursor - 1} but the script has words 0-{n_words - 1}"
    return None


def rule_title(words: list[str]) -> str:
    """The first sentence (the hook), as a title, when no model writes one."""
    a, b = sentences_of(words)[0]
    first = " ".join(words[a : b + 1]).rstrip(" .!")
    return first[:70] if len(first) <= 70 else first[:67].rsplit(" ", 1)[0] + "..."


def _merge(rule: dict[str, Any], ans: dict[str, Any]) -> dict[str, Any]:
    """The model's description of one beat over the rule beat: the words, numbers and roles of the rules stay."""
    out = dict(rule)
    headline = normalize_ws(str(ans.get("headline") or ""))
    if headline and len(headline.split()) <= 5 and len(headline) <= 45 and headline.casefold() in rule["text"].casefold():
        out["headline"] = headline
    concept = normalize_ws(str(ans.get("concept") or ""))
    if concept:
        out["concept"] = concept[:160]
    prompts = [normalize_ws(str(x))[:120] for x in ans.get("prompts") or [] if str(x).strip()][:3]
    if prompts:
        out["prompts"] = prompts
    out["distractors"] = [normalize_ws(str(d))[:80] for d in ans.get("distractors") or [] if str(d).strip()][:4]
    role = str(ans.get("role") or "").lower()
    if rule["number"] is None and rule["role"] not in ("hook", "suspense", "cta") and role in ("claim", "comparison", "item"):
        out["role"] = role  # the rules are sure of list numbers, hooks, teasers and the CTA; the model may refine the rest
    game = str(ans.get("game") or "").lower().replace(" ", "")
    game = {"gtav": "gta5", "gta5": "gta5", "gtavi": "gta6", "gta6": "gta6"}.get(game)
    if game and out["role"] not in ("hook", "suspense", "cta"):
        out["game"] = game
    if isinstance(ans.get("specific"), bool) and out["role"] not in ("hook", "suspense", "cta"):
        out["specific"] = ans["specific"]
    if out["role"] in ("hook", "suspense", "cta"):
        out["specific"] = False
    claim = ans.get("claim")
    out["claim"] = normalize_ws(str(claim))[:200] if claim and out["specific"] else (rule["claim"] if out["specific"] else None)
    emphasis = ans.get("emphasis")
    if isinstance(emphasis, str):
        emphasis = emphasis.split()
    out["emphasis"] = [normalize_ws(e) for e in emphasis if isinstance(e, str) and e.strip()][:2] if isinstance(emphasis, list) else []
    return out


def validate_answer(data: Any, n: int) -> str | None:
    beats = data.get("beats") if isinstance(data, dict) else None
    if not isinstance(beats, list) or not beats:
        return "beats must be a non-empty list"
    if sum(1 for b in beats if isinstance(b, dict)) < max(1, n // 2):
        return f"describe every one of the {n} beats"
    return None


def plan_script(llm: Any, script: str, *, log=lambda m: None) -> dict[str, Any]:
    """The edit plan of a script. The words are cut into beats by rule (never fails, never drifts);
    the model, when there is one, only says what each beat must show. One short call, one retry."""
    words = words_of(script)
    if not words:
        raise ValueError("The script is empty.")
    base = segment_script(script)
    if llm is None:
        return {**base, "title": rule_title(words)}
    rules = base["beats"]
    listing = "\n".join(f"{k}. [{b['role']}{' #' + str(b['number']) if b['number'] else ''}] {b['text']}" for k, b in enumerate(rules, 1))
    log(f"script plan: asking the model what each of the {len(rules)} beats must show (one short call)")
    try:
        data = llm.complete_json(PLAN_SYSTEM, PLAN_PROMPT.format(beats=listing, n=len(rules)), max_tokens=3500, label="plan_script",
                                 required_keys=("beats",), validate=lambda d: validate_answer(d, len(rules)), retries=1)
    except Exception as exc:  # the rules always give a usable plan
        log(f"script plan: the model failed ({type(exc).__name__}: {str(exc)[:160]}); using the rule-based plan")
        return {**base, "title": rule_title(words)}
    answers: dict[int, dict[str, Any]] = {}
    for k, a in enumerate(data["beats"], 1):
        if isinstance(a, dict):
            idx = a.get("i") if isinstance(a.get("i"), int) and 1 <= a.get("i") <= len(rules) else k
            answers.setdefault(idx, a)
    beats = [_merge(r, answers.get(k, {})) for k, r in enumerate(rules, 1)]
    described = sum(1 for k in range(1, len(rules) + 1) if k in answers)
    log(f"script plan: the model described {described}/{len(rules)} beats")
    title = normalize_ws(str(data.get("title") or ""))[:70] or rule_title(words)
    return {"words": words, "beats": beats, "title": title, "method": "model" if described == len(rules) else "model+rules"}


def sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?][\"'”’)]*$", text.strip()))
