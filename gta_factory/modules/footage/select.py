"""Put real GTA 6 footage behind the motion graphics: 80-90 % of the video, and what is said is what is shown.

**Share.** ``footage.ratio`` (default 0.85) is the target share of the narration
time shown over real footage; the edit stays between ``footage.ratio_min`` (0.8)
and ``footage.ratio_max`` (0.9). The other scenes stay pure motion graphics: the
ones that read best on a clean backdrop (bars, sources, timeline...), first those
that do not name anything the library can show.

**What is said is shown.** Each scene's narration is read for concepts
(``vocabulary.py``): "Lucia", "Vice City", "the cops", "an alligator", "the second
trailer", or any tag you put on a file. When the library has footage of it, that
shot or screenshot is cut in *at the word* (from the word timings of the
timeline step) and held while it is talked about. The rest of the scene is filled
with trailer footage that follows the trailer's own edit; when nothing in a
scene matches, any footage is fine (least used first, so nothing repeats while
the library still has unused shots).

Selection is deterministic for a given storyboard and library, so re-running a
step gives the same edit (and the render cache keeps working).

1. ``assign_footage`` (rights step): which scenes get footage, the matching
   shot / screenshot for every mention ("beats") and the filler pieces.
2. ``cut_scene_clips`` (timeline step, once the real word timings are known):
   ``build_segments`` places each beat at its word, then ffmpeg renders one
   silent H.264 clip per scene at the output size (cover-cropped for 9:16) that
   Remotion plays with ``<OffthreadVideo>``. Screenshots inside a clip get a slow pan.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Callable

from ...app.core.errors import RenderError
from ...app.core.utils import stable_hash
from .library import DEFAULT_CREDIT, footage_dir, library_summary, load_items
from .vocabulary import load_vocabulary, word_tokens

# templates whose content is dense enough to read best on a clean graphic backdrop,
# in the order they are kept graphics-only when the ratio leaves room for some
GRAPHICS_FIRST = ["bars", "sources", "versus", "timeline", "takeaway", "headline", "quote", "stat", "bullets", "countdown", "claim"]
ALWAYS_FOOTAGE = {"title", "outro"}
GROUP_WORDS = 8  # mentions this close together are shown as one beat when one picture has them all


def _h(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()


def _est_seconds(sc: dict[str, Any], wpm: float) -> float:
    return max(1.5, len(sc.get("narration", "").split()) / wpm * 60.0 + 0.4)


def ratio_band(settings: Any) -> tuple[float, float, float]:
    """(target, min, max) share of the video on real footage."""
    cfg = settings.get("footage", {}) or {}
    ratio = max(0.0, min(1.0, float(cfg.get("ratio", 0.85))))
    lo = max(0.0, min(ratio, float(cfg.get("ratio_min", min(ratio, 0.8)))))
    hi = min(1.0, max(ratio, float(cfg.get("ratio_max", max(ratio, 0.9)))))
    return ratio, lo, hi


def _pick_scenes(scenes: list[dict[str, Any]], ratio: float, wpm: float, *, lo: float | None = None, hi: float | None = None, matched: set[int] | frozenset[int] = frozenset()) -> set[int]:
    """Scene numbers that get footage, so that footage covers about ``ratio`` (within [lo, hi]) of the time.

    Graphics-only, in this order: dense templates that name nothing the library has (up to the target),
    then, only when footage would still exceed ``hi``: other templates, then scenes that do name something.
    The first scene (the hook), the title and the outro always get footage.
    """
    lo = ratio if lo is None else min(lo, ratio)
    hi = ratio if hi is None else max(hi, ratio)
    dur = {sc["scene_number"]: _est_seconds(sc, wpm) for sc in scenes}
    total = sum(dur.values()) or 1.0
    target_g, min_g, max_g = (1.0 - ratio) * total, (1.0 - hi) * total, (1.0 - lo) * total
    eligible = {sc["scene_number"] for sc in scenes if not (sc["template"] == "broll" and sc.get("media_file"))}
    rank = {t: i for i, t in enumerate(GRAPHICS_FIRST)}

    def tier(sc: dict[str, Any]) -> tuple[int, int]:
        hit = sc["scene_number"] in matched
        if sc["template"] in rank:
            return (2 if hit else 0, rank[sc["template"]])
        return (3 if hit else 1, 0)

    cands = sorted((sc for sc in scenes[1:] if sc["scene_number"] in eligible and sc["template"] not in ALWAYS_FOOTAGE), key=lambda s: (tier(s), s["scene_number"]))
    keep_graphics: set[int] = set()
    used = 0.0
    for sc in cands:
        if used >= target_g - 1e-6:
            break
        if tier(sc)[0] > 0 and used >= min_g - 1e-6:
            continue  # only to stay under ratio_max
        d = dur[sc["scene_number"]]
        if used + d <= max_g + 0.5:
            keep_graphics.add(sc["scene_number"])
            used += d
    return {n for n in eligible if n not in keep_graphics}


def _wanted(concepts: list[str], vocab: Any) -> dict[str, float]:
    want = {c: 1.0 for c in concepts}
    for c in concepts:
        for a in vocab.also(c):
            want.setdefault(a, 0.5)
    return want


def _score(item: dict[str, Any], want: dict[str, float]) -> float:
    return sum(item["tags"].get(c, 0.0) * f for c, f in want.items())


def _piece(item: dict[str, Any], max_clip: float) -> dict[str, Any]:
    if item["kind"] == "photo":
        return {"type": "photo", "file": item["path"]}
    length = min(max_clip, item["e"] - item["s"])
    off = max(0.0, (item["e"] - item["s"] - length) / 2)  # the middle of a long shot: the most "settled" part of the move
    return {"type": "shot", "file": item["path"], "s": item["s"], "e": item["e"], "start": round(item["s"] + off, 3), "end": round(item["s"] + off + length, 3)}


def plan_beats(text: str, items: dict[str, Any], *, min_piece: float = 1.2) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mentions of ``text`` the library can show (grouped when one picture shows them all), and those it cannot."""
    vocab = items["vocab"]
    known = load_vocabulary_ids(items)
    pool = [it for it in items["shots"] if it["e"] - it["s"] >= min_piece] + items["photos"]
    beats: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for m in vocab.mentions(text):
        want = _wanted(m["concepts"], vocab)
        if not any(_score(it, want) > 0 for it in pool):
            if any(c in known for c in m["concepts"]):
                missing.append({"said": m["said"], "concepts": m["concepts"]})
            continue
        if beats and m["word"] - beats[-1]["last_word"] <= GROUP_WORDS:
            merged = list(dict.fromkeys(beats[-1]["concepts"] + m["concepts"]))
            full = {c: 1.0 for c in merged}
            if any(all(it["tags"].get(c, 0) > 0 for c in full) for it in pool):
                beats[-1].update(concepts=merged, said=beats[-1]["said"] + " / " + m["said"], last_word=m["word"])
                continue
        beats.append({"word": m["word"], "last_word": m["word"], "said": m["said"], "concepts": m["concepts"]})
    return beats, missing


def load_vocabulary_ids(items: dict[str, Any]) -> set[str]:
    """Concepts of the vocabulary files (not the ones made up from library tags): only those are reported as missing."""
    return set(items.get("known") or [])


def _candidates(pool: list[dict[str, Any]], want: dict[str, float]) -> list[tuple[float, dict[str, Any]]]:
    """Items that show what is wanted, with their score. Related concepts ("also") only when nothing shows it directly."""
    scored = [(s, it) for it in pool if (s := _score(it, want)) > 0]
    direct = [(s, it) for s, it in scored if any(it["tags"].get(c) for c, f in want.items() if f >= 1.0)]
    return direct or scored


def best_item(want: dict[str, float], pool: list[dict[str, Any]], used: dict[str, int], seed: str, prefer: str = "shot") -> tuple[dict[str, Any] | None, float]:
    scored = _candidates(pool, want)
    if not scored:
        return None, 0.0
    top = max(s for s, _ in scored)
    # a manual tag (3) and an auto tag (2) are both a real match: prefer variety among good matches
    good = [(s, it) for s, it in scored if s >= top * 0.66]
    s, it = min(good, key=lambda x: (used.get(x[1]["key"], 0), -x[0], 0 if x[1]["kind"] == ("shot" if prefer == "shot" else "photo") else 1, _h(seed, x[1]["key"])))
    return it, s


class _Library:
    """One footage library (GTA 6, or another game of a comparison) while footage is assigned to the scenes."""

    def __init__(self, settings: Any, gid: str, *, ledger: dict[str, dict[str, Any]], cfg: dict[str, Any], max_clip: float, min_piece: float, photo_share: float):
        self.settings = settings
        self.gid = gid
        self.items = load_items(settings)
        self.items["known"] = set(load_vocabulary(settings).concepts)
        self.summary = library_summary(self.items["index"])
        self.shots, self.photos = self.items["shots"], self.items["photos"]
        self.root = footage_dir(settings)
        self.beat_pool = [it for it in self.shots if it["e"] - it["s"] >= min_piece] + self.photos
        self.fill_shots = [it for it in self.shots if it["e"] - it["s"] >= min_piece]
        self.by_video: dict[str, list[dict[str, Any]]] = {}
        for it in self.fill_shots:
            self.by_video.setdefault(it["file"], []).append(it)
        self.used: dict[str, int] = {}
        self.ledger = ledger
        self.cfg = cfg
        self.max_clip, self.min_piece, self.photo_share = max_clip, min_piece, photo_share
        self.name = str(settings.get("footage.game_name") or "GTA 6")

    @property
    def empty(self) -> bool:
        return not self.shots and not self.photos

    def plan(self, text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return plan_beats(text, self.items, min_piece=self.min_piece)

    def ledger_add(self, it: dict[str, Any], scene_n: int) -> str:
        file_rel = it["file"]
        aid = ("gta6-" if self.gid == "gta6" else f"{self.gid}-") + hashlib.sha1(file_rel.encode()).hexdigest()[:10]
        what = "Real GTA 6 footage (footage.ratio)" if self.gid == "gta6" else f"Real {self.name} footage, shown while the comparison talks about it"
        e = self.ledger.setdefault(aid, {
            "asset_id": aid,
            "kind": "official_screenshot" if it["kind"] == "photo" else "official_trailer",
            "file_path": str(self.root / file_rel),
            "origin_url": (it.get("source_url") if it["kind"] == "photo" else it["video"].get("source_url")) or "",
            "license": "Official Rockstar Games promotional material, used for news commentary (short excerpts, transformed with graphics, credited)",
            "commercial_use": True,
            "attribution_required": True,
            "attribution": (it.get("credit") if it["kind"] == "photo" else it["video"].get("credit")) or DEFAULT_CREDIT,
            "review_status": "approved",
            "reason": f"{what}. Excerpts <= {self.max_clip:g}s, credited on screen and in the description. YouTube Content ID may still claim the video for Rockstar/Take-Two.",
            "scenes": [],
            **({"game": self.gid} if self.gid != "gta6" else {}),
        })
        if scene_n not in e["scenes"]:
            e["scenes"].append(scene_n)
        return aid

    def fill_for(self, sc: dict[str, Any], need: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Filler pieces: follow one trailer's own edit from a shot that fits the scene's words, least used first."""
        n = sc["scene_number"]
        used = self.used
        words = {t for t, _ in word_tokens(" ".join([sc.get("narration", ""), sc.get("on_screen_text", ""), sc.get("emphasis", "")]))}
        pieces: list[dict[str, Any]] = []
        its: list[dict[str, Any]] = []
        got = 0.0
        if self.fill_shots:
            start = min(self.fill_shots, key=lambda s: (-len(words & set(s["tags"])), used.get(s["key"], 0), _h(n, s["key"])))
            seq = self.by_video[start["file"]]
            j = seq.index(start)
            k = 0
            while got < need and k < len(seq) * 2:
                s = seq[(j + k) % len(seq)]
                k += 1
                if k > 1 and used.get(s["key"], 0) > used.get(start["key"], 0):
                    continue
                p = _piece(s, self.max_clip)
                pieces.append(p)
                its.append(s)
                used[s["key"]] = used.get(s["key"], 0) + 1
                got += p["end"] - p["start"]
        else:  # screenshots only: a slow slideshow
            for ph in sorted(self.photos, key=lambda p: (used.get(p["key"], 0), _h(n, p["key"]))):
                if got >= need:
                    break
                pieces.append(_piece(ph, self.max_clip))
                its.append(ph)
                used[ph["key"]] = used.get(ph["key"], 0) + 1
                got += float(self.cfg.get("photo_hold_seconds", 3.5))
        return pieces, its

    def assign(self, sc: dict[str, Any], est: float, beats_plan: list[dict[str, Any]], matched: list[dict[str, Any]], *, allow_photo: bool = True) -> dict[str, Any] | None:
        """The footage of one scene (a whole screenshot, or a clip of beats + filler), or None when the library has nothing."""
        n = sc["scene_number"]
        used = self.used
        beats: list[dict[str, Any]] = []
        its: list[dict[str, Any]] = []
        extra = {"game": self.gid} if self.gid != "gta6" else {}
        for b in beats_plan:
            it, score = best_item(_wanted(b["concepts"], self.items["vocab"]), self.beat_pool, used, f"{n}:{b['word']}", "shot")
            if it is None:
                continue
            if its and its[-1]["key"] == it["key"]:  # same picture again: keep it on screen, no new cut
                beats[-1]["said"] += " / " + b["said"]
                beats[-1]["concepts"] = list(dict.fromkeys(beats[-1]["concepts"] + b["concepts"]))
                matched[-1].update(said=beats[-1]["said"], concepts=beats[-1]["concepts"])
                continue
            used[it["key"]] = used.get(it["key"], 0) + 1
            beats.append({"word": b["word"], "said": b["said"], "concepts": b["concepts"], "piece": _piece(it, self.max_clip), "score": round(score, 2)})
            its.append(it)
            matched.append({"scene": n, "said": b["said"], "concepts": b["concepts"], "file": it["file"], "kind": it["kind"], **({"at": it["s"]} if it["kind"] == "shot" else {}), **extra})
        whole_photo = None
        if allow_photo:
            if not beats:
                want_photo = bool(self.photos) and (not self.fill_shots or (n * 0.6180339 % 1.0) < self.photo_share)
                if want_photo:
                    whole_photo = min(self.photos, key=lambda p: (used.get(p["key"], 0), _h(n, p["key"])))
                    used[whole_photo["key"]] = used.get(whole_photo["key"], 0) + 1
            elif len(beats) == 1 and beats[0]["piece"]["type"] == "photo" and beats[0]["word"] <= 2 and est <= 8.0:
                whole_photo = its[0]  # one screenshot named right at the start of a short scene: show it the whole time
        if whole_photo is not None:
            aid = self.ledger_add(whole_photo, n)
            return {"kind": "photo", "file": whole_photo["path"], "credit": whole_photo.get("credit") or DEFAULT_CREDIT, "asset_ids": [aid], "beats": beats, **extra}
        fill, fill_its = self.fill_for(sc, est * 1.5 + 2.0)
        if not fill and not beats:
            return None
        all_its = its + fill_its
        aids = list(dict.fromkeys(self.ledger_add(it, n) for it in all_its))
        first = all_its[0]
        credit = (first.get("credit") if first["kind"] == "photo" else first["video"].get("credit")) or DEFAULT_CREDIT
        return {"kind": "video", "beats": beats, "fill": fill, "credit": credit, "asset_ids": aids, **extra}


def assign_footage(settings: Any, scenes: list[dict[str, Any]], *, log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Mutates ``scenes`` (adds ``footage``) and returns {"ledger": [...], "summary": {...}}.

    Scenes that already carry a fixed piece of footage (``kind: detail``, the "details you missed"
    freeze frames) keep it and count as footage time; the others are planned as usual.

    Comparison scenes (``modules/topic``) say which library they need: ``footage_game`` (a full-screen
    scene of GTA 6 or of the other game) or ``footage_pair`` (a split screen: ``footage_b`` is the left
    panel, from the other game, and ``footage`` the right one, from GTA 6). Those videos are made of
    real footage from start to end, so the 80-90 % band does not apply to them.
    """
    from .games import game_settings

    for sc in scenes:
        if (sc.get("footage") or {}).get("kind") != "detail":
            sc.pop("footage", None)
        sc.pop("footage_b", None)
    fixed = {sc["scene_number"] for sc in scenes if sc.get("footage")}
    cfg = settings.get("footage", {}) or {}
    if not cfg.get("enabled", True):
        return {"ledger": [], "summary": {"enabled": False}}
    topic = any(sc.get("footage_game") or sc.get("footage_pair") for sc in scenes)
    ledger: dict[str, dict[str, Any]] = {}
    opts = {
        "ledger": ledger,
        "cfg": cfg,
        "max_clip": float(cfg.get("max_clip_seconds") or settings.get("rights_review.max_seconds_per_official_clip", 6)),
        "min_piece": float(cfg.get("min_piece_seconds", 1.2)),
        "photo_share": max(0.0, min(1.0, float(cfg.get("photo_share", 0.25)))),
    }
    main = _Library(settings, "gta6", **opts)
    libs: dict[str, _Library] = {"gta6": main}
    for gid in sorted({g for sc in scenes for g in [sc.get("footage_game"), *(sc.get("footage_pair") or [])] if g and g != "gta6"}):
        libs[gid] = _Library(game_settings(settings, gid), gid, **opts)
    lib = main.summary
    if all(x.empty for x in libs.values()):
        log("footage: the GTA 6 footage library is empty - run `python -m gta_factory footage update` to use real trailer footage")
        return {"ledger": [], "summary": {"enabled": True, "library": {k: v for k, v in lib.items() if k != "skipped"}, "share": 0.0}}
    for gid, x in libs.items():
        if x.empty and gid != "gta6":
            log(f"footage: the {x.name} library is empty - run `python -m gta_factory footage update --game {gid}` (its scenes stay graphics-only until then)")
    ratio, lo, hi = ratio_band(settings)
    wpm = float(settings.get("script.words_per_minute", 165))
    match_on = bool(cfg.get("match_spoken", True))

    def lib_of(gid: str | None) -> _Library:
        # never another game's pictures for GTA 6 (or the reverse): an empty library leaves its scenes graphics-only
        return libs.get(gid or "gta6") or main

    plans: dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for sc in scenes:
        n = sc["scene_number"]
        plans[n] = lib_of(sc.get("footage_game")).plan(sc.get("narration", "")) if match_on and n not in fixed and not sc.get("footage_pair") else ([], [])
    free = [sc for sc in scenes if sc["scene_number"] not in fixed]
    if topic:
        chosen = {sc["scene_number"] for sc in free}
    else:
        chosen = _pick_scenes(free, ratio, wpm, lo=lo, hi=hi, matched={n for n, (b, _) in plans.items() if b}) if free else set()

    time_total = sum(_est_seconds(sc, wpm) for sc in scenes) or 1.0
    time_footage = 0.0
    n_photo = n_video = n_split = 0
    matched: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    per_game: dict[str, int] = {}

    for sc in scenes:
        n = sc["scene_number"]
        if n in fixed:
            fo = sc["footage"]
            root = main.root
            rel = Path(fo["file"]).resolve().relative_to(root.resolve()).as_posix() if str(Path(fo["file"]).resolve()).startswith(str(root.resolve())) else Path(fo["file"]).name
            v = next((x for x in main.items["index"].get("videos", []) if x["file"] == rel), {})
            fo["asset_ids"] = [main.ledger_add({"kind": "shot", "file": rel, "video": {"credit": fo.get("credit") or v.get("credit"), "source_url": v.get("source_url", "")}}, n)]
            time_footage += _est_seconds(sc, wpm)
            n_video += 1
            continue
        beats_plan, miss = plans[n]
        x = lib_of(sc.get("footage_game"))
        missing += [{"scene": n, **m, **({"game": x.gid} if x.gid != "gta6" else {})} for m in miss]
        if n not in chosen:
            continue
        est = _est_seconds(sc, wpm)
        if sc.get("footage_pair"):  # split screen: one clip per panel, from each game's library
            left, right = (lib_of(g) for g in sc["footage_pair"])
            text = " ".join([sc.get("narration", ""), (sc.get("data") or {}).get("title") or ""])
            fo_r = right.assign(sc, est, right.plan(text)[0] if match_on else [], matched, allow_photo=False)
            fo_l = left.assign(sc, est, left.plan(text)[0] if match_on else [], matched, allow_photo=False)
            if fo_r:
                sc["footage"] = fo_r
            if fo_l:
                sc["footage_b"] = fo_l
            if fo_r or fo_l:
                n_split += 1
                time_footage += est
                for g in {right.gid, left.gid}:
                    per_game[g] = per_game.get(g, 0) + 1
            continue
        fo = x.assign(sc, est, beats_plan, matched)
        if fo is None:
            continue
        sc["footage"] = fo
        per_game[x.gid] = per_game.get(x.gid, 0) + 1
        if fo["kind"] == "photo":
            n_photo += 1
        else:
            n_video += 1
        time_footage += est
    share = round(time_footage / time_total, 3)
    summary = {
        "enabled": True,
        "library": {k: v for k, v in lib.items() if k != "skipped"},
        "ratio_target": ratio,
        "ratio_min": lo,
        "ratio_max": hi,
        "share": share,
        "video_scenes": n_video,
        "photo_scenes": n_photo,
        "graphics_only_scenes": len(scenes) - n_video - n_photo - n_split,
        "matched": matched,
        "missing": missing,
        **({"fixed_scenes": len(fixed), "mode": "details"} if fixed else {}),
        **({"mode": "topic", "split_scenes": n_split, "games": {g: {"name": libs[g].name, "scenes": per_game.get(g, 0), "library": {k: v for k, v in libs[g].summary.items() if k != "skipped"}} for g in libs}} if topic else {}),
    }
    others = ", ".join(f"{libs[g].name} on {per_game.get(g, 0)}" for g in libs if g != "gta6")
    log(f"footage: {share:.0%} of the narration on real footage ({n_video} trailer scene(s), {n_photo} screenshot scene(s){f', {n_split} split screen(s)' if n_split else ''}, {summary['graphics_only_scenes']} graphics-only){f'; {others} scene(s)' if others else ''}, {len(matched)} spoken mention(s) shown")
    if missing:
        names = sorted({m["said"] for m in missing})
        log(f"footage: said but not in the library yet: {', '.join(names[:12])} (add footage with `python -m gta_factory footage add ... --tags ...`)")
    return {"ledger": list(ledger.values()), "summary": summary}


# ----------------------------------------------------------------------------- the edit of one scene

def _fit(p: dict[str, Any], d: float) -> float:
    """Source in-point for ``d`` seconds of a shot piece: the chosen excerpt, else the whole shot around it."""
    if d <= p["end"] - p["start"] + 1e-6:
        return p["start"] + (p["end"] - p["start"] - d) / 2
    return max(p["s"], min(p["start"] - (d - (p["end"] - p["start"])) / 2, p["e"] - d))


def _room(p: dict[str, Any]) -> float:
    return float("inf") if p["type"] == "photo" else p["e"] - p["s"]


def _seg(p: dict[str, Any], t: float, d: float, beat: dict[str, Any] | None = None) -> dict[str, Any]:
    seg = {"t": round(t, 3), "d": round(d, 3), "type": p["type"], "file": p["file"]}
    if p["type"] == "shot":
        seg["start"] = round(_fit(p, d), 3)
    if beat is not None:
        seg["said"] = beat.get("said", "")
    return seg


class _Fill:
    """Hands out filler pieces in order (looping), cut to fill a time slot without leaving tiny leftovers."""

    def __init__(self, pieces: list[dict[str, Any]], photo_hold: float, min_piece: float):
        self.pieces = pieces
        self.k = 0
        self.photo_hold = photo_hold
        self.min_piece = min_piece

    def _len(self, p: dict[str, Any]) -> float:
        return self.photo_hold if p["type"] == "photo" else p["end"] - p["start"]

    def take(self, a: float, b: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        t = a
        guard = 0
        while b - t > 1e-3 and self.pieces and guard < 500:
            guard += 1
            p = self.pieces[self.k % len(self.pieces)]
            self.k += 1
            rem = b - t
            avail = self._len(p)
            if avail >= rem - 1e-6 or (p["type"] == "shot" and _room(p) >= rem) or (p["type"] == "photo" and rem <= avail + self.min_piece):
                use = rem
            elif rem - avail >= self.min_piece:
                use = avail
            elif rem - self.min_piece >= self.min_piece:
                use = rem - self.min_piece  # leave exactly one short-but-readable piece for the end
            else:  # find a piece long enough to close the slot in one go
                fit = next((q for q in self.pieces if _room(q) >= rem), None)
                p, use = (fit, rem) if fit else (p, avail)
            out.append({**_seg(p, t, use), "_p": p})
            t += use
        return out


def build_segments(fo: dict[str, Any], words: list[dict[str, Any]], duration: float, *, lead: float = 0.25, min_piece: float = 1.2, max_hold: float = 6.0, photo_hold: float = 3.5) -> list[dict[str, Any]]:
    """The edit of one scene: each beat starts ``lead`` seconds before its word and holds while it is talked about.

    ``words`` are the scene-local word timings ([{"w", "s", "e"}], one per whitespace word of the narration).
    Returns contiguous segments [{"t", "d", "type": "shot"|"photo", "file", "start"?, "said"?}] covering ``duration``.
    """
    marks = sorted(((max(0.0, words[b["word"]]["s"] - lead), b) for b in fo.get("beats") or [] if 0 <= b["word"] < len(words)), key=lambda x: x[0])
    fill = _Fill(list(fo.get("fill") or []) or [b["piece"] for _, b in marks], photo_hold, min_piece)
    segs: list[dict[str, Any]] = []
    cursor = 0.0
    for i, (t, b) in enumerate(marks):
        if duration - cursor < min_piece:
            break
        t = max(t, cursor)  # the previous beat is still on screen: this one comes right after it
        if t - cursor < min_piece:
            t = cursor  # too little room for a filler cut: start the beat a bit early
        else:
            segs += fill.take(cursor, t)
        p = b["piece"]
        nxt = marks[i + 1][0] if i + 1 < len(marks) else duration
        hold = min(_room(p), max_hold if p["type"] == "shot" else photo_hold)
        end = min(duration, t + max(hold, min_piece))
        if nxt >= t + min_piece:
            end = min(end, nxt)
        if 0 < duration - end < min_piece:  # never leave a sliver at the end of the scene
            if _room(p) >= duration - t:
                end = duration
            elif duration - min_piece - t >= min_piece:
                end = duration - min_piece
        segs.append({**_seg(p, t, end - t, b), "_p": p})
        cursor = end
    left = duration - cursor
    if left > 1e-3:
        last = segs[-1] if segs else None
        if last is not None and left < min_piece and _room(last["_p"]) >= last["d"] + left:
            segs[-1] = {**_seg(last["_p"], last["t"], last["d"] + left), **({"said": last["said"]} if "said" in last else {}), "_p": last["_p"]}
        else:
            segs += fill.take(cursor, duration)
    return [{k: v for k, v in s.items() if k != "_p"} for s in segs]


def _cover_filter(w: int, h: int, fps: int) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},format=yuv420p"


def _pan_filter(w: int, h: int, fps: int, frames: int, k: int) -> str:
    """A screenshot inside a clip: cover-cropped 10 % larger, then a slow horizontal pan (direction alternates)."""
    W, H = (int(w * 1.1) // 2) * 2, (int(h * 1.1) // 2) * 2
    dur = max(1, frames) / fps
    x = f"({W}-{w})*min(1,t/{dur:.3f})" if k % 2 == 0 else f"({W}-{w})*(1-min(1,t/{dur:.3f}))"
    return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},crop={w}:{h}:x='{x}':y='({H}-{h})/2',setsar=1,fps={fps},format=yuv420p"


def cut_scene_clips(
    settings: Any,
    scenes: list[dict[str, Any]],
    durations: dict[int, float],
    *,
    out_dir: Path,
    width: int,
    height: int,
    fps: int,
    words: dict[int, list[dict[str, Any]]] | None = None,
    log: Callable[[str], None] = lambda m: None,
) -> int:
    """Render one silent clip per video-footage scene (cached by content). Sets ``footage["clip"]``. Returns clips cut."""
    cfg = settings.get("footage", {}) or {}
    lead = float(cfg.get("match_lead_seconds", 0.25))
    min_piece = float(cfg.get("min_piece_seconds", 1.2))
    max_hold = float(cfg.get("max_clip_seconds") or settings.get("rights_review.max_seconds_per_official_clip", 6))
    photo_hold = float(cfg.get("photo_hold_seconds", 3.5))
    words = words or {}
    cut = 0
    cache_dir = settings.jobs_dir / ".cache" / "footage"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for sc in scenes:
        fo = sc.get("footage")
        if fo and fo.get("kind") == "detail":  # "details you missed": run-in, then the frame freezes for the zoom
            from ..details.produce import detail_plan, render_detail_clip

            plan = detail_plan(sc, durations.get(sc["scene_number"], 0.0), width=width, height=height, fps=fps)
            cached = cache_dir / f"detail_{stable_hash({**plan, 'v': 1})}.mp4"
            if not (cached.exists() and cached.stat().st_size > 0):
                render_detail_clip(plan, cached)
                cut += 1
            dest = out_dir / f"footage_scene_{sc['scene_number']:03d}.mp4"
            out_dir.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or dest.stat().st_size != cached.stat().st_size:
                dest.write_bytes(cached.read_bytes())
            fo["clip"] = str(dest)
            continue
        need = durations.get(sc["scene_number"], 0.0) + 0.2
        panels = (sc.get("data") or {}).get("panels") if sc.get("template") == "compare" else None
        # a split screen has one clip per panel (left = footage_b, the other game; right = footage, GTA 6)
        jobs = [("footage", "", width, height)] if not panels else [("footage", "", panels["right"][2], panels["right"][3]), ("footage_b", "_b", panels["left"][2], panels["left"][3])]
        for key, suffix, w, h in jobs:
            fo = sc.get(key)
            if not fo or fo.get("kind") != "video":
                continue
            if _cut_clip(sc, fo, need, words.get(sc["scene_number"]) or [], out_dir=out_dir, suffix=suffix, width=w, height=h, fps=fps, cache_dir=cache_dir, lead=lead, min_piece=min_piece, max_hold=max_hold, photo_hold=photo_hold):
                cut += 1
    if cut:
        log(f"footage: {cut} scene clip(s) cut from the trailers and screenshots")
    return cut


def _cut_clip(sc: dict[str, Any], fo: dict[str, Any], need: float, words: list[dict[str, Any]], *, out_dir: Path, suffix: str, width: int, height: int, fps: int, cache_dir: Path, lead: float, min_piece: float, max_hold: float, photo_hold: float) -> bool:
    """Cut one scene clip (cached by content) and set ``fo["clip"]``. Returns True when ffmpeg had to run."""
    cut = False
    segs = build_segments(fo, words, need, lead=lead, min_piece=min_piece, max_hold=max_hold, photo_hold=photo_hold)
    if not segs:
        return False
    # whole frames: each cut lands on a frame boundary and the clip has exactly the frames it needs
    bounds = [round(s["t"] * fps) for s in segs] + [round(need * fps)]
    frames = [max(1, b - a) for a, b in zip(bounds, bounds[1:])]
    fo["segments"] = segs
    key = stable_hash({"s": segs, "f": frames, "w": width, "h": height, "fps": fps, "v": 2})
    cached = cache_dir / f"clip_{key}.mp4"
    if not (cached.exists() and cached.stat().st_size > 0):
        cmd = ["ffmpeg", "-y", "-v", "error"]
        chains = []
        for i, (s, nf) in enumerate(zip(segs, frames)):
            secs = nf / fps + 0.25  # a little extra input; trim cuts at the exact frame
            if s["type"] == "photo":
                cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{secs:.3f}", "-i", s["file"]]
                vf = _pan_filter(width, height, fps, nf, i + sc["scene_number"])
            else:
                cmd += ["-ss", f"{s['start']:.3f}", "-t", f"{secs:.3f}", "-i", s["file"]]
                vf = _cover_filter(width, height, fps)
            chains.append(f"[{i}:v]{vf},trim=end_frame={nf},setpts=PTS-STARTPTS[v{i}];")
        graph = "".join(chains) + "".join(f"[v{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[out]"
        tmp = cached.with_suffix(".part.mp4")
        cmd += ["-filter_complex", graph, "-map", "[out]", "-frames:v", str(sum(frames)), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-g", str(fps), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not tmp.exists():
            raise RenderError(f"Could not cut the footage for scene {sc['scene_number']}: {proc.stderr.strip()[-500:]}", hint="Check the files of the footage library (`python -m gta_factory footage list`, or `--game ...`).")
        tmp.rename(cached)
        cut = True
    dest = out_dir / f"footage_scene_{sc['scene_number']:03d}{suffix}.mp4"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.stat().st_size != cached.stat().st_size:
        dest.write_bytes(cached.read_bytes())
    fo["clip"] = str(dest)
    return cut


# ----------------------------------------------------------------------------- thumbnails and previews

def thumbnail_photos(settings: Any, text: str, count: int, *, out_dir: Path) -> list[Path]:
    """Real GTA 6 pictures for the thumbnails: what the title names first, else sharp stills from the trailers."""
    cfg = settings.get("footage", {}) or {}
    if not cfg.get("enabled", True) or not cfg.get("thumbnails", True) or count <= 0:
        return []
    items = load_items(settings)
    vocab = items["vocab"]
    want: dict[str, float] = {}
    for m in vocab.mentions(text):
        for c, f in _wanted(m["concepts"], vocab).items():
            want[c] = max(want.get(c, 0.0), f)
    seed = _h(text)
    photos = sorted(items["photos"], key=lambda p: (-_score(p, want), _h(seed, p["file"])))
    out = [Path(p["path"]) for p in photos[:count]]
    if len(out) < count:
        shots = [s for s in items["shots"] if s["e"] - s["s"] >= 1.5]
        shots.sort(key=lambda s: (-_score(s, want), -s["luma"], _h(seed, s["key"])))
        out_dir.mkdir(parents=True, exist_ok=True)
        for sh in shots[: (count - len(out)) * 3 : 3] or shots[: count - len(out)]:
            t = round((sh["s"] + sh["e"]) / 2, 2)
            dest = out_dir / f"thumb_still_{len(out) + 1}.jpg"
            proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", sh["path"], "-frames:v", "1", "-q:v", "2", str(dest)], capture_output=True, text=True)
            if proc.returncode == 0 and dest.exists():
                out.append(dest)
            if len(out) >= count:
                break
    return out


def preview(settings: Any, text: str, top: int = 3) -> list[dict[str, Any]]:
    """What a sentence would show: each mention and the best matching files (``footage find``)."""
    items = load_items(settings)
    items["known"] = set(load_vocabulary(settings).concepts)
    beats, missing = plan_beats(text, items)
    pool = items["shots"] + items["photos"]
    out = []
    for b in beats:
        want = _wanted(b["concepts"], items["vocab"])
        ranked = sorted(_candidates(pool, want), key=lambda x: (-x[0], x[1]["key"]))
        seen: list[dict[str, Any]] = []
        for s, it in ranked:
            label = it["file"] + (f" @ {it['s']:.1f}-{it['e']:.1f}s" if it["kind"] == "shot" else "")
            if len(seen) < top:
                seen.append({"file": label, "score": round(s, 2), "tags": sorted(it["tags"])})
        out.append({"said": b["said"], "concepts": b["concepts"], "matches": sum(1 for _ in ranked), "best": seen})
    out += [{"said": m["said"], "concepts": m["concepts"], "matches": 0, "best": []} for m in missing]
    return out
