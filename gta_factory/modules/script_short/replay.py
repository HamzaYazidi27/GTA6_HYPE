"""Editorial labels for the Replay layout, derived from the current narration and actual source."""
from __future__ import annotations

import re
from itertools import groupby
from textwrap import shorten
from typing import Any
from urllib.parse import parse_qs, urlparse

from ...app.core.errors import ConfigError
from .plan import _LEAD_NUMBER

_METRIC = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|[kmb])?\s+(views|likes|shares|saves)\b", re.I)
_SUFFIX = {"thousand": "K", "million": "M", "billion": "B"}


def replay_cards(beats: list[dict[str, Any]], title: str) -> list[dict[str, str]]:
    """Keep each rank's headline across its narration. Counts never carry across items."""
    cards = []
    for item, members in groupby(beats, key=lambda beat: beat.get("item")):
        group = list(members)
        metrics = [_METRIC.search(beat["text"]) for beat in group]
        shared = {m.groups() for m in metrics if m}
        group_metric = next(iter(shared)) if item is not None and len(shared) == 1 else None
        subject = group[0]
        if item is not None and not _LEAD_NUMBER.sub("", subject["text"], count=1).strip():
            subject = next((b for b in group[1:] if not _METRIC.fullmatch(b["text"].strip(" .!"))), subject)
        for i, beat in enumerate(group):
            basis = subject if item is not None else beat
            text = basis["text"].strip()
            heading = _LEAD_NUMBER.sub("", text, count=1).strip() if basis.get("number") is not None else text
            if len(heading) > 45:
                clause = re.split(r"[,;:!?]|\s+[–—]\s+", heading, maxsplit=1)[0]
                heading = basis.get("headline") or shorten(clause, width=45, placeholder="…")
            card = {"headline": heading.strip(" .,:;") or title}
            metric = metrics[i].groups() if metrics[i] else group_metric
            if metric:
                value, scale, unit = metric
                scale = (scale or "").lower()
                card.update(value=value + _SUFFIX.get(scale, scale.upper()), unit=unit.upper())
            cards.append(card)
    return cards


def source_label(segment: dict[str, Any]) -> str:
    url = urlparse(segment.get("source_url") or "")
    if url.hostname in ("www.tiktok.com", "tiktok.com"):
        handle = re.match(r"/(@[\w.]+)/video/\d+", url.path)
        if handle:
            return handle[1]
    return segment.get("credit") or segment.get("creator") or ""


def split_source_links(script: str) -> tuple[str, list[dict[str, Any]]]:
    """An optional post URL at the end of a line selects its picture without entering the narration."""
    lines, refs = [], []
    words = 0
    for line in script.splitlines():
        match = re.search(r"(?:^|\s)(https?://\S+)\s*$", line)
        if match:
            url = urlparse(match[1])
            host = (url.hostname or "").lower()
            params = parse_qs(url.query)
            if host in ("www.tiktok.com", "tiktok.com") and re.fullmatch(r"/@[\w.]+/video/\d+/?", url.path):
                link = "https://www.tiktok.com" + url.path.rstrip("/")
            elif host in ("www.youtube.com", "youtube.com", "youtu.be"):
                video = params.get("v", [""])[0] if url.path == "/watch" else url.path.rsplit("/", 1)[-1]
                if not re.fullmatch(r"[\w-]{11}", video):
                    raise ConfigError("Use the URL of a specific YouTube video.")
                link = "https://www.youtube.com/watch?v=" + video
            else:
                raise ConfigError("Use a direct TikTok or YouTube video URL after the sentence.")
            offset = parse_qs(url.fragment).get("t", params.get("t", ["0"]))[0]
            if not re.fullmatch(r"\d+(?:\.\d+)?s?", offset):
                raise ConfigError("Give the clip start in seconds, for example #t=18.5.")
            line = line[:match.start()].strip()
            if not line:
                raise ConfigError("Put the original clip URL on the same line as its narration.")
            refs.append({"first": words, "last": words + len(line.split()) - 1, "url": link, "start": float(offset.rstrip("s"))})
        words += len(line.split())
        lines.append(line)
    return "\n".join(lines), refs


def linked_matches(ctx: Any, beats: list[dict[str, Any]], needs: list[float]) -> dict[int, dict[str, Any]]:
    """Use the operator's original and in-point for the associated narration. Report short coverage normally."""
    from ..footage.games import game_settings
    from ..footage.library import _cookies, _probe, _sidecar, footage_dir
    from .match import rights_of, source_fps
    from .websource import _store, defaults, playable, youtube_download

    selected = {}
    cfg = defaults(ctx.settings)
    for ref in ctx.options.get("replay_sources", []):
        indices = [i for i, b in enumerate(beats) if ref["first"] <= b["words"][0] and b["words"][1] <= ref["last"]]
        if not indices:
            ctx.warn("Replay: a source line must contain a complete narration beat; using visual search.")
            continue
        game = beats[indices[0]]["game"]
        root = footage_dir(game_settings(ctx.settings, game))
        url = ref["url"]
        def fetch(folder):
            if ctx.options.get("offline") or ctx.options.get("no_web"):
                return None, {}
            path, meta = youtube_download(url, folder, max_height=1080, max_seconds=cfg["max_video_seconds"], cookies=_cookies(ctx.settings), include_audio=True)
            return (path if path and playable(path) else None), meta

        def provenance(meta):
            who = meta.get("uploader") or meta.get("channel") or urlparse(url).path.split("/")[1]
            return {**meta, "source_url": url, "credit": f"Footage: {who}", "web_found": True,
                    "allow_third_party": True, "article_media": ctx.job_id}
        try:
            path = _store(root, "videos", url, fetch, provenance)
            if path is None:
                ctx.warn(f"Replay: original unavailable ({url}); using visual search.")
                continue
            duration = _probe(path)["duration"]
        except Exception as exc:
            ctx.warn(f"Replay: original unavailable ({url}): {exc}; using visual search.")
            continue
        side = _sidecar(path)
        start = ref["start"]
        for i in indices:
            b = beats[i]
            end = min(duration, start + needs[i])
            if end - start < 0.2 or b["game"] != game:
                continue
            segment = dict(kind="shot", file=path.relative_to(root).as_posix(), path=str(path), game=game,
                           start=round(start, 3), end=round(end, 3), shot_start=0, shot_end=duration,
                           crop=None, fps=source_fps(str(path)), prob_mean=0., prob_first=0.,
                           source_url=url, creator=side.get("uploader", ""), credit=side.get("credit", ""),
                           rights=rights_of(ctx.settings, side))
            selected[i] = dict(beat=i, text=b["text"], concept=b["concept"], game=game, specific=b["specific"],
                               need=needs[i], segments=[segment], status="source_link", confidence=0., seen="",
                               short_by=round(max(0., needs[i] - (end-start)), 3), web=True)
            start = end
    return selected
