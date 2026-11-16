"""Word-level alignment of the narration and subtitle export (SRT / VTT).

The script is the ground truth for *spelling*; audio analysis only supplies
*timing*. Three timing sources are used, best first:

1. timestamps returned by the voice provider (ElevenLabs),
2. forced alignment with faster-whisper (local, optional dependency),
3. syllable-weighted distribution inside each sentence span (sentence spans
   are exact because every sentence is synthesised separately).
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

import numpy as np

_WORD = re.compile(r"\S+")
_whisper_model = None


def _norm(tok: str) -> str:
    return re.sub(r"[^a-z0-9]", "", tok.lower().replace("’", "'"))


def syllables(word: str) -> int:
    w = _norm(word)
    if not w:
        return 1
    if w.isdigit():
        return max(1, len(w) * 2)
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and n > 1 and not w.endswith(("le", "ee")):
        n -= 1
    return max(1, n)


def heuristic_words(sentences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Distribute each sentence's words across its exact span, weighted by syllables."""
    out = []
    for s in sentences:
        toks = _WORD.findall(s["text"])
        if not toks:
            continue
        weights = np.array([syllables(t) + (0.9 if re.search(r"[,;:]$", t) else 0.0) + 0.35 for t in toks], dtype=float)
        span = max(0.05, s["end"] - s["start"])
        edges = s["start"] + np.concatenate([[0.0], np.cumsum(weights)]) / weights.sum() * span
        for i, t in enumerate(toks):
            out.append({"w": t, "s": round(float(edges[i]), 3), "e": round(float(edges[i + 1]), 3)})
    return out


def whisper_words(wav_path: Path) -> list[dict[str, Any]] | None:
    """Recognised words with timings, or None when faster-whisper is not available."""
    global _whisper_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    import soundfile as sf
    from scipy.signal import resample_poly

    try:
        if _whisper_model is None:
            _whisper_model = WhisperModel("base.en", device="cpu", compute_type="int8")
        data, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != 16000:
            from math import gcd

            g = gcd(sr, 16000)
            data = resample_poly(data, 16000 // g, sr // g).astype(np.float32)
        segments, _ = _whisper_model.transcribe(data, language="en", word_timestamps=True, beam_size=1, condition_on_previous_text=False, vad_filter=False)
        return [{"w": w.word.strip(), "s": float(w.start), "e": float(w.end)} for seg in segments for w in (seg.words or []) if w.word.strip()]
    except Exception:
        return None


def map_timings(script_tokens: list[str], heard: list[dict[str, Any]], fallback: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    """Give every script token a timing by matching it to recognised words.

    Unmatched runs are interpolated between the surrounding matched anchors.
    Returns (words, share of tokens matched directly).
    """
    a = [_norm(t) for t in script_tokens]
    b = [_norm(h["w"]) for h in heard]
    times: list[tuple[float, float] | None] = [None] * len(a)
    matched = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (heard[j1 + k]["s"], heard[j1 + k]["e"])
                matched += 1
        elif tag == "replace" and (i2 - i1) > 0 and (j2 - j1) > 0:
            # same stretch of audio, different spelling ("6" vs "six"): spread the heard span over the script tokens
            s0, e0 = heard[j1]["s"], heard[j2 - 1]["e"]
            weights = np.array([syllables(t) for t in script_tokens[i1:i2]], dtype=float)
            edges = s0 + np.concatenate([[0.0], np.cumsum(weights)]) / weights.sum() * max(0.02, e0 - s0)
            for k in range(i2 - i1):
                times[i1 + k] = (float(edges[k]), float(edges[k + 1]))
    words = []
    for i, tok in enumerate(script_tokens):
        if times[i] is None:
            s, e = fallback[i]["s"], fallback[i]["e"]
            prev_e = next((times[j][1] for j in range(i - 1, -1, -1) if times[j]), None)  # type: ignore[index]
            next_s = next((times[j][0] for j in range(i + 1, len(a)) if times[j]), None)  # type: ignore[index]
            if prev_e is not None and next_s is not None and next_s > prev_e:
                gap_tokens = [j for j in range(i, len(a)) if times[j] is None and all(times[k] is None for k in range(i, j + 1))]
                n = len(gap_tokens)
                step = (next_s - prev_e) / max(1, n)
                s, e = prev_e, prev_e + step
            elif prev_e is not None:
                s = max(s, prev_e)
                e = max(e, s + 0.12)
            times[i] = (s, e)
        s, e = times[i]  # type: ignore[misc]
        words.append({"w": tok, "s": s, "e": e})
    # monotonic clean-up
    last = 0.0
    for w in words:
        w["s"] = max(w["s"], last)
        w["e"] = max(w["e"], w["s"] + 0.06)
        last = w["s"] + 0.01
    for w, nxt in zip(words, words[1:]):
        w["e"] = min(w["e"], max(nxt["s"], w["s"] + 0.06))
    for w in words:
        w["s"], w["e"] = round(w["s"], 3), round(w["e"], 3)
    return words, matched / max(1, len(a))


def align_scene(narration: str, sentences: list[dict[str, Any]], wav_path: Path, provider_words: list[dict[str, Any]], *, use_whisper: bool = True) -> dict[str, Any]:
    """Word timings (relative to the scene's audio start) for one scene."""
    tokens = _WORD.findall(narration)
    fallback = heuristic_words(sentences)
    if len(fallback) != len(tokens):  # sentence splitter and narration disagree: rebuild from one span
        end = sentences[-1]["end"] if sentences else 1.0
        fallback = heuristic_words([{"text": narration, "start": 0.0, "end": end}])
    if provider_words:
        words, share = map_timings(tokens, provider_words, fallback)
        if share >= 0.6:
            return {"words": words, "method": "provider_timestamps", "match": round(share, 3)}
    if use_whisper:
        heard = whisper_words(wav_path)
        if heard:
            words, share = map_timings(tokens, heard, fallback)
            if share >= 0.55:
                return {"words": words, "method": "forced_alignment", "match": round(share, 3)}
    return {"words": fallback, "method": "sentence_heuristic", "match": 0.0}


# --------------------------------------------------------------------------- captions
def build_captions(words: list[dict[str, Any]], *, max_chars: int = 42, max_lines: int = 2, max_gap: float = 0.55) -> list[dict[str, Any]]:
    """Group timed words into readable subtitle cues (for SRT/VTT)."""
    cues: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []

    def flush() -> None:
        if cur:
            cues.append({"start": cur[0]["s"], "end": cur[-1]["e"], "words": list(cur)})
            cur.clear()

    limit = max_chars * max_lines
    for i, w in enumerate(words):
        text_len = len(" ".join(x["w"] for x in cur + [w]))
        gap = w["s"] - cur[-1]["e"] if cur else 0.0
        if cur and (text_len > limit or gap > max_gap):
            flush()
        cur.append(w)
        ends_sentence = bool(re.search(r"[.!?][\"'”’]?$", w["w"]))
        ends_clause = bool(re.search(r"[,;:]$", w["w"]))
        length = len(" ".join(x["w"] for x in cur))
        # "November 19, 2026" and "1,200" read as one unit: never split before a number
        next_is_number = i + 1 < len(words) and bool(re.match(r"^\d", words[i + 1]["w"]))
        if ends_sentence or (ends_clause and length > max_chars * 0.75 and not next_is_number):
            flush()
    flush()
    # timing polish: minimum display time, never overlap the next cue
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else None
        c["end"] = max(c["end"], c["start"] + 0.9)
        if nxt is not None:
            c["end"] = min(c["end"] + 0.12, nxt - 0.02) if c["end"] + 0.12 < nxt else nxt - 0.02
        c["end"] = max(c["end"], c["start"] + 0.3)
        c["text"] = wrap_lines(" ".join(w["w"] for w in c["words"]), max_chars, max_lines)
        c["start"], c["end"] = round(c["start"], 3), round(c["end"], 3)
    return cues


def wrap_lines(text: str, max_chars: int, max_lines: int) -> str:
    """Balanced line breaking (two lines of similar length read better than one long + one short)."""
    if len(text) <= max_chars:
        return text
    words = text.split()
    best, best_cost = text, float("inf")
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > max_chars or len(b) > max_chars:
            continue
        cost = abs(len(a) - len(b)) + (4 if words[i - 1][-1:] not in ",;:.!?" else 0)
        if re.match(r"^\d", words[i]):  # keep "PlayStation 5", "December 4" together
            cost += 12
        if cost < best_cost:
            best, best_cost = a + "\n" + b, cost
    if best_cost == float("inf"):
        lines, cur = [], ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > max_chars:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        lines.append(cur)
        best = "\n".join(lines[: max_lines + 1])
    return best


def _ts(t: float, sep: str) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues: list[dict[str, Any]]) -> str:
    return "\n".join(f"{i}\n{_ts(c['start'], ',')} --> {_ts(c['end'], ',')}\n{c['text']}\n" for i, c in enumerate(cues, 1))


def to_vtt(cues: list[dict[str, Any]]) -> str:
    return "WEBVTT\n\n" + "\n".join(f"{i}\n{_ts(c['start'], '.')} --> {_ts(c['end'], '.')}\n{c['text']}\n" for i, c in enumerate(cues, 1))


def burn_in_phrases(words: list[dict[str, Any]], *, max_words: int = 5, max_chars: int = 30) -> list[dict[str, Any]]:
    """Short phrases for the on-video captions (the renderer highlights the active word)."""
    phrases: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []

    def flush() -> None:
        if cur:
            phrases.append({"s": cur[0]["s"], "e": cur[-1]["e"], "words": [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in cur]})
            cur.clear()

    for w in words:
        if cur and (len(cur) >= max_words or len(" ".join(x["w"] for x in cur + [w])) > max_chars or w["s"] - cur[-1]["e"] > 0.5):
            flush()
        cur.append(w)
        if re.search(r"[.!?,;:][\"'”’]?$", w["w"]) and len(cur) >= 2:
            flush()
    flush()
    for p, nxt in zip(phrases, phrases[1:]):
        p["e"] = round(min(max(p["e"] + 0.15, p["s"] + 0.5), nxt["s"]), 3)
    if phrases:
        phrases[-1]["e"] = round(phrases[-1]["e"] + 0.4, 3)
    return phrases
