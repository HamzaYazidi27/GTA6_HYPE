"""Is an entry about GTA VI, which content category does it suggest, and is it a story we already have?

Keyword rules only (fast, free, explainable). The AI analysis decides the final
categories; these hints are shown until then and steer the first pass.
"""
from __future__ import annotations

import re
from typing import Any

from ...app.core.utils import tokens

GTA6 = re.compile(r"(?i)\b(?:gta\s*-?\s*(?:6|vi)|grand\s+theft\s+auto\s*(?:6|vi)|gta6|gtavi)\b|#gta6\b|#gtavi\b|\bleonida\b")
# names that only exist in GTA VI: enough when the text also names the series or Rockstar
WORLD = re.compile(r"(?i)\b(?:vice city|lucia|jason duval|port gellhorn|ambrosia|grassrivers|mount kalaga|leonida keys|jason and lucia)\b")
SERIES = re.compile(r"(?i)\b(?:gta|grand theft auto|rockstar)\b")
# other games / editions that mention GTA VI only in passing are still kept: the AI judges them

CATEGORY_WORDS: dict[str, tuple[str, ...]] = {
    "secrets": (
        "easter egg", "easter eggs", "hidden", "secret", "secrets", "reference", "references", "nod to", "callback", "cameo",
        "missed", "spotted", "discovered", "discovery", "detail", "details", "mystery", "bigfoot", "sasquatch", "ufo", "ghost",
        "returning character", "returns", "tribute", "frame by frame", "breakdown",
    ),
    "map": (
        "map", "location", "locations", "leonida", "vice city", "port gellhorn", "ambrosia", "grassrivers", "kalaga", "keys",
        "interior", "interiors", "building", "buildings", "region", "regions", "florida", "miami", "explore", "explorable", "landmark",
        "island", "beach", "swamp", "everglades", "city", "neighborhood", "size", "bigger than",
    ),
    "missions": (
        "mission", "missions", "gameplay", "mechanic", "mechanics", "ability", "abilities", "activity", "activities", "side mission",
        "side quest", "story", "character", "skill", "skills", "stamina", "cover", "fishing", "gym", "minigame", "mini-game",
        "companion", "protagonist", "protagonists", "switch", "driving", "hunting", "social media", "phone", "economy", "customization",
    ),
    "heists": (
        "heist", "heists", "robbery", "robberies", "rob", "robbing", "bank", "store", "theft", "steal", "stealing", "carjack",
        "police", "cops", "wanted", "wanted level", "chase", "chases", "weapon", "weapons", "gun", "guns", "escape", "getaway",
        "stash", "safe", "loot", "crime", "crimes", "criminal", "bounty", "arrest",
    ),
}
# words every GTA VI headline shares: they say nothing about which story it is
GENERIC = set("gta grand theft auto vi rockstar rockstar's games game video videos new latest finally everything know need update news official trailer trailers player players".split())


def is_relevant(title: str, summary: str, *, topic_only: bool = False) -> bool:
    text = f"{title}\n{summary}"
    if GTA6.search(text):
        return True
    if WORLD.search(text) and SERIES.search(text):
        return True
    return bool(topic_only)


def category_hints(title: str, summary: str) -> list[str]:
    text = f" {title.lower()} {summary.lower()[:600]} "
    scores = {}
    for cat, words in CATEGORY_WORDS.items():
        n = sum(1 for w in words if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text))
        if n:
            scores[cat] = n + (2 if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", f" {title.lower()} ") for w in words) else 0)
    return [c for c, _ in sorted(scores.items(), key=lambda kv: -kv[1])][:2]


def fingerprint(title: str) -> list[str]:
    """Title tokens that identify the story (series names and filler words removed)."""
    return sorted({t for t in tokens(title) if t not in GENERIC})


def similarity(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    return max(inter / len(sa | sb), inter / min(len(sa), len(sb)) if inter >= 3 else 0.0)


def same_story(a: list[str], b: list[str], threshold: float = 0.5) -> bool:
    """Two headlines about the same news: most of the shorter one's words appear in the other."""
    return similarity(a, b) >= threshold


def find_cluster(fp: list[str], candidates: list[dict[str, Any]], threshold: float = 0.5) -> dict[str, Any] | None:
    best, best_sim = None, 0.0
    for c in candidates:
        sim = similarity(fp, c.get("fingerprint") or [])
        if sim >= threshold and sim > best_sim:
            best, best_sim = c, sim
    return best
