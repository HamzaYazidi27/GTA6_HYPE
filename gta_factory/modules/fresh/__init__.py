"""``python -m gta_factory fresh``: the freshest GTA 6 stories worth a video, by category.

Reads, in one pass (RSS / Atom only, nothing scraped):

* the reputable outlets' own feeds (``sources.feeds`` + ``fresh.feeds``),
* Bing News RSS (direct article links) and Google News RSS (coverage signals) for each category's queries,
* the community (``fresh.reddit``: r/GTA6 rising / new): where secrets, map finds and heist payouts show
  up first. Community posts are ideas to check in your own game, never a source the video cites.

Every entry is put in a category (news, secrets, map, missions, heists: ``fresh.categories``), entries
about the same story are grouped, and each story gets a 0-100 score: how fresh it is, how reliable its
best source is (primary > secondary > the rest), how many outlets cover it, whether the community talks
about it, and whether the channel already made a video about it (story memory). The output gives the
command that makes the video, with the reputable articles first.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ...app.core.utils import canonical_url, domain_of, jaccard, normalize_ws, tokens

GTA_RE = re.compile(r"\b(gta\s*(6|vi)|grand theft auto\s*(6|vi)|leonida|vice city|lucia|jason duval|rockstar)\b", re.I)
TIER_POINTS = {"primary": 25, "secondary": 18, "other": 6, "community": 5}
TIER_RANK = {"primary": 0, "secondary": 1, "other": 2, "community": 3}


@dataclass
class Entry:
    title: str
    url: str
    publisher: str
    domain: str
    tier: str  # primary | secondary | other | community
    published_at: str | None
    provider: str
    snippet: str = ""
    direct: bool = True  # a link the factory can read as an article

    def age_hours(self, now: datetime) -> float | None:
        if not self.published_at:
            return None
        try:
            dt = datetime.fromisoformat(self.published_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (now - dt).total_seconds() / 3600)


@dataclass
class Story:
    category: str
    title: str
    entries: list[Entry] = field(default_factory=list)
    score: int = 0
    age_hours: float | None = None
    covered_before: str | None = None
    reasons: list[str] = field(default_factory=list)
    command: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entries"] = [asdict(e) for e in self.entries]
        return d


# ----------------------------------------------------------------------------- reading the sources

def _feed_entries(url: str, timeout: float) -> list[dict[str, Any]]:
    from ...providers.search import _feed

    return list(_feed(url, None, timeout).entries)


def _date(e: dict[str, Any]) -> str | None:
    from ...providers.search import _date as parse

    return parse(e.get("published") or e.get("updated"))


def _strip(text: str) -> str:
    return normalize_ws(re.sub(r"<[^>]+>", " ", text or ""))


def read_sources(
    settings: Any,
    *,
    log: Callable[[str], None] = print,
    feed_entries: Callable[[str, float], list[dict[str, Any]]] = _feed_entries,
    search: Callable[[str, str], list[Any]] | None = None,
) -> list[Entry]:
    """Every entry the configured feeds, news searches and community feeds give right now."""
    timeout = float(settings.get("providers.search.request_timeout_seconds", 25))
    cfg = settings.get("fresh", {}) or {}
    out: list[Entry] = []
    feeds = list(settings.get("sources.feeds", []) or []) + list(cfg.get("feeds") or [])
    for feed in feeds:
        try:
            entries = feed_entries(feed["url"], timeout)
        except Exception as exc:  # one broken feed never stops the others
            log(f"fresh: {feed.get('name', feed['url'])}: not read ({type(exc).__name__})")
            continue
        for e in entries:
            link = canonical_url(e.get("link") or "")
            if not link:
                continue
            dom = domain_of(link)
            out.append(Entry(normalize_ws(e.get("title", "")), link, str(feed.get("name") or dom), dom, settings.source_tier(dom), _date(e), "feed", _strip(e.get("summary", ""))[:300], True))
    for feed in cfg.get("reddit") or []:
        url = feed["url"] if isinstance(feed, dict) else str(feed)
        name = feed.get("name") if isinstance(feed, dict) else "Reddit"
        try:
            entries = feed_entries(url, timeout)
        except Exception as exc:
            log(f"fresh: {name}: not read ({type(exc).__name__})")
            continue
        for e in entries:
            link = e.get("link") or ""
            if link:
                out.append(Entry(normalize_ws(e.get("title", "")), link, str(name or "Reddit"), domain_of(link), "community", _date(e), "community", "", False))
    if search is None:
        search = _news_search(settings)
    queries = sorted({q for c in (cfg.get("categories") or {}).values() for q in c.get("queries") or []})
    for q in queries:
        for provider in ("bing_news_rss", "google_news_rss"):
            try:
                hits = search(provider, q)
            except Exception as exc:
                log(f"fresh: {provider} '{q}': {type(exc).__name__}")
                continue
            for h in hits:
                dom = h.domain or domain_of(h.url)
                out.append(Entry(h.title, h.url, h.publisher or dom, dom, settings.source_tier(dom) if dom else "other", h.published_at, provider, h.snippet, bool(h.direct)))
    return out


def _news_search(settings: Any) -> Callable[[str, str], list[Any]]:
    from ...providers.search import PROVIDERS

    made: dict[str, Any] = {}

    def run(provider: str, query: str) -> list[Any]:
        if provider not in made:
            made[provider] = PROVIDERS[provider](settings)
        return made[provider].search(query)

    return run


# ----------------------------------------------------------------------------- sorting them

def categorize(entry: Entry, categories: dict[str, dict[str, Any]]) -> str:
    """The category whose keywords the title (then the snippet) names most; "news" when none does."""
    title, text = entry.title.lower(), f"{entry.title} {entry.snippet}".lower()

    def says(kw: str, where: str) -> bool:  # whole words ("rob" is not "problem"), plural allowed
        return re.search(rf"\b{re.escape(kw.lower())}(e?s)?\b", where) is not None

    best, hits = "news", 0
    for cid, c in categories.items():
        n = sum(2 if says(kw, title) else 1 for kw in c.get("keywords") or [] if says(kw, text))
        if n > hits:
            best, hits = cid, n
    return best


_COMMON = {"gta", "grand", "theft", "auto", "vi", "already", "someone", "people", "players", "player", "everything", "really", "actually",
           "finally", "found", "first", "after", "before", "every", "things", "thing", "better", "still", "would", "could", "should",
           "about", "rockstar", "game", "games", "here's", "heres", "update", "guide", "explained", "best"}


# words of a whole category, not of one story ("mission", "map"): sharing them does not make two stories one
_TOPIC_GENERIC = frozenset({"mission", "missions", "chapter", "chapters", "walkthrough", "guide", "guides", "location", "locations", "secret", "secrets",
                            "hidden", "easter", "mystery", "legend", "heist", "heists", "robbery", "payout", "payouts", "patch", "update", "sales",
                            "trophy", "trophies", "achievement", "collectible", "collectibles", "explained", "money", "character", "characters"})


def group(entries: list[Entry], threshold: float = 0.4, *, topic_words: set[str] | frozenset[str] = frozenset()) -> list[list[Entry]]:
    """Entries about the same story: titles that share most of their words, or a rare name only they use
    ("Bigfoot" in a Reddit post and in a GamesRadar article). A category's own words ("mission", "map")
    never make two stories one."""
    words_of = [set(tokens(e.title)) - _COMMON for e in entries]
    df: dict[str, int] = {}
    for ws in words_of:
        for w in ws:
            df[w] = df.get(w, 0) + 1
    groups: list[tuple[set[str], list[Entry]]] = []
    for e, t in zip(entries, words_of):
        rare = {w for w in t if df[w] <= 2 and len(w) >= 6 and w.rstrip("s") not in topic_words and w not in topic_words}
        for words, members in groups:
            if jaccard(t, words) >= threshold or len(t & words) >= 4 or (rare & words):
                members.append(e)
                words |= t
                break
        else:
            groups.append((t, [e]))
    return [m for _, m in groups]


def _freshness(age: float | None) -> int:
    if age is None:
        return 6
    return 35 if age <= 3 else 30 if age <= 6 else 24 if age <= 12 else 16 if age <= 24 else 8


def score_story(story: Story, now: datetime, covered: list[dict[str, Any]], used_urls: set[str]) -> None:
    ages = [a for a in (e.age_hours(now) for e in story.entries) if a is not None]
    story.age_hours = round(min(ages), 1) if ages else None
    best = min((e.tier for e in story.entries), key=lambda t: TIER_RANK.get(t, 9))
    outlets = {e.publisher.lower() for e in story.entries if e.tier != "community"}
    community = sum(1 for e in story.entries if e.tier == "community")
    pts = {
        "fresh": _freshness(story.age_hours),
        "source": TIER_POINTS.get(best, 4),
        "coverage": min(20, 6 * max(0, len(outlets) - 1)),
        "community": min(10, 5 * community),
    }
    words = set(tokens(story.title))
    for s in covered:
        if jaccard(words, set(tokens(s.get("headline") or ""))) >= 0.45:
            story.covered_before = s.get("job_id")
    if any(canonical_url(e.url) in used_urls for e in story.entries):
        story.covered_before = story.covered_before or "an earlier job"
    pts["novelty"] = -40 if story.covered_before else 10
    story.score = max(0, min(100, sum(pts.values())))
    story.reasons = [f"{k} {v:+d}" for k, v in pts.items()]


def suggest(story: Story, categories: dict[str, dict[str, Any]]) -> None:
    """The command that makes the video: reputable, readable articles first (they decide what can be said)."""
    fmt = (categories.get(story.category) or {}).get("format") or "short"
    readable = sorted({e.url: e for e in story.entries if e.direct and e.tier in ("primary", "secondary")}.values(), key=lambda e: TIER_RANK[e.tier])
    if readable:
        story.command = "python -m gta_factory new " + " ".join(f'"{e.url}"' for e in readable[:3]) + f" --format {fmt}"
        if len(readable) == 1:
            story.note = "one reputable article only: claims will be REPORTED, not CONFIRMED"
        return
    weak = [e for e in story.entries if e.direct and e.tier == "other"]
    if weak:
        story.command = "python -m gta_factory new " + " ".join(f'"{e.url}"' for e in weak[:2]) + f" --format {fmt}"
        story.note = "no reputable outlet yet: everything would be RUMOR on screen - check it in your own game first, or wait for IGN / VGC / Eurogamer"
    else:
        story.note = "community find: check it in your own game, capture it (`footage add ... --own`), then make it"


def fresh_stories(
    settings: Any,
    *,
    hours: float | None = None,
    only: str | None = None,
    entries: list[Entry] | None = None,
    covered: list[dict[str, Any]] | None = None,
    used_urls: set[str] | None = None,
    now: datetime | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, list[Story]]:
    """{category: [Story, ...]} best first. ``entries`` / ``covered`` / ``used_urls`` default to the live sources and the story memory."""
    cfg = settings.get("fresh", {}) or {}
    categories: dict[str, dict[str, Any]] = cfg.get("categories") or {}
    hours = float(hours or cfg.get("hours") or 48)
    now = now or datetime.now(timezone.utc)
    if entries is None:
        entries = read_sources(settings, log=log)
    if covered is None or used_urls is None:
        from ...app.database.db import get_db

        db = get_db(settings.db_path)
        covered = db.list_stories() if covered is None else covered
        if used_urls is None:
            with db.connect() as con:
                used_urls = {r[0] for r in con.execute("SELECT url FROM articles").fetchall()}
    keep: dict[str, Entry] = {}
    for e in entries:
        if not e.title or not GTA_RE.search(f"{e.title} {e.snippet}"):
            continue
        age = e.age_hours(now)
        if age is not None and age > hours:
            continue
        key = canonical_url(e.url) if e.direct else e.title.lower()
        if key not in keep or TIER_RANK.get(e.tier, 9) < TIER_RANK.get(keep[key].tier, 9):
            keep[key] = e
    by_cat: dict[str, list[Entry]] = {}
    for e in keep.values():
        by_cat.setdefault(categorize(e, categories), []).append(e)
    out: dict[str, list[Story]] = {}
    for cat, items in by_cat.items():
        if only and cat != only:
            continue
        stories = []
        for members in group(items, topic_words=_TOPIC_GENERIC):
            lead = min(members, key=lambda e: (TIER_RANK.get(e.tier, 9), -(len(e.title))))
            st = Story(cat, lead.title, sorted(members, key=lambda e: TIER_RANK.get(e.tier, 9)))
            score_story(st, now, covered or [], used_urls or set())
            suggest(st, categories)
            stories.append(st)
        out[cat] = sorted(stories, key=lambda s: -s.score)
    order = list(categories) + ["news"]
    return {c: out[c] for c in sorted(out, key=lambda c: order.index(c) if c in order else 99)}


def render_text(result: dict[str, list[Story]], categories: dict[str, dict[str, Any]], *, per_category: int = 5) -> str:
    lines = []
    for cat, stories in result.items():
        label = (categories.get(cat) or {}).get("label") or cat
        lines.append(f"\n=== {label} ({len(stories)} stor{'y' if len(stories) == 1 else 'ies'}) ===")
        for s in stories[:per_category]:
            age = f"{s.age_hours:.0f} h" if s.age_hours is not None else "date ?"
            done = f"  [ALREADY MADE: {s.covered_before}]" if s.covered_before else ""
            lines.append(f"\n[{s.score:3d}] {s.title}  ({age}){done}")
            seen = []
            for e in s.entries:
                tag = f"{e.publisher} ({e.tier})"
                if tag not in seen:
                    seen.append(tag)
            lines.append("      sources: " + ", ".join(seen[:6]) + (f" +{len(seen) - 6}" if len(seen) > 6 else ""))
            if s.command:
                lines.append("      " + s.command)
            if s.note:
                lines.append("      note: " + s.note)
            community = [e for e in s.entries if e.tier == "community"][:2]
            for e in community:
                lines.append(f"      community: {e.url}")
    return "\n".join(lines).strip() or "Nothing new about GTA 6 in the sources right now."
