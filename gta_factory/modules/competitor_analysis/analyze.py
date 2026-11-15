"""Turn the collected public metadata into measurable statistics.

Every number written by this module can be traced to the dataset produced by
:mod:`collect`. Nothing is estimated from memory. Where a statistic rests on a
small sample (for example exact publish hours, which only the official feeds
expose) the sample size is stored next to it.
"""
from __future__ import annotations

import json
import math
import re
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .collect import BREAKING_RE, GTA6_RE, GUIDE_RE

WORD_RE = re.compile(r"[A-Za-z0-9']+")
CAPS_RE = re.compile(r"\b[A-Z]{3,}\b")
BRAND_CAPS = {"GTA", "RDR", "RDR2", "GTA5", "GTA6", "NPC", "NPCS", "DLC", "RAGE", "PS5", "PC", "RP", "VI", "II", "III", "IV", "USA", "HD", "AI"}
URL_RE = re.compile(r"https?://\S+")
TIMESTAMP_RE = re.compile(r"^\s*\(?\d{1,2}:\d{2}(?::\d{2})?\)?\s+\S", re.M)

TOPIC_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("gta6_guide_or_list", re.compile(r"(gta\s*6|gta\s*vi\b).*(guide|explained|things|facts|tips|how to|before you|do first)|(guide|things|facts|explained).*(gta\s*6|gta\s*vi\b)", re.I)),
    ("gta6_news_or_info", GTA6_RE),
    ("gta_online_update", re.compile(r"gta\s*(online|5|v\b).*(update|dlc)|(update|dlc).*gta\s*(online|5)", re.I)),
    ("gta_online_money_or_guide", re.compile(r"gta\s*(online|5|v\b)", re.I)),
    ("red_dead", re.compile(r"red dead|rdr\s*2?|rdo\b", re.I)),
]

TITLE_FEATURES: dict[str, re.Pattern[str]] = {
    "ends_with_ellipsis": re.compile(r"(\.\.\.|…)\s*$"),
    "has_exclamation": re.compile(r"!"),
    "has_question": re.compile(r"\?"),
    "has_number": re.compile(r"\b\d+\b(?!\s*$)"),
    "starts_with_number": re.compile(r"^\s*(top\s+)?\d+\b", re.I),
    "has_parentheses": re.compile(r"\("),
    "has_emphasis_caps_word": re.compile(r"$^"),  # filled programmatically
    "mentions_rockstar": re.compile(r"\brockstar\b", re.I),
    "uses_just": re.compile(r"\bjust\b", re.I),
    "uses_finally": re.compile(r"\bfinally\b", re.I),
    "uses_explained": re.compile(r"\bexplained\b", re.I),
    "second_person": re.compile(r"\b(you|your|you'll|you're)\b", re.I),
    "superlative_or_scale": re.compile(r"\b(biggest|massive|huge|insane|ton|entire|everything|every|most|best|worst)\b", re.I),
    "secrecy_or_gap": re.compile(r"\b(hiding|secret|hidden|no one|nobody|anyone realizes|didn't know|something|truth|actually)\b", re.I),
    "risk_or_loss": re.compile(r"\b(regret|mistake|too far|taking away|problem|worried|warning|don't|never|gone|going away|miss)\b", re.I),
}


def _median(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return float(st.median(xs)) if xs else None


def _pct(xs: list[float], q: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return float(xs[lo] + (xs[hi] - xs[lo]) * (k - lo))


def emphasis_caps(title: str) -> list[str]:
    """ALL-CAPS words used for emphasis (brand abbreviations excluded)."""
    return [w for w in CAPS_RE.findall(title or "") if w not in BRAND_CAPS]


def topic_of(title: str) -> str:
    for name, rx in TOPIC_RULES:
        if rx.search(title or ""):
            return name
    return "other"


def title_skeleton(title: str) -> str:
    """Abstract a title into a reusable pattern, e.g. ``GTA 6 Just Got a <CAPS> <w> <w>...``."""
    t = re.sub(r"(\.\.\.|…)\s*$", " <ELLIPSIS>", title.strip())
    out = []
    for i, tok in enumerate(t.split()):
        core = tok.strip(".,!?:()\"'*")
        if tok == "<ELLIPSIS>":
            out.append("…")
        elif re.fullmatch(r"\d+", core):
            out.append("<N>")
        elif core.upper() in {"GTA", "6", "VI", "ONLINE", "ROCKSTAR"} or (i < 2 and core.lower() in {"gta", "rockstar"}):
            out.append(core.upper() if core.lower() != "rockstar" else "Rockstar")
        elif core in BRAND_CAPS:
            out.append(core)
        elif CAPS_RE.fullmatch(core):
            out.append("<CAPS>")
        else:
            out.append(core)
    return " ".join(out)


def _opening(title: str, n: int) -> str:
    toks = WORD_RE.findall(title)
    return " ".join(toks[:n]).lower()


def analyze_titles(videos: list[dict[str, Any]]) -> dict[str, Any]:
    titles = [v["title"] for v in videos if v.get("title")]
    views = [v.get("view_count") or 0 for v in videos if v.get("title")]
    base = _median(views) or 1.0
    feats = {}
    for name, rx in TITLE_FEATURES.items():
        if name == "has_emphasis_caps_word":
            mask = [bool(emphasis_caps(t)) for t in titles]
        else:
            mask = [bool(rx.search(t)) for t in titles]
        with_v = [v for v, m in zip(views, mask) if m]
        without_v = [v for v, m in zip(views, mask) if not m]
        feats[name] = {
            "share": round(sum(mask) / max(1, len(mask)), 3),
            "n_with": len(with_v),
            "median_views_with": _median(with_v),
            "median_views_without": _median(without_v),
            "lift_vs_sample_median": round((_median(with_v) or 0) / base, 2) if with_v else None,
        }
    caps = Counter(w for t in titles for w in emphasis_caps(t))
    caps_views: dict[str, list[int]] = defaultdict(list)
    for t, v in zip(titles, views):
        for w in set(emphasis_caps(t)):
            caps_views[w].append(v)
    lengths = [len(t) for t in titles]
    words = [len(WORD_RE.findall(t)) for t in titles]
    caps_per_title = [len(emphasis_caps(t)) for t in titles]
    return {
        "n_titles": len(titles),
        "median_views_of_sample": base,
        "length_chars": {"median": _median(lengths), "p10": _pct(lengths, 0.1), "p90": _pct(lengths, 0.9)},
        "length_words": {"median": _median(words), "p10": _pct(words, 0.1), "p90": _pct(words, 0.9)},
        "emphasis_caps_words_per_title": dict(sorted(Counter(caps_per_title).items())),
        "features": feats,
        "top_emphasis_words": [
            {"word": w, "count": c, "median_views": _median(caps_views[w])} for w, c in caps.most_common(30)
        ],
        "top_opening_2_words": Counter(_opening(t, 2) for t in titles).most_common(15),
        "top_opening_3_words": Counter(_opening(t, 3) for t in titles).most_common(20),
        "top_opening_4_words": Counter(_opening(t, 4) for t in titles).most_common(20),
        "top_skeleton_prefixes": Counter(" ".join(title_skeleton(t).split()[:5]) for t in titles).most_common(20),
    }


def analyze_cadence(videos: list[dict[str, Any]], shorts: list[dict[str, Any]], now_ts: float) -> dict[str, Any]:
    def per_window(items: list[dict[str, Any]], days: int) -> dict[str, Any]:
        sel = [v for v in items if v.get("timestamp") and 0 <= now_ts - v["timestamp"] < days * 86400]
        return {"uploads": len(sel), "per_day": round(len(sel) / days, 2), "per_week": round(len(sel) / days * 7, 1)}

    monthly: Counter[str] = Counter()
    for v in videos:
        if v.get("timestamp"):
            monthly[datetime.fromtimestamp(v["timestamp"], timezone.utc).strftime("%Y-%m")] += 1
    # Beyond ~11 months the listing only says "1 year ago", "2 years ago", ... so those uploads collapse into
    # one bucket per year. Month-level counts are therefore only reported for the last 11 months.
    months = sorted(monthly)[-11:]
    return {
        "note": "Upload days come from the tab listing, where YouTube exposes only a relative date ('3 days ago', "
        "'4 months ago', '2 years ago'). Day resolution holds for about a month, month resolution for 11 months; "
        "older uploads cannot be dated from the listing.",
        "long_form": {f"last_{d}_days": per_window(videos, d) for d in (7, 30, 90, 180, 330)},
        "shorts": {
            "note": "The Shorts tab listing exposes no dates, so cadence cannot be computed from it.",
            "total_listed": len(shorts),
        },
        "long_form_uploads_by_month_last_11": {m: monthly[m] for m in months},
    }


def analyze_timing(details: dict[str, dict[str, Any]], listing_kind: dict[str, str]) -> dict[str, Any]:
    hours: Counter[int] = Counter()
    weekdays: Counter[str] = Counter()
    rows = []
    for d in details.values():
        if not d.get("published") or listing_kind.get(d["id"]) != "long":
            continue
        dt = datetime.fromisoformat(d["published"].replace("Z", "+00:00")).astimezone(timezone.utc)
        hours[dt.hour] += 1
        weekdays[dt.strftime("%a")] += 1
        rows.append(dt)
    gaps = []
    rows.sort()
    for a, b in zip(rows, rows[1:]):
        g = (b - a).total_seconds() / 3600
        if g < 72:
            gaps.append(g)
    return {
        "sample_size": len(rows),
        "note": "Exact publish times are only exposed by the official feeds (15 entries per feed).",
        "publish_hour_utc_histogram": dict(sorted(hours.items())),
        "weekday_histogram": dict(weekdays),
        "median_gap_hours_between_consecutive_recent_uploads": _median(gaps),
    }


def analyze_durations(videos: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = [(0, 480, "<8 min"), (480, 600, "8-10 min"), (600, 720, "10-12 min"), (720, 900, "12-15 min"), (900, 1200, "15-20 min"), (1200, 3600, "20-60 min"), (3600, 10**9, ">60 min")]
    durs = [v["duration"] for v in videos if v.get("duration")]
    out_b = {}
    for lo, hi, name in buckets:
        sel = [v for v in videos if v.get("duration") and lo <= v["duration"] < hi]
        out_b[name] = {"count": len(sel), "share": round(len(sel) / max(1, len(durs)), 3), "median_views": _median([v.get("view_count") or 0 for v in sel])}
    return {
        "n": len(durs),
        "median_seconds": _median(durs),
        "p10_seconds": _pct(durs, 0.1),
        "p25_seconds": _pct(durs, 0.25),
        "p75_seconds": _pct(durs, 0.75),
        "p90_seconds": _pct(durs, 0.9),
        "buckets": out_b,
    }


def analyze_topics(videos: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in videos:
        groups[topic_of(v.get("title") or "")].append(v)
    return {
        k: {
            "count": len(g),
            "share": round(len(g) / max(1, len(videos)), 3),
            "median_views": _median([x.get("view_count") or 0 for x in g]),
            "p90_views": _pct([x.get("view_count") or 0 for x in g], 0.9),
            "median_duration_seconds": _median([x.get("duration") for x in g if x.get("duration")]),
        }
        for k, g in sorted(groups.items(), key=lambda kv: -len(kv[1]))
    }


def analyze_descriptions(details: dict[str, dict[str, Any]], listing_kind: dict[str, str]) -> dict[str, Any]:
    rows = [d for d in details.values() if d.get("description") and listing_kind.get(d["id"]) == "long"]
    if not rows:
        return {"sample_size": 0}
    def first_block(desc: str) -> str:
        return desc.strip().split("\n\n")[0]
    lens = [len(d["description"]) for d in rows]
    return {
        "sample_size": len(rows),
        "note": "Descriptions are read from the official feeds; only structural features are stored, not the text.",
        "length_chars": {"median": _median(lens), "p10": _pct(lens, 0.1), "p90": _pct(lens, 0.9)},
        "median_links": _median([len(URL_RE.findall(d["description"])) for d in rows]),
        "share_with_chapter_timestamps": round(sum(bool(TIMESTAMP_RE.search(d["description"])) for d in rows) / len(rows), 2),
        "share_with_hashtags": round(sum("#" in d["description"] for d in rows) / len(rows), 2),
        "median_hashtags": _median([len(re.findall(r"(?<!\w)#\w+", d["description"])) for d in rows]),
        "median_first_block_chars": _median([len(first_block(d["description"])) for d in rows]),
        "share_first_line_repeats_title_keywords": round(
            sum(
                len(set(WORD_RE.findall(d["title"].lower())) & set(WORD_RE.findall(first_block(d["description"]).lower())))
                >= max(2, len(set(WORD_RE.findall(d["title"].lower()))) // 2)
                for d in rows
            )
            / len(rows),
            2,
        ),
        "share_with_subscribe_cta_in_first_block": round(sum(bool(re.search(r"subscribe|like", first_block(d["description"]), re.I)) for d in rows) / len(rows), 2),
        "share_with_membership_or_merch_links": round(sum(bool(re.search(r"/join|merch|teespring|member", d["description"], re.I)) for d in rows) / len(rows), 2),
        "share_with_source_links_to_news_outlets": round(
            sum(bool(re.search(r"rockstargames\.com|ign\.com|gamespot|eurogamer|videogameschronicle|take2games|kotaku|pcgamer", d["description"], re.I)) for d in rows) / len(rows), 2
        ),
        "share_with_keyword_block": round(sum(len(re.findall(r"gta\s*6", d["description"], re.I)) >= 8 for d in rows) / len(rows), 2),
    }


def analyze_engagement(details: dict[str, dict[str, Any]], listing_kind: dict[str, str]) -> dict[str, Any]:
    out = {}
    for kind in ("long", "short"):
        rows = [d for d in details.values() if d.get("like_count") and d.get("view_count") and listing_kind.get(d["id"]) == kind]
        ratios = [d["like_count"] / d["view_count"] for d in rows if d["view_count"] > 0]
        out[kind] = {
            "sample_size": len(rows),
            "median_like_rate": round(_median(ratios), 4) if ratios else None,
            "p10_like_rate": round(_pct(ratios, 0.1), 4) if ratios else None,
            "p90_like_rate": round(_pct(ratios, 0.9), 4) if ratios else None,
        }
    out["note"] = "Like and exact view counts come from the official feeds. Comment counts are not exposed without the Data API."
    return out


def thumbnail_stats(path: Path) -> dict[str, Any] | None:
    """Pixel statistics of one cached thumbnail (palette, brightness, layout energy)."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:  # pragma: no cover
        return None
    try:
        im = Image.open(path).convert("RGB").resize((320, 180))
    except OSError:
        return None
    rgb = np.asarray(im, dtype=np.float32) / 255.0
    hsv = np.asarray(im.convert("HSV"), dtype=np.float32) / 255.0
    lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    sat, hue = hsv[..., 1], hsv[..., 0] * 360.0
    names = [("red", 345, 15), ("orange", 15, 45), ("yellow", 45, 70), ("green", 70, 165), ("cyan", 165, 200), ("blue", 200, 255), ("purple", 255, 290), ("magenta_pink", 290, 345)]
    vivid = sat > 0.45
    hue_share = {}
    for name, lo, hi in names:
        m = ((hue >= lo) | (hue < hi)) if lo > hi else ((hue >= lo) & (hue < hi))
        hue_share[name] = round(float((m & vivid).mean()), 3)
    gy, gx = np.gradient(lum)
    edges = np.hypot(gx, gy)
    thirds = [float(edges[:, i * 107 : (i + 1) * 107].mean()) for i in range(3)]
    total = sum(thirds) or 1.0
    # near-white / saturated-yellow pixels are the usual carriers of overlaid text and outlines
    white = (lum > 0.88) & (sat < 0.18)
    yellow = (hue > 40) & (hue < 68) & (sat > 0.6) & (lum > 0.6)
    return {
        "brightness": round(float(lum.mean()), 3),
        "contrast_rms": round(float(lum.std()), 3),
        "saturation": round(float(sat.mean()), 3),
        "vivid_pixel_share": round(float(vivid.mean()), 3),
        "hue_share_of_vivid_pixels": hue_share,
        "edge_energy_left_center_right": [round(t / total, 3) for t in thirds],
        "near_white_share": round(float(white.mean()), 3),
        "saturated_yellow_share": round(float(yellow.mean()), 3),
        "dark_pixel_share": round(float((lum < 0.12).mean()), 3),
    }


def analyze_thumbnails(cache_dir: Path, ids: list[str]) -> dict[str, Any]:
    per = {}
    for vid in ids:
        p = cache_dir / "thumbs" / f"{vid}.jpg"
        if p.exists():
            s = thumbnail_stats(p)
            if s:
                per[vid] = s
    if not per:
        return {"sample_size": 0}
    keys = ["brightness", "contrast_rms", "saturation", "vivid_pixel_share", "near_white_share", "saturated_yellow_share", "dark_pixel_share"]
    agg = {k: {"median": round(_median([s[k] for s in per.values()]), 3), "p10": round(_pct([s[k] for s in per.values()], 0.1), 3), "p90": round(_pct([s[k] for s in per.values()], 0.9), 3)} for k in keys}
    hues = defaultdict(list)
    for s in per.values():
        for h, v in s["hue_share_of_vivid_pixels"].items():
            hues[h].append(v)
    dominant = Counter(max(s["hue_share_of_vivid_pixels"], key=s["hue_share_of_vivid_pixels"].get) for s in per.values())
    edges = [s["edge_energy_left_center_right"] for s in per.values()]
    return {
        "sample_size": len(per),
        "aggregate": agg,
        "mean_hue_share_of_frame": {h: round(sum(v) / len(v), 3) for h, v in hues.items()},
        "dominant_vivid_hue_counts": dict(dominant.most_common()),
        "mean_edge_energy_left_center_right": [round(sum(e[i] for e in edges) / len(edges), 3) for i in range(3)],
        "per_video": per,
    }


def build_database(dataset: dict[str, Any], cache_dir: Path) -> dict[str, Any]:
    """Compute every statistic and assemble ``competitor_video_database.json``."""
    videos = dataset["listings"]["videos"]
    shorts = dataset["listings"]["shorts"]
    streams = dataset["listings"]["streams"]
    details = dataset["details"]
    samples = dataset["samples"]
    kind = {v["id"]: v["kind"] for v in videos + shorts + streams}
    by_id = {v["id"]: v for v in videos + shorts + streams}
    now_ts = datetime.fromisoformat(dataset["collected_at"]).timestamp()

    recent100 = videos[:100]
    recent300 = videos[:300]
    gta6_era = [v for v in videos[:600] if GTA6_RE.search(v.get("title") or "")]

    def record(vid: str) -> dict[str, Any]:
        v = by_id.get(vid, {"id": vid})
        d = details.get(vid, {})
        title = v.get("title") or d.get("title") or ""
        ts = v.get("timestamp")
        return {
            "id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "kind": v.get("kind"),
            "title": title,
            "duration_seconds": v.get("duration"),
            "views_listing_rounded": v.get("view_count"),
            "views_exact": d.get("view_count"),
            "likes": d.get("like_count"),
            "published_exact_utc": d.get("published"),
            "upload_day_approx": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else None,
            "topic": topic_of(title),
            "is_breaking_news_title": bool(GTA6_RE.search(title) and BREAKING_RE.search(title) and not GUIDE_RE.search(title)),
            "is_guide_title": bool(GTA6_RE.search(title) and GUIDE_RE.search(title)),
            "title_skeleton": title_skeleton(title) if title else None,
            "emphasis_caps_words": emphasis_caps(title),
            "samples": [name for name, ids in samples.items() if vid in ids],
            "detail_source": d.get("detail_source"),
        }

    sample_ids = []
    for ids in samples.values():
        for i in ids:
            if i not in sample_ids:
                sample_ids.append(i)

    stats = {
        "cadence": analyze_cadence(videos, shorts, now_ts),
        "publish_timing": analyze_timing(details, kind),
        "durations_recent_100_long": analyze_durations(recent100),
        "durations_all_long": analyze_durations(videos),
        "durations_gta6_breaking_titles": analyze_durations([v for v in gta6_era if BREAKING_RE.search(v["title"]) and not GUIDE_RE.search(v["title"])]),
        "durations_gta6_guide_titles": analyze_durations([v for v in gta6_era if GUIDE_RE.search(v["title"])]),
        "topics_recent_100_long": analyze_topics(recent100),
        "topics_recent_300_long": analyze_topics(recent300),
        "views": {
            "median_recent_30_long": _median([v.get("view_count") or 0 for v in videos[:30]]),
            "median_recent_100_long": _median([v.get("view_count") or 0 for v in recent100]),
            "p90_recent_100_long": _pct([v.get("view_count") or 0 for v in recent100], 0.9),
            "median_all_long": _median([v.get("view_count") or 0 for v in videos]),
            "median_shorts": _median([v.get("view_count") or 0 for v in shorts]),
            "p90_shorts": _pct([v.get("view_count") or 0 for v in shorts], 0.9),
            "note": "Listing view counts are rounded by YouTube (e.g. 275K). Videos younger than ~3 days are still accumulating views.",
        },
        "titles_recent_300_long": analyze_titles(recent300),
        "titles_gta6_era": analyze_titles(gta6_era),
        "titles_top_100_all_time": analyze_titles(sorted(videos, key=lambda v: v.get("view_count") or 0, reverse=True)[:100]),
        "titles_shorts": analyze_titles(shorts),
        "descriptions": analyze_descriptions(details, kind),
        "engagement": analyze_engagement(details, kind),
        "thumbnails": analyze_thumbnails(cache_dir, dataset.get("thumbnail_ids", [])),
    }
    ch = dataset["channel"]
    return {
        "schema_version": 1,
        "collected_at": dataset["collected_at"],
        "collection_method": {
            "listing": "public channel tabs via yt-dlp extract_flat (no watch pages, no media)",
            "details": "official Atom feeds (/feeds/videos.xml), 15 entries per feed"
            + (" + YouTube Data API v3" if any(d.get("detail_source") == "youtube_data_api_v3" for d in details.values()) else ""),
            "thumbnails": "public image CDN, local cache only",
            "not_collected": [
                "caption tracks and spoken content (watch pages answer server networks with a sign-in check; not bypassed)",
                "video and audio streams (never downloaded by design)",
                "comment counts, tags and retention data (need the Data API or channel-owner analytics)",
            ],
        },
        "channel": {
            "name": ch.get("name") or ch.get("channel"),
            "handle": ch.get("handle"),
            "channel_id": ch.get("channel_id"),
            "url": dataset["channel_url"],
            "subscribers_rounded": ch.get("subscribers"),
            "verified": ch.get("verified"),
            "created": ch.get("created"),
            "public_long_form_uploads": len(videos),
            "public_shorts": len(shorts),
            "public_past_streams": len(streams),
            "self_description_keywords": ch.get("keywords"),
        },
        "samples": samples,
        "statistics": stats,
        "videos": [record(i) for i in sample_ids],
    }


def run(cache_dir: Path, out_dir: Path) -> dict[str, Any]:
    dataset = json.loads((cache_dir / "dataset.json").read_text())
    db = build_database(dataset, cache_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "competitor_video_database.json").write_text(json.dumps(db, indent=1, ensure_ascii=False))
    return db


if __name__ == "__main__":  # pragma: no cover - manual utility
    import sys

    db = run(Path(sys.argv[1]), Path(sys.argv[2]))
    print(json.dumps({k: v for k, v in db["statistics"].items() if k != "thumbnails"}, indent=1)[:6000])
