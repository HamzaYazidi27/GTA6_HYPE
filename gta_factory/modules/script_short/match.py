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
   the beginning, middle and end of the cut ("verified_clip").

A specific beat whose footage cannot be found or verified in the library is searched on the web
(``websource.py``: YouTube videos, then pictures) and matched again with the same checks.
Unresolved beats receive an explicitly unverified illustration from the same game's library.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .plan import GENERIC_DISTRACTORS
from .vocabulary import NON_GAMEPLAY, SCENE_LABELS, content, synonyms

STEP = 0.25  # window positions inside a shot, seconds
WEAK_MIN = 0.15
MAX_SEGMENTS = 4  # windows of different shots one beat may chain to fill its length
VOCAB_TOP = 5  # a candidate frame must rank the beat's concept among the 5 labels that fit it best (open vocabulary)...
CLIP_MIN_MARGIN = 0.012  # near ties between visual subjects need a separate vision check
CLIP_ALONE_TOP = 1  # ... and first of all when no vision model looks at it
MAX_VISION_CANDIDATES = 6  # windows the vision model looks at, per beat
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
    """official | own | licensed | web_found | third_party_allowed | unverified"""
    from ..footage.library import _official, third_party

    if side.get("rights") in ("official", "own", "licensed"):
        return side["rights"]
    if side.get("own_capture"):
        return "own"
    if side.get("license") or side.get("license_file"):
        return "licensed"
    if side.get("web_found"):  # keep provenance on subsequent uses of an automatic download
        return "web_found"
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
        from ..footage.library import build_index, load_items, still_shot_key

        self.gid = gid
        self.settings = game_settings(settings, gid) if gid != "gta6" else settings
        self.items = load_items(self.settings, build_index(self.settings), job=job, shared_web=True)
        self.by_key = {it["key"]: it for it in self.items["shots"] + self.items["photos"]}
        self.origin = {key: still_shot_key(self.items["index"], it) or it["autokey"] for key, it in self.by_key.items()}
        self.rows, self.vecs = index_frames(self.settings, self.items, log=log, model=clip) if clip is not None else ([], np.zeros((0, 512), np.float32))
        self.rows_by_key: dict[str, list[int]] = {}
        for i, r in enumerate(self.rows):
            if r["key"] in self.by_key:
                self.rows_by_key.setdefault(r["key"], []).append(i)

    @property
    def empty(self) -> bool:
        return not self.items["shots"]


def _used_origins(lib: Library, used: dict[str, list[tuple[float, float]]]) -> set[str]:
    origins = set()
    for key, it in lib.by_key.items():
        spans = used.get(it["file"]) or used.get(it["path"]) or []
        if spans and (it["kind"] == "photo" or any(a < it["e"] and z > it["s"] for a, z in spans)):
            origins.add(lib.origin[key])
    return origins


def _shown_frames(lib: Library, used: dict[str, list[tuple[float, float]]]) -> np.ndarray:
    """Reference frames for detecting the same picture in stills and other uploads."""
    indices = []
    for key, rows in lib.rows_by_key.items():
        it = lib.by_key[key]
        spans = used.get(it["file"]) or used.get(it["path"]) or []
        for a, z in spans:
            if it["kind"] == "photo":
                indices.append(rows[0])
            elif it["s"] < z and it["e"] > a:
                inside = [i for i in rows if a <= lib.rows[i]["t"] < z]
                indices.extend(inside or [min(rows, key=lambda i: abs(lib.rows[i]["t"] - (a + z) / 2))])
    return lib.vecs[indices]


def _recent_cuts(settings: Any, job: str | None) -> dict[str, list[tuple[float, float]]]:
    """Use recent exported Shorts as a soft tie-breaker. A unique good match stays eligible."""
    manifests = sorted(settings.outputs_dir.glob("*/edit_decision_list.json"), reverse=True)
    history: dict[str, list[tuple[float, float]]] = {}
    for path in [p for p in manifests if p.parent.name != job][:3]:
        try:
            events = json.loads(path.read_text()).get("events", [])
            for ev in events:
                if ev.get("type") in ("shot", "photo") and ev.get("file"):
                    start = float(ev.get("source_in") or 0)
                    end = start + float(ev["record_out"]) - float(ev["record_in"])
                    history.setdefault(ev["file"], []).append((start, end))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return history


def zero_shot(sims_pos: np.ndarray, sims_neg: np.ndarray, temp: float = 100.0) -> np.ndarray:
    """P(concept) per frame in a softmax vote against the distractors (CLIP's zero-shot classification)."""
    logits = np.concatenate([sims_pos[:, None], sims_neg], axis=1) * temp
    logits -= logits.max(axis=1, keepdims=True)
    e = np.exp(logits)
    return e[:, 0] / e.sum(axis=1)


def best_window(times: list[float], probs: list[float], s: float, e: float, need: float, *,
                used: list[tuple[float, float]] = (), max_overlap: float = 0.0) -> tuple[float, float, float, float] | None:
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
            if overlap <= max_overlap * length and (best is None or score > best[0]):
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
        prompt = (f"Check only visible evidence. Reject live-action photos, talking heads, renders with unrelated text, and shots where only the setting fits. Is this an in-game or trailer image clearly showing {concept}?\n"
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
    used: dict[str, list[tuple[float, float]]] | None = None,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """One match per beat: {"status", "confidence", "segments": [...], "considered", "seen", ...}.

    ``needs``: seconds of footage each beat must fill (its scene's length). Statuses:
    verified_vision | verified_clip | general (a hook / teaser / CTA: any strong gameplay) | weak | missing.
    ``used``: {file: [(start, end)]} moments already on screen (an earlier pass): never shown twice.
    """
    libs: dict[str, Library] = {}

    def lib(gid: str) -> Library:
        if gid not in libs:
            libs[gid] = Library(settings, gid, job=job, clip=clip, log=log)
        return libs[gid]

    used = {k: list(v) for k, v in (used or {}).items()}
    recent = _recent_cuts(settings, job)
    out = []
    generic_neg = clip.embed_text(templated(GENERIC_DISTRACTORS)) if clip is not None else None
    vocab = clip.embed_text(templated(SCENE_LABELS)) if clip is not None else None
    for i, (b, need) in enumerate(zip(beats, needs)):
        gid = b.get("game") or "gta6"
        L = lib(gid)
        res: dict[str, Any] = {"beat": i, "text": b["text"], "concept": b["concept"], "game": gid, "specific": bool(b.get("specific")), "need": round(need, 3), "segments": [], "status": "missing", "confidence": 0.0, "considered": 0, "seen": ""}
        if L.empty and not (allow_photos and L.items["photos"]):
            res["reason"] = f"the {gid} footage library has no usable footage for this pass"
            out.append(res)
            continue
        if clip is None or not L.rows:
            res["reason"] = "no CLIP frame index (install the [clip] extra and run `footage index`)"
            out.append(res)
            continue
        prompts = b.get("prompts") or [b["concept"]]
        specific = bool(b.get("specific"))
        visual_text = b.get("visual_text") or b["text"]
        prompts = list(dict.fromkeys([visual_text, *prompts]))
        if b.get("visual_text"):
            res["visual_text"] = visual_text
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
        def priority(key):
            times, probs = per_key[key]
            it = L.by_key[key]
            spans = recent.get(it["file"], ())
            # Prefer a fresh angle when its score is close to a recently exported one.
            return max(p - (0.04 if any(a <= t < z for a, z in spans) else 0.0) for t, p in zip(times, probs))

        occupied = _used_origins(L, used)
        ranked = sorted((key for key in per_key if L.origin[key] not in occupied), key=priority, reverse=True)
        comp = vocab[~np.array(synonyms(prompts + [b["concept"]]))] if specific else None
        comp_labels = [lab for lab, same in zip(SCENE_LABELS, synonyms(prompts + [b["concept"]])) if not same] if specific else []

        concept_words = synonyms(prompts + [b["concept"]])
        junk = [SCENE_LABELS.index(lab) for lab in NON_GAMEPLAY if not concept_words[SCENE_LABELS.index(lab)]]

        def frame_vec(key: str, t: float) -> np.ndarray | None:
            idx = L.rows_by_key.get(key) or []
            return L.vecs[min(idx, key=lambda j: abs((L.rows[j]["t"] or 0.0) - t))] if idx else None

        def not_gameplay(key: str, t: float) -> str:
            """The label of a logo / title card / black frame / menu, when that is what the frame is."""
            v = frame_vec(key, t)
            if v is None:
                return ""
            top = int(np.argmax(vocab @ v))
            return SCENE_LABELS[top] if top in junk else ""

        def gate(key: str, t: float) -> dict[str, Any]:
            """Where the concept ranks among everything this frame could be (CLIP, open vocabulary)."""
            v = frame_vec(key, t)
            if v is None:
                return {"ok": False, "strict": False, "rank": None, "looks_like": ""}
            sc = float((pos @ v).max())
            others = comp @ v
            rank = 1 + int((others > sc).sum())
            beats_explicit = bool((explicit @ v < sc).all())
            margin = sc - max(float(others.max()), float((explicit @ v).max()))
            return {"ok": rank <= VOCAB_TOP and beats_explicit,
                    "strict": rank <= CLIP_ALONE_TOP and margin >= CLIP_MIN_MARGIN,
                    "rank": rank, "looks_like": comp_labels[int(np.argmax(others))]}

        segments: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        weak: list[dict[str, Any]] = []
        seen: list[str] = []
        vision_calls = 0
        remaining = need
        starts: list[float] = []
        means: list[float] = []
        shown = _shown_frames(L, used)
        for key in ranked:
            if remaining <= 0.05 or len(segments) >= MAX_SEGMENTS:
                break
            it = L.by_key[key]
            tt, pp = per_key[key]
            if L.origin[key] in occupied or (not specific and max(pp) < WEAK_MIN):
                continue
            if it["kind"] == "photo":
                if used.get(it["file"]):
                    continue
                win = (0.0, remaining, pp[0], pp[0])
            else:
                win = best_window(tt, pp, it["s"], it["e"], remaining, used=used.get(it["file"], []))
            # a specific beat is not pre-filtered by the vote: the gate and the vision model below decide
            if win is None or (not specific and win[3] < WEAK_MIN):
                continue
            a, z, mean, first = win
            edge = min(0.12, (z - a) / 4)
            times = [0.0] if it["kind"] == "photo" else [a + edge, (a + z) / 2, z - edge]
            samples = np.stack([frame_vec(key, t) for t in times])
            if len(shown) and (samples @ shown.T).max() >= 0.985:
                continue  # another file can be a still or a re-encode of the exact same moment
            junk_label = next((lab for lab in (not_gameplay(key, t) for t in times) if lab), "")
            if junk_label:  # a logo, a title card, a menu: never footage, not even for a hook
                rejected.append({"file": it["file"], "at": round(a, 2), "why": f"not gameplay (looks like {junk_label!r})"})
                continue
            if specific:  # verify the action through the cut
                gates = [gate(key, t) for t in times]
                why = ""
                if not all(g["ok"] for g in gates):
                    why = f"looks more like {next(g['looks_like'] for g in gates if not g['ok'])!r} (CLIP rank {max(g['rank'] or 99 for g in gates)})"
                elif verify is not None:
                    if vision_calls >= MAX_VISION_CANDIDATES:
                        why = "not looked at (vision check limit for this beat)"
                    else:
                        vision_calls += 1
                        target = f'the visible subject and action in the narration "{visual_text}" (visual context: {b["concept"]})'
                        checks = [verify(Path(it["path"]), None if it["kind"] == "photo" else t, target) for t in times]
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
            shown = np.concatenate([shown, samples])
            occupied.add(L.origin[key])
            starts.append(first)
            means.append(mean)
            remaining -= z - a
        res["rejected"] = rejected[:8]
        if not segments:
            res["rejected_moments"] = [[r["file"], r["at"]] for r in rejected if not r["why"].startswith("not looked at")]
        res["seen"] = "; ".join(dict.fromkeys(seen))[:300]
        if not segments:
            res["reason"] = ("no frame of the library shows it" + (": " + "; ".join(f"{r['file']} @ {r['at']}s {r['why']}" for r in rejected[:3]) if rejected else " clearly (look-alikes rejected)"))[:400]
            res["best_candidates"] = [{"file": L.by_key[k]["file"], "prob": round(max(per_key[k][1]), 3)} for k in ranked[:3]]
            if weak:
                res["weak_segments"] = weak
            out.append(res)
            continue
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


def long_focus_still(m: dict[str, Any]) -> bool:
    return (m.get("status") == "illustration" and m["need"] >= 4.5 and len(m["segments"]) == 1
            and m["segments"][0]["kind"] == "photo" and not m.get("focus_still_checked") and not m.get("callback_beat"))


def _narration_cut(timing: dict[str, Any]) -> tuple[float, str] | None:
    """Find a phrase or word onset near the middle, leaving a readable shot on both sides."""
    words = timing.get("words") or []
    duration = timing["need"]
    candidates = []
    for i in range(1, len(words)):
        cut = round(words[i]["s"] - timing["start"], 3)
        if min(cut, duration - cut) < max(1.35, duration * 0.28):
            continue
        prev = words[i - 1]
        boundary = prev["w"].rstrip().endswith((",", ".", "?", "!", ":", ";")) or words[i]["s"] - prev["e"] >= 0.18
        candidates.append((boundary, -abs(cut - duration / 2), cut, i))
    if not candidates:
        return None
    _, _, cut, i = max(candidates)
    return cut, " ".join(w["w"] for w in words[i:])


def _illustration_windows(lib: Library, labels: np.ndarray, scores: np.ndarray, need: float,
                          used: dict[str, list[tuple[float, float]]], allow_photos: bool, *, cutaway: bool = False
                          ) -> Iterator[tuple[tuple[bool, bool, float], dict[str, Any], float, float, float]]:
    occupied = _used_origins(lib, used)
    shown = _shown_frames(lib, used)
    similarities = (lib.vecs @ shown.T).max(axis=1) if len(shown) else None
    for key, indices in lib.rows_by_key.items():
        it = lib.by_key[key]
        photo = it["kind"] == "photo"
        if (photo and not allow_photos) or any(SCENE_LABELS[labels[i]] in NON_GAMEPLAY for i in indices):
            continue
        if photo:
            a, z, score = 0.0, need, float(scores[indices[0]])
        else:
            times = [lib.rows[i]["t"] for i in indices]
            win = best_window(times, scores[indices].tolist(), it["s"], it["e"], need,
                              used=used.get(it["path"], []), max_overlap=0.0)
            if win is None and not cutaway:
                win = best_window(times, scores[indices].tolist(), it["s"], it["e"], need)
            if win is None:
                continue
            a, z, score, _ = win
        repeated = lib.origin[key] in occupied or any(min(z, end) > max(a, start) for start, end in used.get(it["path"], []))
        if not repeated and similarities is not None:
            middle = min(indices, key=lambda i: abs((lib.rows[i]["t"] or 0.0) - (a + z) / 2))
            repeated = similarities[middle] >= 0.985
        complete = z - a >= need - (0.001 if cutaway else 0.05)
        if cutaway and (repeated or not complete):
            continue
        # When scores are close, prefer an animated still for the main illustration and motion for its cutaway.
        preferred_kind = "shot" if cutaway else "photo"
        bonus = 0.015 if it["kind"] == preferred_kind else 0.0
        yield (not repeated, complete, score + bonus), it, a, z, score


def _cut_focus_still(m: dict[str, Any], timing: dict[str, Any], settings: Any, lib: Library, labels: np.ndarray,
                     scores: np.ndarray, clip: Any, used: dict[str, list[tuple[float, float]]], allow_photos: bool) -> None:
    cut = _narration_cut(timing)
    first = m["segments"][0]
    indices = lib.rows_by_key.get(first["file"])
    m["focus_still_checked"] = True
    if not cut or not indices:
        return
    at, tail = cut
    remaining = m["need"] - at
    # Keep the overall concept, but let words actually spoken after the cut guide the alternate.
    scores = (scores + lib.vecs @ clip.embed_text(templated([tail]))[0]) / 2
    minimum = max(0.15, float(scores[indices[0]]) - 0.035)
    windows = _illustration_windows(lib, labels, scores, remaining, used, allow_photos, cutaway=True)
    for _, it, a, z, score in sorted(windows, key=lambda w: w[0], reverse=True):
        if score < minimum or it["path"] == first["path"]:
            continue
        rows = lib.rows_by_key[it["key"]]
        representative = min(rows, key=lambda i: abs((lib.rows[i]["t"] or 0.0) - a))
        if lib.vecs[representative] @ lib.vecs[indices[0]] >= 0.97:
            continue  # another encode of the same frame makes a distracting jump
        sg = _segment(settings, lib, it, a, z, 0.0, 0.0)
        if sg["rights"] not in ("official", "own", "licensed", "third_party_allowed"):
            continue
        first["end"] = round(first["start"] + at, 3)
        m["segments"].append(sg)
        m["web"] = m.get("web", False) or sg["rights"] == "web_found" or "/web/" in sg["file"]
        used.setdefault(it["path"], []).append((a, z))
        break


def fill_illustrations(settings: Any, matches: list[dict[str, Any]], *, job: str, clip: Any,
                       allow_photos: bool, log: Callable[[str], None], focus_beats: list[dict[str, Any]] | None = None) -> None:
    """Fill unresolved beats, and cut long Focus illustrations at an aligned narration boundary."""
    from ...app.core.errors import FactoryError

    timings = {t["beat"]: t for t in focus_beats or []}
    targets = [m for m in matches if not m["segments"] or (m["beat"] in timings and long_focus_still(m))]
    if not targets:
        return
    if clip is None:
        if all(m["segments"] for m in targets):
            return  # an older edit can still render its existing illustration without the model
        raise FactoryError("Cannot select illustrations without the CLIP index.", hint="Restore the CLIP model and resume from footage.")
    vocabulary = clip.embed_text(templated(SCENE_LABELS))
    used: dict[str, list[tuple[float, float]]] = {}
    for m in matches:
        for sg in m["segments"]:
            used.setdefault(sg["path"], []).append((sg["start"], sg["end"]))
    for gid in dict.fromkeys(m["game"] for m in targets):
        lib = Library(settings, gid, job=job, clip=clip, log=log)
        labels = np.argmax(lib.vecs @ vocabulary.T, axis=1)
        long_stills = []
        for m in (m for m in targets if m["game"] == gid):
            descriptions = [m.get("visual_text") or m["text"], m["concept"]]
            vocab = lib.items["vocab"]
            for mention in vocab.mentions(" ".join(descriptions)):
                descriptions.extend(vocab.look(tag) for tag in mention["concepts"] if vocab.look(tag))
            prompts = clip.embed_text(templated(list(dict.fromkeys(descriptions))))
            scores = (lib.vecs @ prompts.T).mean(axis=1)
            if not m["segments"]:
                windows = list(_illustration_windows(lib, labels, scores, m["need"], used, allow_photos))
                if not windows:
                    raise FactoryError(f"No usable {gid} visual for beat {m['beat'] + 1}: {m['text']}",
                                       hint=f"Add gameplay or an eligible screenshot to the {gid} footage library, then resume from match.")
                # Vary the picture among plausible choices, never spend relevance just to make a cut.
                floor = max(w[4] for w in windows) - 0.045
                windows = [w for w in windows if w[4] >= floor]
                rejected = m.get("rejected_moments") or [(r["file"], r["at"]) for r in m.get("rejected", [])]
                # Do not immediately recycle a picture that failed this beat's visual check.
                alternatives = [w for w in windows if not any(
                    w[1]["file"] == file and (w[1]["kind"] == "photo" or w[2] - 0.02 <= at < w[3])
                    for file, at in rejected)]
                _, it, a, z, score = max(alternatives or windows, key=lambda w: w[0])
                sg = _segment(settings, lib, it, a, z, 0.0, 0.0)
                used.setdefault(it["path"], []).append((a, z))
                m.update(segments=[sg], status="illustration", confidence=0.0, seen="",
                         illustration_similarity=round(score, 3), web=sg["rights"] == "web_found" or "/web/" in sg["file"])
                log(f"match: beat {m['beat'] + 1} illustration ({sg['kind']}): {sg['file']}")
            if m["beat"] in timings and long_focus_still(m):
                long_stills.append((m, scores))
        # Reserve the main image for every beat before a supplemental shot can claim it.
        for m, scores in long_stills:
            _cut_focus_still(m, timings[m["beat"]], settings, lib, labels, scores, clip, used, allow_photos)
            if len(m["segments"]) > 1:
                shot = m["segments"][1]
                log(f"match: beat {m['beat'] + 1} illustration cuts at +{m['segments'][0]['end']:.2f}s to {shot['file']}")


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
        for axis in ("focus_x", "focus_y"):
            if axis in sg:
                seg[axis] = sg[axis]
        if sg.get("crop"):
            seg["crop"] = sg["crop"]
        if sg["kind"] == "shot":
            avail = min(sg["end"], float(sg.get("shot_end", sg["end"]))) - sg["start"]
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
