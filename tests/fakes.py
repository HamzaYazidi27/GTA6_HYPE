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
    "hook": "Pre-orders for Grand Theft Auto VI open on December 4, and Rockstar Games has now put that date in writing for every platform.",
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
