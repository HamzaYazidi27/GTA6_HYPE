"""Semantic, frame-verified footage for each beat of a script.

For every beat the matcher looks for the moment of real gameplay that shows what the narration
says, during exactly as long as the beat lasts:

1. **Search** (CLIP): every indexed frame of the beat's game library (GTA 6, or GTA 5 for a
   comparison) is scored against the beat's picture descriptions.
2. **Look-alikes are rejected**: the frame must beat the beat's distractors ("a motorboat" for a jet
   ski, "a sea turtle" for dolphins) and generic scenes in a zero-shot vote, not only score high.
3. **Window**: inside a shot, the window of the beat's length whose frames show it best, with the
   action visible from the first frame of the window (no reveal several seconds late).
4. **Verification**: with a local vision model (Ollama) running, frames of the chosen window are
   looked at and must show the concept ("verified_vision"); without it, CLIP's vote must be clear on
   the first and the middle frame ("verified_clip").

A specific beat whose footage cannot be found or verified is **missing**: nothing unrelated is put in
its place, the scene shows the words on the channel background, and QC refuses "fully verified".
Screenshots never stand in for gameplay (``allow_photos`` is for tests and manual overrides only).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .plan import GENERIC_DISTRACTORS
from .vocabulary import SCENE_LABELS, content, synonyms

STEP = 0.25  # window positions inside a shot, seconds
START_MIN = 0.2  # a candidate window: the concept is a serious option on its first frame (the gate below decides)
WEAK_MIN = 0.15
MAX_SEGMENTS = 4  # windows of different shots one beat may chain to fill its length
VOCAB_TOP = 5  # a candidate frame must rank the beat's concept among the 5 labels that fit it best (open vocabulary)...
CLIP_ALONE_TOP = 1  # ... and first of all when no vision model looks at it
MAX_VISION_CANDIDATES = 6  # windows the vision model looks at, per beat
GENERAL_PROMPTS = ["intense action", "a car chase in a sunny city", "a character driving fast at night"]
# every text is read in the same frame, so the vote compares WHAT is shown, not how the sentence is phrased
TEMPLATE = "a screenshot from a video game showing {}"
NULL = "a screenshot from a video game"  # the null hypothesis: the concept's words must add something to it
_PREFIX = re.compile(r"^(?:a\s+)?(?:video game\s+)?(?:screenshot|picture|image|frame|shot|scene|still)(?:\s+from\s+(?:a\s+)?(?:video game|gta\s*(?:vi|6)))?(?:\s+of|\s+showing|:)\s*|^gta\s*(?:vi|6)\s+gameplay:?\s*", re.I)


def phrase(text: str) -> str:
    """A picture description without its framing ("a video game screenshot: race jet skis" -> "race jet skis")."""
    return _PREFIX.sub("", str(text).strip()).strip(" .") or str(text).strip()


def templated(texts: list[str]) -> list[str]:
    return [TEMPLATE.format(phrase(t)) for t in texts]


def _synonym_flags(labels: list[str], texts: list[str]) -> list[bool]:
    words = set().union(*(content(t) for t in texts)) if texts else set()
    return [bool(content(lab)) and bool(content(lab) & words) for lab in labels]

_fps_cache: dict[str, float] = {}


def source_fps(path: str) -> float:
    if path not in _fps_cache:
        proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", path], capture_output=True, text=True)
        try:
            num, den = (proc.stdout.strip() or "0/1").split("/")
            _fps_cache[path] = round(float(num) / float(den or 1), 3)
        except (ValueError, ZeroDivisionError):
            _fps_cache[path] = 0.0
    return _fps_cache[path]


def rights_of(settings: Any, side: dict[str, Any]) -> str:
    """official | own | licensed | third_party_allowed | unverified"""
    from ..footage.library import _official, third_party

    if side.get("rights") in ("official", "own", "licensed"):
        return side["rights"]
    if side.get("own_capture"):
        return "own"
    if side.get("license") or side.get("license_file"):
        return "licensed"
    if _official(settings, side) or (side.get("source_url") and third_party(settings, side) is None):
        return "official"
    if side.get("allow_third_party"):
        return "third_party_allowed"
    return "unverified"


class Library:
    """One game's footage, its frame index and frame vectors."""

    def __init__(self, settings: Any, gid: str, *, job: str | None, clip: Any, log: Callable[[str], None]):
        from ..footage.clip import index_frames
        from ..footage.games import game_settings
        from ..footage.library import build_index, load_items

        self.gid = gid
        self.settings = game_settings(settings, gid) if gid != "gta6" else settings
        self.items = load_items(self.settings, build_index(self.settings), job=job)
        self.by_key = {it["key"]: it for it in self.items["shots"] + self.items["photos"]}
        self.rows, self.vecs = index_frames(self.settings, self.items, log=log, model=clip) if clip is not None else ([], np.zeros((0, 512), np.float32))
        self.rows_by_key: dict[str, list[int]] = {}
        for i, r in enumerate(self.rows):
            if r["key"] in self.by_key:
                self.rows_by_key.setdefault(r["key"], []).append(i)

    @property
    def empty(self) -> bool:
        return not self.items["shots"]


def zero_shot(sims_pos: np.ndarray, sims_neg: np.ndarray, temp: float = 100.0) -> np.ndarray:
    """P(concept) per frame in a softmax vote against the distractors (CLIP's zero-shot classification)."""
    logits = np.concatenate([sims_pos[:, None], sims_neg], axis=1) * temp
    logits -= logits.max(axis=1, keepdims=True)
    e = np.exp(logits)
    return e[:, 0] / e.sum(axis=1)


def best_window(times: list[float], probs: list[float], s: float, e: float, need: float, *, used: list[tuple[float, float]] = ()) -> tuple[float, float, float, float] | None:
    """(start, end, mean prob, prob at start) of the best ``need``-second window of a shot [s, e]."""
    if not times:
        return None
    t = np.array(times)
    p = np.array(probs)
    length = min(need, e - s)
    if length <= 0.2:
        return None
    best = None
    w0 = s
    while w0 + length <= e + 1e-6:
        inside = (t >= w0 - 0.26) & (t <= w0 + length + 0.26)
        if inside.any():
            first = p[np.argmin(np.abs(t - (w0 + 0.12)))]
            mean = float(p[inside].mean())
            overlap = sum(max(0.0, min(w0 + length, b) - max(w0, a)) for a, b in used)
            score = mean + 0.15 * first
            if overlap <= 0.25 * length and (best is None or score > best[0]):  # never the same moment twice
                best = (score, w0, w0 + length, mean, float(first))
        w0 += STEP
    return best[1:] if best else None


def vision_verifier(settings: Any, log: Callable[[str], None]) -> Callable[[Path, float | None, str], dict[str, Any]] | None:
    """yes / no from the local vision model: does this frame show the concept?"""
    from ..footage.autotag import frame_jpeg, ollama_tagger, vision_status

    try:
        if not vision_status(settings)[0]:
            return None
        call = ollama_tagger(settings, max_tokens=120)
    except Exception:
        return None

    def verify(path: Path, t: float | None, concept: str) -> dict[str, Any]:
        prompt = (f"Look only at what is visible in this video game picture. Does it clearly show: {concept}?\n"
                  'Answer with JSON only: {"shows": true or false, "seen": "what the picture shows, in at most 10 words"}')
        try:
            ans = call(frame_jpeg(path, t), prompt)
        except Exception as exc:
            return {"shows": None, "seen": f"vision check failed: {type(exc).__name__}"}
        shows = ans.get("shows")
        return {"shows": shows is True or str(shows).strip().lower() == "true", "seen": str(ans.get("seen") or "")[:120]}

    return verify


def match_beats(
    settings: Any,
    beats: list[dict[str, Any]],
    needs: list[float],
    *,
    job: str | None = None,
    clip: Any = None,
    verify: Callable[[Path, float | None, str], dict[str, Any]] | None = None,
    allow_photos: bool = False,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """One match per beat: {"status", "confidence", "segments": [...], "considered", "seen", ...}.

    ``needs``: seconds of footage each beat must fill (its scene's length). Statuses:
    verified_vision | verified_clip | general (a hook / teaser / CTA: any strong gameplay) | weak | missing.
    """
    libs: dict[str, Library] = {}

    def lib(gid: str) -> Library:
        if gid not in libs:
            libs[gid] = Library(settings, gid, job=job, clip=clip, log=log)
        return libs[gid]

    used: dict[str, list[tuple[float, float]]] = {}
    used_keys: set[str] = set()
    out = []
    generic_neg = clip.embed_text(templated(GENERIC_DISTRACTORS)) if clip is not None else None
    vocab = clip.embed_text(templated(SCENE_LABELS)) if clip is not None else None
    for i, (b, need) in enumerate(zip(beats, needs)):
        gid = b.get("game") or "gta6"
        L = lib(gid)
        res: dict[str, Any] = {"beat": i, "text": b["text"], "concept": b["concept"], "game": gid, "specific": bool(b.get("specific")), "need": round(need, 3), "segments": [], "status": "missing", "confidence": 0.0, "considered": 0, "seen": ""}
        if L.empty:
            res["reason"] = f"the {gid} footage library has no usable video"
            out.append(res)
            continue
        if clip is None or not L.rows:
            res["reason"] = "no CLIP frame index (install the [clip] extra and run `footage index`)"
            out.append(res)
            continue
        prompts = b.get("prompts") or [b["concept"]]
        specific = bool(b.get("specific"))
        if not specific:
            prompts = prompts + GENERAL_PROMPTS
        pos = clip.embed_text(templated(prompts))
        own = [d for d in b.get("distractors") or [] if phrase(d).lower() not in " ".join(prompts).lower()]
        generic = [k for k, same in enumerate(_synonym_flags(GENERIC_DISTRACTORS, prompts + [b["concept"]])) if not same]
        explicit = clip.embed_text(templated(own) + [NULL])
        neg = np.concatenate([explicit, generic_neg[generic]])
        rows = [k for k in range(len(L.rows)) if L.rows[k]["key"] in L.by_key and (L.rows[k]["kind"] == "shot" or allow_photos)]
        if not rows:
            out.append({**res, "reason": "no indexed gameplay frames"})
            continue
        V = L.vecs[rows]
        sp = (V @ pos.T).max(axis=1)
        if specific:  # it must be THIS, not a look-alike: a vote against the distractors and the null description
            prob = zero_shot(sp, V @ neg.T)
        else:  # a hook, teaser or call to action: the most striking gameplay, ranked against the library itself
            lo = float(np.percentile(sp, 5))
            prob = np.clip((sp - lo) / max(1e-6, float(sp.max()) - lo), 0.0, 1.0)
        res["considered"] = len(rows)
        per_key: dict[str, tuple[list[float], list[float]]] = {}
        for r, pr in zip(rows, prob):
            row = L.rows[r]
            tt, pp = per_key.setdefault(row["key"], ([], []))
            tt.append(row["t"] if row["t"] is not None else 0.0)
            pp.append(float(pr))
        # the best match first; for a hook / teaser / call to action, shots not shown yet first
        ranked = sorted(per_key, key=lambda k: (not specific and k in used_keys, -max(per_key[k][1])))[:40]
        comp = vocab[~np.array(synonyms(prompts + [b["concept"]]))] if specific else None
        comp_labels = [lab for lab, same in zip(SCENE_LABELS, synonyms(prompts + [b["concept"]])) if not same] if specific else []

        def gate(key: str, t: float) -> dict[str, Any]:
            """Where the concept ranks among everything this frame could be (CLIP, open vocabulary)."""
            idx = L.rows_by_key.get(key) or []
            if not idx:
                return {"ok": False, "strict": False, "rank": None, "looks_like": ""}
            k = min(idx, key=lambda j: abs((L.rows[j]["t"] or 0.0) - t))
            v = L.vecs[k]
            sc = float((pos @ v).max())
            others = comp @ v
            rank = 1 + int((others > sc).sum())
            beats_explicit = bool((explicit @ v < sc).all())
            return {"ok": rank <= VOCAB_TOP and beats_explicit, "strict": rank <= CLIP_ALONE_TOP and beats_explicit, "rank": rank, "looks_like": comp_labels[int(np.argmax(others))]}

        segments: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        weak: list[dict[str, Any]] = []
        seen: list[str] = []
        vision_calls = 0
        remaining = need
        starts: list[float] = []
        means: list[float] = []
        for key in ranked:
            if remaining <= 0.05 or len(segments) >= MAX_SEGMENTS:
                break
            it = L.by_key[key]
            tt, pp = per_key[key]
            if max(pp) < WEAK_MIN:
                break
            if it["kind"] == "photo":
                win = (0.0, remaining, pp[0], pp[0])
            else:
                win = best_window(tt, pp, it["s"], it["e"], remaining, used=used.get(it["file"], []))
            if win is None or win[3] < (START_MIN if specific else WEAK_MIN):
                continue
            a, z, mean, first = win
            if specific:  # frame-level verification of the first and the middle frame of the window
                times = [a + min(0.12, (z - a) / 4), (a + z) / 2]
                gates = [gate(key, t) for t in times]
                why = ""
                if not all(g["ok"] for g in gates):
                    why = f"looks more like {gates[0]['looks_like'] if not gates[0]['ok'] else gates[1]['looks_like']!r} (CLIP rank {max(g['rank'] or 99 for g in gates)})"
                elif verify is not None:
                    if vision_calls >= MAX_VISION_CANDIDATES:
                        why = "not looked at (vision check limit for this beat)"
                    else:
                        vision_calls += 1
                        checks = [verify(Path(it["path"]), None if it["kind"] == "photo" else t, b["concept"]) for t in times]
                        seen += [c["seen"] for c in checks if c.get("seen")]
                        if not all(c["shows"] for c in checks):
                            why = "the vision model saw: " + "; ".join(c["seen"] for c in checks if c.get("seen"))[:160]
                elif not all(g["strict"] for g in gates):
                    why = f"not certain without a vision model (CLIP rank {max(g['rank'] for g in gates)}, could be {gates[0]['looks_like']!r})"
                if why:
                    rejected.append({"file": it["file"], "at": round(a, 2), "why": why})
                    if not weak:
                        weak.append(_segment(settings, L, it, a, z, mean, first))
                    continue
            segments.append(_segment(settings, L, it, a, z, mean, first))
            used.setdefault(it["file"], []).append((a, z))
            used_keys.add(key)
            starts.append(first)
            means.append(mean)
            remaining -= z - a
        res["rejected"] = rejected[:8]
        res["seen"] = "; ".join(dict.fromkeys(seen))[:300]
        if not segments:
            res["reason"] = ("no frame of the library shows it" + (": " + "; ".join(f"{r['file']} @ {r['at']}s {r['why']}" for r in rejected[:3]) if rejected else " clearly (look-alikes rejected)"))[:400]
            res["best_candidates"] = [{"file": L.by_key[k]["file"], "prob": round(max(per_key[k][1]), 3)} for k in ranked[:3]]
            if weak:
                res["weak_segments"] = weak
            out.append(res)
            continue
        if remaining > 0.05:  # the last window is stretched to the end of its shot when it can be
            last = segments[-1]
            room = last["shot_end"] - last["end"]
            grow = min(room, remaining)
            last["end"] = round(last["end"] + grow, 3)
            remaining -= grow
        conf = float(np.mean(means))
        res.update(segments=segments, confidence=round(conf, 3), first=round(starts[0], 3), short_by=round(max(0.0, remaining), 3))
        res["status"] = "general" if not specific else "verified_vision" if verify is not None else "verified_clip"
        out.append(res)
        log(f"match: beat {i + 1} “{b['text'][:48]}” -> {res['status']} ({conf:.2f}) {segments[0]['file']} @ {segments[0]['start']:.1f}s")
    return out


def _segment(settings: Any, L: Library, it: dict[str, Any], a: float, z: float, mean: float, first: float) -> dict[str, Any]:
    side = (it.get("video") or {}).get("sidecar") or {} if it["kind"] == "shot" else {}
    if it["kind"] == "photo":
        from ..footage.library import _sidecar

        side = _sidecar(Path(it.get("source_path") or it["path"]))
    path = it["path"]
    return {
        "kind": it["kind"],
        "file": it["file"],
        "path": path,
        "game": L.gid,
        "start": round(a, 3),
        "end": round(z, 3),
        "shot_start": it.get("s", 0.0),
        "shot_end": it.get("e", z),
        "crop": it.get("crop"),
        "fps": source_fps(path) if it["kind"] == "shot" else 0.0,
        "prob_mean": round(mean, 3),
        "prob_first": round(first, 3),
        "source_url": side.get("source_url") or (it.get("video") or {}).get("source_url") or "",
        "creator": side.get("uploader") or side.get("channel") or "",
        "credit": (it.get("video") or {}).get("credit") or it.get("credit") or "Footage: Rockstar Games",
        "rights": rights_of(L.settings, side),
    }


def sequence_for(match: dict[str, Any], need: float, *, offset: float = 0.0) -> list[dict[str, Any]]:
    """The picture of one beat: its matched windows back to back, filling ``need`` seconds exactly.

    The last window never reads past the end of its shot: when the library had nothing more to
    show, the missing time is a ``hold`` on the shot's last frame (reported by QC as frozen time).
    """
    segs = []
    t = 0.0
    n = len(match["segments"])
    for k, sg in enumerate(match["segments"]):
        d = sg["end"] - sg["start"] if k < n - 1 else max(0.05, need - t)
        seg = {"t": round(offset + t, 3), "d": round(d, 3), "type": "photo" if sg["kind"] == "photo" else "shot", "file": sg["path"], "start": sg["start"], "beat": match["beat"]}
        if sg.get("crop"):
            seg["crop"] = sg["crop"]
        if sg["kind"] == "shot":
            avail = float(sg.get("shot_end", sg["end"])) - sg["start"]
            if d > avail + 0.02:
                seg["hold"] = round(d - avail, 3)
        segs.append(seg)
        t += d
    return segs


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
