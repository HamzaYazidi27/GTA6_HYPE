"""Voiceover engine: per-scene synthesis with caching, clean-up, loudness and a single master track.

* One WAV per scene (``scene_001.wav`` ...) plus ``final_voiceover.wav``.
* Scenes are cached by a hash of (provider signature, spoken text), so changing
  one scene re-synthesises only that scene.
* Local voices (Kokoro) synthesise each sentence on its own: this gives exact
  sentence boundaries that the subtitle aligner uses as anchors. Premium voices
  (ElevenLabs, OpenAI) read the whole scene in one go, so the intonation flows
  from one sentence to the next like a human narrator (``audio.synthesis``).
* Missing audio is always an error. Nothing is ever replaced by silence.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np
import yaml

from ...app.core.errors import ProviderError
from ...app.core.utils import read_json, stable_hash, write_json
from ...providers.tts import TTSProvider
from ..script_generation.writer import split_sentences

WORK_SR = 48000


def load_lexicon(settings: Any) -> list[tuple[re.Pattern[str], str]]:
    rules: list[Any] = []
    base = Path(__file__).with_name("pronunciation.yaml")
    for path in (base, settings.root / "pronunciation.yaml"):
        if path.exists():
            rules += (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("rules") or []
    return [(re.compile(pat), rep) for pat, rep in rules]


def spoken_text(text: str, lexicon: list[tuple[re.Pattern[str], str]]) -> str:
    """Apply the pronunciation lexicon. Only the voice engine sees this version."""
    out = text
    for rx, rep in lexicon:
        out = rx.sub(rep, out)
    return re.sub(r"\s+", " ", out).strip()


def resample(x: np.ndarray, sr: int, target: int = WORK_SR) -> np.ndarray:
    if sr == target:
        return x.astype(np.float32)
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(sr, target)
    return resample_poly(x, target // g, sr // g).astype(np.float32)


def trim_silence(x: np.ndarray, sr: int, *, threshold_db: float = -42.0, pad: float = 0.045) -> np.ndarray:
    """Remove leading/trailing silence, keeping a small natural pad."""
    if len(x) == 0:
        return x
    win = max(1, int(sr * 0.01))
    n = len(x) // win
    if n < 3:
        return x
    rms = np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1) + 1e-12)
    loud = np.where(20 * np.log10(rms) > threshold_db)[0]
    if len(loud) == 0:
        return x
    start = max(0, loud[0] * win - int(pad * sr))
    end = min(len(x), (loud[-1] + 1) * win + int(pad * sr))
    return x[start:end]


def fade(x: np.ndarray, sr: int, ms: float = 6.0) -> np.ndarray:
    n = min(len(x) // 2, int(sr * ms / 1000))
    if n <= 0:
        return x
    y = x.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    y[:n] *= ramp
    y[-n:] *= ramp[::-1]
    return y


def rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x)) + 1e-12)))


def match_level(x: np.ndarray, target_rms_db: float = -20.0, peak_db: float = -1.5) -> np.ndarray:
    """Bring a scene to a consistent speech level without clipping."""
    active = x[np.abs(x) > 10 ** (-45 / 20)]
    if len(active) < 100:
        return x
    gain = 10 ** ((target_rms_db - rms_db(active)) / 20)
    y = x * gain
    peak = float(np.max(np.abs(y)) + 1e-9)
    limit = 10 ** (peak_db / 20)
    if peak > limit:
        y = y * (limit / peak)
    return y.astype(np.float32)


def write_wav(path: Path, x: np.ndarray, sr: int = WORK_SR) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(x, -1.0, 1.0), sr, subtype="PCM_16")


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data, int(sr)


def measure_loudness(path: Path) -> dict[str, float]:
    """Integrated loudness (LUFS) and true peak via ffmpeg's EBU R128 meter."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"], capture_output=True, text=True
    )
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not m:
        raise ProviderError("ffmpeg could not measure loudness: " + proc.stderr[-300:])
    data = json.loads(m.group(0))
    return {"lufs": float(data["input_i"]), "true_peak_db": float(data["input_tp"]), "lra": float(data["input_lra"])}


def _clean(res: Any, text: str) -> tuple[np.ndarray, float]:
    """Trimmed, faded 48 kHz samples of one synthesis, and the leading silence removed (for provider timings)."""
    raw = res.samples
    lead = 0.0
    if len(raw):
        win = max(1, int(res.sample_rate * 0.01))
        n = len(raw) // win
        if n >= 3:
            r = np.sqrt(np.mean(raw[: n * win].reshape(n, win) ** 2, axis=1) + 1e-12)
            loud = np.where(20 * np.log10(r) > -42.0)[0]
            if len(loud):
                lead = max(0.0, loud[0] * win / res.sample_rate - 0.045)
    x = fade(resample(trim_silence(raw, res.sample_rate), res.sample_rate), WORK_SR)
    if len(x) < WORK_SR * 0.12:
        raise ProviderError(f"The voice provider returned almost no audio for: {text[:80]!r}")
    return x, lead


def whole_scene(settings: Any, tts: TTSProvider) -> bool:
    """``audio.synthesis``: auto (premium voices read the whole scene, local ones sentence by sentence), scene or sentence."""
    mode = str(settings.get("audio.synthesis", "auto")).lower()
    return mode == "scene" or (mode == "auto" and tts.name in ("elevenlabs", "openai"))


def synthesize_scene(tts: TTSProvider, narration: str, lexicon: list[tuple[re.Pattern[str], str]], *, sentence_pause: float = 0.24, whole: bool = False) -> dict[str, Any]:
    """Synthesise one scene (sentence by sentence, or in one go with ``whole``). Returns samples (48 kHz) and sentence spans."""
    sentences = split_sentences(narration) or [narration]
    if whole and len(sentences) > 1:  # one reading: the voice keeps its melody across sentences
        res = tts.synthesize(spoken_text(narration, lexicon))
        x, lead = _clean(res, narration)
        dur = len(x) / WORK_SR
        weights = [max(1, len(s)) for s in sentences]
        spans, at = [], 0.0
        for s, w in zip(sentences, weights):  # sentence spans in proportion to their length (the aligner refines them)
            d = dur * w / sum(weights)
            spans.append({"text": s, "start": round(at, 3), "end": round(at + d, 3)})
            at += d
        words = [{"w": w["w"], "s": round(max(0.0, w["s"] - lead), 3), "e": round(max(0.0, w["e"] - lead), 3)} for w in res.words]
        audio = match_level(x)
        return {"samples": audio, "duration": len(audio) / WORK_SR, "sentences": spans, "provider_words": words}
    pieces: list[np.ndarray] = []
    spans = []
    provider_words: list[dict[str, Any]] = []
    cursor = 0.0
    for i, sent in enumerate(sentences):
        res = tts.synthesize(spoken_text(sent, lexicon))
        x, lead = _clean(res, sent)  # offset removed by trimming, so provider timings stay correct
        dur = len(x) / WORK_SR
        spans.append({"text": sent, "start": round(cursor, 3), "end": round(cursor + dur, 3)})
        for w in res.words:
            provider_words.append({"w": w["w"], "s": round(max(0.0, w["s"] - lead) + cursor, 3), "e": round(max(0.0, w["e"] - lead) + cursor, 3)})
        pieces.append(x)
        cursor += dur
        if i < len(sentences) - 1:
            # a question or an exclamation takes a slightly longer breath than a full stop
            pause = sentence_pause * (1.25 if sent.rstrip("\"'”’ ").endswith(("?", "!")) else 1.0)
            pieces.append(np.zeros(int(pause * WORK_SR), dtype=np.float32))
            cursor += pause
    audio = match_level(np.concatenate(pieces))
    return {"samples": audio, "duration": len(audio) / WORK_SR, "sentences": spans, "provider_words": provider_words}


def generate_voiceover(
    tts: TTSProvider,
    settings: Any,
    scenes: list[dict[str, Any]],
    *,
    cache_dir: Path,
    audio_dir: Path,
    only_scenes: set[int] | None = None,
    force_scenes: set[int] | None = None,
    progress: Callable[[int, int, str], None] = lambda done, total, msg: None,
    vertical: bool = False,
) -> dict[str, Any]:
    """Create scene WAVs and the manifest. ``force_scenes`` bypasses the cache for those scene numbers."""
    tts.check()
    lexicon = load_lexicon(settings)
    whole = whole_scene(settings, tts)
    pause = settings.rhythm(vertical=vertical)["sentence_pause_seconds"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    reused = 0
    for i, sc in enumerate(scenes, 1):
        n = sc["scene_number"]
        key = stable_hash({"sig": tts.signature(), "text": sc["narration"], "lex": [r.pattern for r, _ in lexicon], "read": "scene" if whole else f"sentence/{pause:g}"})
        wav_cache, meta_cache = cache_dir / f"{key}.wav", cache_dir / f"{key}.json"
        use_cache = wav_cache.exists() and meta_cache.exists() and not (force_scenes and n in force_scenes)
        if only_scenes and n not in only_scenes and not use_cache:
            use_cache = False
        if use_cache:
            meta = read_json(meta_cache)
            reused += 1
        else:
            result = synthesize_scene(tts, sc["narration"], lexicon, sentence_pause=pause, whole=whole)
            write_wav(wav_cache, result["samples"])
            meta = {"duration": round(result["duration"], 3), "sentences": result["sentences"], "provider_words": result["provider_words"]}
            write_json(meta_cache, meta)
        out = audio_dir / f"scene_{n:03d}.wav"
        out.write_bytes(wav_cache.read_bytes())
        manifest.append({"scene_number": n, "file": out.name, "cache_key": key, "duration": meta["duration"], "sentences": meta["sentences"], "provider_words": meta.get("provider_words") or [], "cached": use_cache})
        progress(i, len(scenes), f"scene {n} ({meta['duration']:.1f}s){' [cache]' if use_cache else ''}")
    for stale in audio_dir.glob("scene_*.wav"):
        if stale.name not in {m["file"] for m in manifest}:
            stale.unlink()
    return {"provider": tts.name, "voice": tts.voice_id(), "signature": tts.signature(), "scenes": manifest, "reused_from_cache": reused}


MIN_SCENE_SECONDS = {"title": 3.2, "chapter": 2.2, "kinetic": 1.8, "outro": 5.5, "countdown": 3.5, "stat": 3.2, "detail": 3.6}


def build_timeline(settings: Any, scenes: list[dict[str, Any]], voice: dict[str, Any], audio_dir: Path, fps: int, *, vertical: bool = False) -> dict[str, Any]:
    """Place every scene on the master timeline and write ``final_voiceover.wav``.

    Scene durations are rounded to whole frames so picture and sound cannot drift.
    Breaths between scenes, the silence after the last word and the end card follow the
    video's rhythm profile (``rhythm.short`` for 9:16, ``rhythm.long`` for 16:9).
    """
    rh = settings.rhythm(vertical=vertical)
    gap, tail = rh["scene_gap_seconds"], rh["tail_seconds"]
    lead_in = 0.25 if vertical else 0.35
    min_seconds = {**MIN_SCENE_SECONDS, "outro": rh["outro_seconds"]}
    if vertical:  # a Short never holds a card after the voice stops (only the end card and the detail zoom need their time)
        min_seconds = {k: v for k, v in min_seconds.items() if k in ("outro", "detail", "countdown")}
    by_n = {m["scene_number"]: m for m in voice["scenes"]}
    total_samples = 0
    placed = []
    frame_cursor = 0
    for idx, sc in enumerate(scenes):
        m = by_n.get(sc["scene_number"])
        if m is None:
            raise ProviderError(f"No narration audio for scene {sc['scene_number']}.", hint="Re-run the voice step.")
        offset = lead_in if idx == 0 else min(0.1, gap / 2)
        natural = offset + m["duration"] + (tail if idx == len(scenes) - 1 else gap)
        seconds = max(natural, min_seconds.get(sc["template"], 0.0))
        frames = max(1, int(round(seconds * fps)))
        start = frame_cursor / fps
        placed.append({"scene_number": sc["scene_number"], "start_frame": frame_cursor, "duration_frames": frames, "start_time": round(start, 3), "duration": round(frames / fps, 3), "voice_offset": round(offset, 3), "voice_duration": m["duration"]})
        frame_cursor += frames
    total_seconds = frame_cursor / fps
    total_samples = int(round(total_seconds * WORK_SR))
    master = np.zeros(total_samples, dtype=np.float32)
    for p in placed:
        x, sr = read_wav(audio_dir / by_n[p["scene_number"]]["file"])
        x = resample(x, sr)
        at = int(round((p["start_time"] + p["voice_offset"]) * WORK_SR))
        end = min(total_samples, at + len(x))
        master[at:end] += x[: end - at]
    # loudness: linear gain to the voice target, then a safety ceiling
    tmp = audio_dir / "_voice_raw.wav"
    write_wav(tmp, master)
    loud = measure_loudness(tmp)
    target = float(settings.get("audio.voice_lufs", -16.0))
    gain_db = target - loud["lufs"]
    ceiling = float(settings.get("audio.true_peak_db", -1.0))
    gain_db = min(gain_db, ceiling - loud["true_peak_db"] + 3.0)  # allow up to 3 dB of limiting
    master = master * (10 ** (gain_db / 20))
    limit = 10 ** (ceiling / 20)
    over = np.abs(master) > limit
    if over.any():  # soft-knee limiter for the few peaks above the ceiling
        master = np.where(over, np.sign(master) * (limit + (1 - limit) * np.tanh((np.abs(master) - limit) / (1 - limit + 1e-9)) * 0.35), master)
        master = np.clip(master, -limit * 1.02, limit * 1.02)
    final = audio_dir / "final_voiceover.wav"
    write_wav(final, master.astype(np.float32))
    tmp.unlink(missing_ok=True)
    measured = measure_loudness(final)
    return {"fps": fps, "total_frames": frame_cursor, "total_seconds": round(total_seconds, 3), "scenes": placed, "voiceover": {"file": str(final), "lufs": measured["lufs"], "true_peak_db": measured["true_peak_db"], "applied_gain_db": round(gain_db, 2)}}
