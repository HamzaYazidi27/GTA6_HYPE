"""Checking a source typed by the operator (dashboard "Add a source" or ``radar sources add``)."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from ...app.core.errors import FactoryError
from .store import KINDS, TRUSTS

DEFAULT_TRUST = {"rss": "press", "reddit": "community", "youtube": "creator", "news_search": "press"}
SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")


def normalize_source(raw: dict[str, Any]) -> dict[str, Any]:
    """{"name", "kind", "target", "trust", "topic_only", "options"} ready for ``RadarStore.add_source``."""
    kind = str(raw.get("kind") or "").strip()
    target = re.sub(r"\s+", " ", str(raw.get("target") or "")).strip()
    if kind not in KINDS:
        raise FactoryError(f"Unknown source type '{kind}'.", hint="rss, reddit, youtube or news_search")
    if not target or len(target) > 300:
        raise FactoryError("Give the feed URL, the subreddit, the YouTube channel or the search words.")
    options: dict[str, Any] = {}
    name = str(raw.get("name") or "").strip()[:80]
    if kind == "rss":
        u = urlparse(target)
        if u.scheme not in ("http", "https") or not u.netloc:
            raise FactoryError("An RSS source needs the feed's URL (https://...).", hint="Most sites publish it at /feed, /rss or /feeds.xml.")
        name = name or u.netloc.removeprefix("www.")
    elif kind == "reddit":
        m = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", target)
        sub = m.group(1) if m else target.removeprefix("/").removeprefix("r/").strip("/")
        if not SUBREDDIT.match(sub):
            raise FactoryError(f"'{target}' is not a subreddit name.", hint="For example GTA6 or r/GTA6.")
        target = sub
        options = {"listing": str(raw.get("listing") or "top"), "t": "day"}
        name = name or f"r/{sub}"
    elif kind == "youtube":
        m = re.search(r"(UC[\w-]{22})", target) or re.search(r"(@[\w.\-]{3,40})", target)
        if not m:
            raise FactoryError(f"'{target}' is not a YouTube channel.", hint="Give the channel id (UC...), its @handle or its URL.")
        target = m.group(1)
        name = name or f"{target} (YouTube)"
    else:
        if len(target) < 2:
            raise FactoryError("Give the words to search for, e.g. \"GTA 6\" heist.")
        name = name or f"News search: {target[:40]}"
    trust = str(raw.get("trust") or DEFAULT_TRUST[kind])
    if trust not in TRUSTS:
        raise FactoryError(f"Unknown trust '{trust}'.", hint="official, press, community or creator")
    topic_only = bool(raw.get("topic_only")) if raw.get("topic_only") is not None else kind == "reddit"
    return {"name": name, "kind": kind, "target": target, "trust": trust, "topic_only": topic_only, "options": options}
