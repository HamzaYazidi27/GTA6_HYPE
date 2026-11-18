"""Offline stand-ins for the LLM and the voice, so the whole pipeline runs in tests.

TEST FIXTURE: the article below is fictional and exists only to exercise the
pipeline. It is never published and does not describe real events.
"""
from __future__ import annotations

import json
import re
from typing import Any

import numpy as np

from gta_factory.providers.llm.base import LLMProvider
from gta_factory.providers.tts import TTSProvider, TTSResult

ARTICLE_URL = "https://www.rockstargames.com/newswire/article/test-fixture"
ARTICLE_TEXT = """TEST FIXTURE - fictional article used by the automated tests.

Rockstar Games today shared a new look at its next game and confirmed that pre-orders open on December 4 for every platform.

The studio said the game will launch on PlayStation 5 and Xbox Series X|S on November 19, 2026, as previously scheduled.

Rockstar added that a third trailer is planned before launch and will focus on the state of Leonida and its cities.

In a statement, the studio said: "We can't wait for players to finally explore Leonida for themselves."

Some fans speculate that a PC version could follow later, but the studio did not comment on other platforms in its announcement.

The update was published on the Rockstar Newswire alongside new screenshots of Vice City at night and a short message thanking the community for its patience during development."""

CLAIMS = [
    {"id": "C1", "claim": "Pre-orders open on December 4 for every platform.", "type": "date", "source_index": 1, "evidence": "confirmed that pre-orders open on December 4 for every platform", "attributed_to": "Rockstar Games", "basis": "official", "event_date": "2026-12-04", "importance": 5},
    {"id": "C2", "claim": "The game launches on PlayStation 5 and Xbox Series X|S on November 19, 2026.", "type": "date", "source_index": 1, "evidence": "The studio said the game will launch on PlayStation 5 and Xbox Series X|S on November 19, 2026, as previously scheduled.", "attributed_to": "Rockstar Games", "basis": "official", "event_date": "2026-11-19", "importance": 5},
    {"id": "C3", "claim": "A third trailer is planned before launch.", "type": "plan", "source_index": 1, "evidence": "Rockstar added that a third trailer is planned before launch", "attributed_to": "Rockstar Games", "basis": "official", "event_date": None, "importance": 4},
    {"id": "C4", "claim": "Some fans speculate that a PC version could follow later.", "type": "opinion", "source_index": 1, "evidence": "Some fans speculate that a PC version could follow later", "attributed_to": "fans", "basis": "analysis", "event_date": None, "importance": 2},
]

NARRATION = {
    "hook": "Pre-orders for Grand Theft Auto VI open on December 4, and Rockstar Games has put that date in writing for every platform.",
    "story": "Rockstar Games says the game launches on November 19, 2026, on PlayStation 5 and Xbox Series X|S. The studio also says a third trailer is planned before launch. Some fans speculate that a PC version could follow later, but that is only speculation for now.",
    "payoff": "For players, the plan is simple. Mark December 4 for pre-orders, and expect one more trailer before launch day.",
    "cta": "Will you pre-order on day one, or wait for the third trailer?",
}


class ScriptedLLM(LLMProvider):
    """Answers each pipeline prompt with a fixed, valid document."""

    name = "scripted"

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8000) -> str:
        if "DETAILS (id | where in the footage" in prompt:
            return details_answer(prompt)
        topic = topic_answer(prompt)
        if topic is not None:
            return topic
        if "NARRATION - numbered sentences" in prompt:
            n = len(re.findall(r"^\d+\. \[", prompt, re.M))
            return json.dumps({"scenes": self._scenes(n)})
        if "SOURCE ARTICLES" in prompt and '"story_headline"' in prompt:
            return json.dumps(self._analysis())
        if "PREVIOUSLY PRODUCED VIDEOS" in prompt:
            return json.dumps(self._editor())
        if "SECTIONS TO WRITE" in prompt:
            ids = re.findall(r"^- (\w+) \[\d+ words\]:", prompt, re.M)
            return json.dumps({"title": "Pre-orders open December 4", "sections": [{"id": i, "narration": NARRATION.get(i, NARRATION["story"]), "claim_ids": self._claims_for(i)} for i in ids]})
        if "SCRIPT (numbered sentences)" in prompt:
            return json.dumps({"sentences": [], "issues": [], "verdict": "pass"})
        if "Design exactly 3 thumbnail concepts" in prompt:
            return json.dumps({"concepts": [
                {"concept": "stamp", "lines": ["PRE-ORDERS", "DEC 4"], "accent": "DEC 4", "lead_claim_id": "C1", "motif": "calendar", "color": "coral", "calendar": {"month": "DEC", "day": "4"}, "why": "date"},
                {"concept": "number", "lines": ["LAUNCH"], "bigText": "NOV 19", "label": "LAUNCH DAY", "lead_claim_id": "C2", "motif": "calendar", "color": "sun", "calendar": {"month": "NOV", "day": "19"}, "why": "date"},
                {"concept": "split", "lines": ["ONE MORE", "TRAILER"], "accent": "TRAILER", "lead_claim_id": "C3", "motif": "controller", "color": "cyan", "calendar": None, "why": "trailer"},
            ]})
        if "pinned_comment" in prompt:
            return json.dumps({"titles": ["GTA 6 pre-orders open December 4", "Rockstar sets the GTA 6 pre-order date", "GTA 6: pre-orders, launch date and one more trailer"], "chosen": 0, "intro": "Rockstar has set the pre-order date.\nHere is what is confirmed.", "tags": ["gta 6 pre orders", "gta 6 trailer 3"], "hashtags": ["GTA6", "GTAVI", "Rockstar"], "pinned_comment": "Day one or wait?"})
        if "Say OK" in prompt:
            return "OK"
        raise AssertionError("unexpected prompt: " + prompt[:200])

    @staticmethod
    def _claims_for(section: str) -> list[str]:
        return {"hook": ["C1"], "story": ["C2", "C3", "C4"], "payoff": ["C1", "C3"], "cta": []}.get(section, [])

    @staticmethod
    def _analysis() -> dict[str, Any]:
        return {
            "story_headline": "Rockstar sets the pre-order date and plans a third trailer",
            "summary": "Rockstar Games says pre-orders open on December 4 and the launch date is unchanged.",
            "topic_tags": ["pre-orders", "release date", "trailer"],
            "claims": CLAIMS,
            "quotes": [{"text": "We can't wait for players to finally explore Leonida for themselves.", "speaker": "Rockstar Games", "source_index": 1}],
            "entities": {"people": [], "organizations": ["Rockstar Games"], "games": ["Grand Theft Auto VI"], "places": ["Leonida"], "products": ["PlayStation 5"]},
            "key_dates": [{"date": "2026-12-04", "label": "pre-orders open", "kind": "upcoming"}],
            "search_queries": ["GTA 6 pre-orders December 4"],
            "sensitivities": [],
        }

    @staticmethod
    def _editor() -> dict[str, Any]:
        s = lambda n: {"score": n, "why": "test"}  # noqa: E731
        return {
            "scores": {"originality": s(80), "audience_relevance": s(85), "potential_interest": s(70), "player_impact": s(72)},
            "new_claim_ids": ["C1", "C2", "C3", "C4"],
            "already_covered_claim_ids": [],
            "angles": [{"title": "date", "thesis": "The pre-order date is now official.", "why_original": "Puts the dates in order.", "lead_claim_ids": ["C1"], "risk": "Do not present the PC version as planned."}] * 3,
            "chosen_angle": 0,
            "working_title": "Pre-orders open December 4",
            "hook_idea": "Pre-orders open on December 4 [C1]",
            "what_we_do_not_know": ["Whether a PC version is planned."],
            "recommended_format": "short",
            "editor_notes": "Keep it tight.",
        }

    @staticmethod
    def _scenes(n: int) -> list[dict[str, Any]]:
        plan = [  # one scene per narration sentence, in order
            ("kinetic", {"lines": ["PRE-ORDERS", "DECEMBER 4"], "accent": "DECEMBER 4"}, ["C1"], "flash", "impact"),
            ("title", {"title": "Pre-orders open December 4", "subtitle": "What Rockstar confirmed", "date": "November 16, 2026"}, ["C2"], "slice", "swell"),
            ("claim", {"claim": "A third trailer is planned before launch", "status": "confirmed", "source": "Rockstar Games"}, ["C3"], "wipe", "impact"),
            ("claim", {"claim": "A PC version could follow later", "status": "confirmed", "source": "fans"}, ["C4"], "cut", "impact"),
            ("kinetic", {"lines": ["THE PLAN"], "accent": "PLAN"}, [], "cut", "whoosh"),
            ("timeline", {"title": "Road to launch", "events": [{"date": "Dec 4", "label": "Pre-orders open", "state": "future"}, {"date": "Before launch", "label": "Third trailer", "state": "future"}], "cues": ["December", "trailer"]}, ["C1", "C3"], "push", "tick"),
        ]
        scenes = []
        for i in range(1, n + 1):
            t, data, ids, tr, sfx = plan[(i - 1) % len(plan)] if i < n else ("outro", {"question": "Day one or wait?", "cta": "Tell us below"}, [], "slice", "swell")
            scenes.append({"sentences": [i, i], "template": t, "data": data, "emphasis": "", "visual_description": t, "claim_ids": ids, "transition": tr, "sound_effect": sfx})
        return scenes


class FakeTTS(TTSProvider):
    """A tone with a speech-like envelope: about 3 words per second."""

    name = "fake"

    def synthesize(self, text: str) -> TTSResult:
        sr = 24000
        words = max(1, len(text.split()))
        dur = 0.32 * words + 0.1
        t = np.arange(int(dur * sr)) / sr
        env = 0.55 + 0.45 * np.sin(2 * np.pi * 3.1 * t)
        rng = np.random.default_rng(words)
        x = 0.25 * np.sin(2 * np.pi * 165 * t) * env + 0.01 * rng.standard_normal(len(t))
        pad = np.zeros(int(0.12 * sr))
        return TTSResult(np.concatenate([pad, x, pad]).astype(np.float32), sr)


def details_answer(prompt: str) -> str:
    """The scripted LLM's answer to the "details you missed" script prompt."""
    ids = re.findall(r"^(D\d+) \|", prompt, re.M)
    return json.dumps({
        "title": f"{len(ids)} details you missed in Trailer 1",
        "hook": f"You watched the trailer. But did you see these {len(ids)} small details?",
        "lines": [{"id": i, "line": f"Look at the corner of this shot. There is a small sign here that most people never noticed, number {k}.", "label": f"SMALL SIGN {k}"} for k, i in enumerate(ids, 1)],
        "outro_question": "Which of these details had you missed?",
        "thumb_lines": [f"{len(ids)} DETAILS", "YOU MISSED"],
    })


# ----------------------------------------------------------------------------- comparisons and top lists
# TEST FIXTURE: two fictional preview articles, used only by the automated tests.
TOPIC_URLS = ["https://www.ign.com/articles/test-fixture-gta-6-preview", "https://www.gamesradar.com/test-fixture-gta-6-hands-on"]
TOPIC_TEXTS = [
    """TEST FIXTURE - fictional preview used by the automated tests.

In our hands-on preview, Jason carries two long guns and a sidearm, and every other weapon goes in the trunk of his car.

Rockstar's Rob Nelson told us that the focus ability slows time and highlights the weak points of enemies.

Leonida is home to more than 170 animal species, from alligators to flamingos.

Nelson said that a crime now needs a witness or an alarm before the police come after you.""",
    """TEST FIXTURE - fictional hands-on report used by the automated tests.

Rockstar says a crime now needs a witness or an alarm before the police come after you, and the game tracks what the police know.

A gym membership lets Jason and Lucia build visible muscle with timing-based workouts.

Focus also works on cars, marking the engine, the fuel tank and the tires.""",
]
TOPIC_CLAIMS = [
    {"id": "C1", "subject": "weapon limit", "claim": "Jason carries two long guns and a sidearm, the rest goes in the car trunk.", "type": "fact", "source_index": 1, "evidence": "Jason carries two long guns and a sidearm, and every other weapon goes in the trunk of his car.", "attributed_to": "IGN preview", "basis": "named_source", "event_date": None, "importance": 5},
    {"id": "C2", "subject": "focus mode", "claim": "The focus ability slows time and highlights enemy weak points.", "type": "fact", "source_index": 1, "evidence": "the focus ability slows time and highlights the weak points of enemies", "attributed_to": "Rob Nelson, Rockstar", "basis": "official", "event_date": None, "importance": 5},
    {"id": "C3", "subject": "witnesses", "claim": "A crime needs a witness or an alarm before the police respond.", "type": "fact", "source_index": 2, "evidence": "a crime now needs a witness or an alarm before the police come after you", "attributed_to": "Rockstar", "basis": "official", "event_date": None, "importance": 4},
    {"id": "C4", "subject": "gym", "claim": "A gym membership lets Jason and Lucia build visible muscle.", "type": "fact", "source_index": 2, "evidence": "A gym membership lets Jason and Lucia build visible muscle with timing-based workouts.", "attributed_to": "GamesRadar hands-on", "basis": "named_source", "event_date": None, "importance": 3},
    {"id": "C5", "subject": "animals", "claim": "Leonida has more than 170 animal species.", "type": "number", "source_index": 1, "evidence": "Leonida is home to more than 170 animal species", "attributed_to": "IGN preview", "basis": "named_source", "event_date": None, "importance": 3},
]
VERSUS_SCRIPT = {
    "hook": "Leonida is home to more than 170 animal species, and that is not the only thing GTA 6 shares with Red Dead 2. Put the two games side by side, system by system.",
    "item_01": "Start with what you can carry. In Red Dead 2, Arthur carries at most two sidearms and two long guns, and every other gun stays on his horse. In GTA 6, IGN's preview says Jason carries two long guns and a sidearm, and the rest goes in the trunk. Red Dead 2 and GTA 6 both keep you light on foot.",
    "item_02": "Then there is how you aim. In Red Dead 2, Dead Eye slows time so you can mark several targets before firing. In GTA 6, Rockstar's Rob Nelson says focus slows time and highlights enemy weak points.",
    "item_03": "Finally, the law. Red Dead 2 had witnesses report crimes, and stopping one in time prevented the report. A bandana in Red Dead 2 could keep Arthur from being recognised. Crimes in Red Dead 2 put a bounty on Arthur's head in each state, and bounty hunters came after him until it was paid. In GTA 6, according to Rockstar, a crime needs a witness or an alarm before the police respond.",
    "verdict": "Red Dead 2 and GTA 6 clearly share a lot. GTA 6 keeps the weight of Red Dead and adds cars and phones. How far it goes is still to be seen.",
    "cta": "Which Red Dead system are you most glad to see in GTA 6?",
}
LIST_SCRIPT = {
    "hook": "Leonida is home to more than 170 animal species, and day one is the time to start. Here is the plan.",
    "item_01": "Get a gym membership. According to the GamesRadar hands-on, it lets Jason and Lucia build visible muscle.",
    "item_02": "Learn to use focus. Rockstar's Rob Nelson says it slows time and highlights enemy weak points.",
    "item_03": "Pick your spot before any crime. According to Rockstar, a crime needs a witness or an alarm before the police respond.",
    "verdict": "Three habits, one good first day in Leonida.",
    "cta": "What will you do first in GTA 6?",
}


def topic_answer(prompt: str) -> str | None:
    """The scripted answers of a comparison / top list job (None for any other prompt)."""
    if "VIDEO TOPIC:" in prompt and "SOURCE ARTICLES" in prompt:
        return json.dumps({"headline": "What the previews say about GTA 6 systems", "claims": TOPIC_CLAIMS, "quotes": [{"text": "a crime now needs a witness or an alarm before the police come after you", "speaker": "Rockstar", "source_index": 2}]})
    if "CLAIMS TO CHECK" in prompt and "fictional hands-on report" in prompt:
        # C3 is "supported" by its own article (must be ignored) and by the other one (counts)
        return json.dumps({"checks": [
            {"id": "C3", "sources": [
                {"source_index": 2, "verdict": "supports", "evidence": "Rockstar says a crime now needs a witness or an alarm before the police come after you", "independent": True, "cites_official": False},
                {"source_index": 1, "verdict": "supports", "evidence": "Nelson said that a crime now needs a witness or an alarm before the police come after you.", "independent": True, "cites_official": False},
            ], "is_old_news": False, "first_known_date": None, "note": ""},
        ], "contradictions": [], "additional_facts": []})
    if "Plan exactly" in prompt and "GTA 6 FACT SHEET" in prompt:
        versus = "game_fact_ids" in prompt
        if versus:
            ids = {f: re.search(rf"^(G\d+) \[{f}\]", prompt, re.M).group(1) for f in ("weapon limit", "dead eye and aiming", "witnesses and the law")}
            items = [
                {"title": "Weapon limit", "claim_ids": ["C1"], "game_fact_ids": [ids["weapon limit"]], "then": "Two guns, rest on the horse", "now": "Two guns, rest in the trunk"},
                {"title": "Focus mode", "claim_ids": ["C2"], "game_fact_ids": [ids["dead eye and aiming"]], "then": "Dead Eye", "now": "Focus"},
                {"title": "Witnesses", "claim_ids": ["C3"], "game_fact_ids": [ids["witnesses and the law"]], "then": "Witnesses report crimes", "now": "Witness or alarm"},
                {"title": "Made up", "claim_ids": ["C99"], "game_fact_ids": [ids["weapon limit"]]},
            ]
        else:
            items = [{"title": "Join a gym", "claim_ids": ["C4"], "kicker": "Visible muscle"}, {"title": "Master focus", "claim_ids": ["C2"], "kicker": "Slow time"}, {"title": "Pick your spot", "claim_ids": ["C3"], "kicker": "No witness, no cops"}]
        return json.dumps({"working_title": "GTA 6 vs Red Dead 2" if versus else "3 things to do on day one", "thesis": "What GTA 6 takes from Red Dead 2." if versus else "A first day plan.", "hook_idea": "170 species [C5]", "items": items, "verdict_idea": "A lot is shared.", "thumb_lines": ["GTA 6", "VS RED DEAD"] if versus else ["3 THINGS", "TO DO FIRST"]})
    if "How to write an item section" in prompt:
        ids = re.findall(r"^- (\w+) \[\d+ words\]:", prompt, re.M)
        versus = "Red Dead" in prompt
        text = VERSUS_SCRIPT if versus else LIST_SCRIPT
        claims = {"hook": ["C5"], "item_01": ["C1"] if versus else ["C4"], "item_02": ["C2"], "item_03": ["C3"], "verdict": [], "cta": []}
        return json.dumps({"title": "GTA 6 vs Red Dead 2, system by system" if versus else "3 things to do on day one in GTA 6", "sections": [{"id": i, "narration": text[i], "claim_ids": claims.get(i, [])} for i in ids]})
    return None
