"""Collect *public metadata* about a reference YouTube channel.

Sources used, in order of preference
------------------------------------
1. **Public tab listings** (videos / shorts / streams) read with yt-dlp in
   ``extract_flat`` mode: id, title, duration, approximate upload day and the
   rounded public view count of every listed upload.
2. **Official Atom feeds** (``/feeds/videos.xml``) for the channel and for its
   auto-generated uploads / popular playlists: exact publish time, the full
   description, view count and like count of 15 entries per feed.
3. **YouTube Data API v3** (optional, needs ``YOUTUBE_API_KEY``): exact
   statistics, tags and duration for any id. This is the officially supported
   way to get details for a larger sample.
4. **Public thumbnails** from the image CDN, cached locally for pixel
   statistics only.

Deliberate limits
-----------------
* No video or audio stream is ever downloaded.
* Watch pages are not scraped. From server networks YouTube answers them with
  a sign-in / bot check; this module does not try to get around that and does
  not accept cookies or account sessions.
* Caption tracks are therefore not read. Anything that needs the spoken
  content or the picture of a video is out of scope for automated collection
  and is listed as a manual review item in the generated reports.
* Thumbnails stay in a git-ignored cache and are never copied into ``assets/``.
"""
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

ATOM = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

# Auto-generated playlists that exist for every channel (prefix + channel id without "UC").
FEED_PLAYLISTS = {
    "recent_long": "UULF",
    "popular_long": "UULP",
    "recent_shorts": "UUSH",
    "popular_shorts": "UUPS",
    "recent_live": "UULV",
}

BREAKING_RE = re.compile(
    r"\b(just|officially|confirmed|confirms|revealed|reveals|announced|announces|breaking|delayed|delay|"
    r"trailer|release date|leak|leaked|new information|new details|update|finally)\b",
    re.I,
)
GUIDE_RE = re.compile(
    r"\b(guide|how to|explained|things you|things to|tips|tricks|before you play|should do|need to know|"
    r"best ways|top \d+|\d+ things|\d+ facts|everything you|do first)\b",
    re.I,
)
GTA6_RE = re.compile(r"\b(gta\s*6|gta\s*vi\b|grand theft auto\s*(6|vi)\b)", re.I)


def _get(url: str, *, params: dict[str, Any] | None = None, timeout: float = 30.0) -> httpx.Response | None:
    try:
        r = httpx.get(url, params=params, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError:
        return None
    return r if r.status_code == 200 else None


def list_tab(channel_url: str, tab: str) -> dict[str, Any]:
    """Return the flat public listing of one channel tab (no watch pages, no media)."""
    import yt_dlp

    url = re.sub(r"/(videos|shorts|streams|featured)/?$", "", channel_url.rstrip("/")) + f"/{tab}"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "ignoreerrors": True,
        "socket_timeout": 30,
        "extractor_args": {"youtubetab": {"approximate_date": [""]}},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    kind = {"videos": "long", "shorts": "short", "streams": "live"}[tab]
    entries = [
        {
            "id": e["id"],
            "title": e.get("title"),
            "duration": e.get("duration"),
            "view_count": e.get("view_count"),
            "timestamp": e.get("timestamp"),
            "kind": kind,
        }
        for e in (info.get("entries") or [])
        if e and e.get("id")
    ]
    channel = {
        "name": info.get("channel"),
        "channel_id": info.get("channel_id"),
        "handle": info.get("uploader_id"),
        "subscribers": info.get("channel_follower_count"),
        "verified": info.get("channel_is_verified"),
        "description": info.get("description"),
        "keywords": info.get("tags"),
    }
    return {"channel": channel, "entries": entries}


def read_feed(*, channel_id: str | None = None, playlist_id: str | None = None) -> dict[str, Any]:
    """Read one official Atom feed. Returns ``{"channel_published": iso, "entries": [...]}``."""
    params = {"channel_id": channel_id} if channel_id else {"playlist_id": playlist_id}
    r = _get("https://www.youtube.com/feeds/videos.xml", params=params)
    if r is None:
        return {"channel_published": None, "entries": []}
    root = ET.fromstring(r.text)
    out = []
    for e in root.findall("a:entry", ATOM):
        group = e.find("media:group", ATOM)
        community = group.find("media:community", ATOM) if group is not None else None
        stats = community.find("media:statistics", ATOM) if community is not None else None
        rating = community.find("media:starRating", ATOM) if community is not None else None
        out.append(
            {
                "id": e.findtext("yt:videoId", namespaces=ATOM),
                "title": e.findtext("a:title", namespaces=ATOM),
                "published": e.findtext("a:published", namespaces=ATOM),
                "updated": e.findtext("a:updated", namespaces=ATOM),
                "description": group.findtext("media:description", namespaces=ATOM) if group is not None else None,
                "view_count": int(stats.get("views")) if stats is not None and stats.get("views") else None,
                "like_count": int(rating.get("count")) if rating is not None and rating.get("count") else None,
            }
        )
    return {"channel_published": root.findtext("a:published", namespaces=ATOM), "entries": out}


def data_api_details(video_ids: list[str], api_key: str) -> dict[str, dict[str, Any]]:
    """Optional: exact details through the official YouTube Data API v3."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(video_ids), 50):
        r = _get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet,statistics,contentDetails", "id": ",".join(video_ids[i : i + 50]), "key": api_key},
        )
        if r is None:
            continue
        for item in r.json().get("items", []):
            sn, st = item.get("snippet", {}), item.get("statistics", {})
            out[item["id"]] = {
                "id": item["id"],
                "title": sn.get("title"),
                "published": sn.get("publishedAt"),
                "description": sn.get("description"),
                "tags": sn.get("tags") or [],
                "category_id": sn.get("categoryId"),
                "view_count": int(st["viewCount"]) if st.get("viewCount") else None,
                "like_count": int(st["likeCount"]) if st.get("likeCount") else None,
                "comment_count": int(st["commentCount"]) if st.get("commentCount") else None,
                "iso_duration": item.get("contentDetails", {}).get("duration"),
            }
    return out


def select_samples(videos: list[dict[str, Any]], shorts: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Named id lists used by the analysis (listing order of ``videos`` is newest first)."""
    now = time.time()
    by_views = sorted(videos, key=lambda v: v.get("view_count") or 0, reverse=True)
    last_year = [v for v in videos if v.get("timestamp") and now - v["timestamp"] < 365 * 86400]
    gta6 = [v for v in videos if GTA6_RE.search(v.get("title") or "")]
    breaking = [v for v in gta6 if BREAKING_RE.search(v["title"]) and not GUIDE_RE.search(v["title"])]
    guides = [v for v in gta6 if GUIDE_RE.search(v["title"])]
    return {
        "recent_30": [v["id"] for v in videos[:30]],
        "recent_100": [v["id"] for v in videos[:100]],
        "top_20_all_time": [v["id"] for v in by_views[:20]],
        "top_20_last_12_months": [
            v["id"] for v in sorted(last_year, key=lambda v: v.get("view_count") or 0, reverse=True)[:20]
        ],
        "gta6_breaking_news_recent_15": [v["id"] for v in breaking[:15]],
        "gta6_guides_recent_15": [v["id"] for v in guides[:15]],
        "recent_shorts_30": [v["id"] for v in shorts[:30]],
        "top_shorts_15": [v["id"] for v in sorted(shorts, key=lambda v: v.get("view_count") or 0, reverse=True)[:15]],
    }


def fetch_thumbnail(video_id: str, dest: Path) -> bool:
    if dest.exists():
        return True
    for name in ("maxresdefault", "sddefault", "hqdefault"):
        r = _get(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg")
        if r is not None and len(r.content) > 2500:  # the CDN serves a tiny grey placeholder for missing sizes
            dest.write_bytes(r.content)
            return True
    return False


def collect_channel(
    channel_url: str,
    cache_dir: Path,
    *,
    refresh: bool = False,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run the collection and return the dataset (also written to ``cache_dir/dataset.json``)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "thumbs").mkdir(exist_ok=True)

    listings: dict[str, list[dict[str, Any]]] = {}
    channel: dict[str, Any] = {}
    for tab in ("videos", "shorts", "streams"):
        cache = cache_dir / f"listing_{tab}.json"
        if cache.exists() and not refresh:
            data = json.loads(cache.read_text())
        else:
            progress(f"listing /{tab} ...")
            data = list_tab(channel_url, tab)
            cache.write_text(json.dumps(data))
        listings[tab] = data["entries"]
        channel = {**channel, **{k: v for k, v in data["channel"].items() if v}}
        progress(f"/{tab}: {len(data['entries'])} public entries")

    cid = channel.get("channel_id") or ""
    feeds: dict[str, list[dict[str, Any]]] = {}
    if cid.startswith("UC"):
        main = read_feed(channel_id=cid)
        channel["created"] = main["channel_published"]
        feeds["channel_latest"] = main["entries"]
        for name, prefix in FEED_PLAYLISTS.items():
            feeds[name] = read_feed(playlist_id=prefix + cid[2:])["entries"]
            time.sleep(0.4)
        progress("official feeds: " + ", ".join(f"{k}={len(v)}" for k, v in feeds.items()))

    details: dict[str, dict[str, Any]] = {}
    for name, entries in feeds.items():
        for e in entries:
            if e.get("id"):
                details.setdefault(e["id"], {**e, "detail_source": "atom_feed", "feeds": []})["feeds"].append(name)

    samples = select_samples(listings["videos"], listings["shorts"])
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if api_key:
        wanted = sorted({i for ids in samples.values() for i in ids} - set(details))
        got = data_api_details(wanted, api_key)
        for vid, d in got.items():
            details[vid] = {**d, "detail_source": "youtube_data_api_v3", "feeds": []}
        progress(f"YouTube Data API details: {len(got)}")
    else:
        progress("YOUTUBE_API_KEY not set: details limited to the entries exposed by the official feeds")

    thumb_ids = sorted(
        set(samples["recent_30"])
        | set(samples["top_20_all_time"])
        | set(samples["top_20_last_12_months"])
        | set(samples["gta6_breaking_news_recent_15"])
        | set(samples["gta6_guides_recent_15"])
        | set(samples["recent_shorts_30"][:12])
    )
    ok = 0
    for vid in thumb_ids:
        ok += fetch_thumbnail(vid, cache_dir / "thumbs" / f"{vid}.jpg")
    progress(f"thumbnails cached for pixel statistics: {ok}/{len(thumb_ids)}")

    dataset = {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel_url": channel_url,
        "channel": channel,
        "listings": listings,
        "feeds": {k: [e["id"] for e in v] for k, v in feeds.items()},
        "details": details,
        "samples": samples,
        "thumbnail_ids": thumb_ids,
    }
    (cache_dir / "dataset.json").write_text(json.dumps(dataset))
    return dataset


if __name__ == "__main__":  # pragma: no cover - manual utility
    import sys

    collect_channel(sys.argv[1], Path(sys.argv[2]))
