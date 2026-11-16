"""Original music beds and sound effects, synthesised from scratch.

Nothing here samples, imitates or reuses third-party audio: every sound is
built from oscillators, filtered noise and envelopes with a fixed random seed,
so ``python -m gta_factory setup`` always regenerates the same files. That keeps
the audio free of Content ID matches and of licence questions, at zero cost.

Beds are seamless loops (a whole number of bars; the reverb tail of the last
bar is folded back onto the start). The mixer loops and ducks them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

SR = 48000
MOODS = ("pulse", "tension", "drive")
SFX_NAMES = ("whoosh", "impact", "riser", "tick", "pop", "glitch", "swell")

# A minor family keeps every bed in a key that sits under a male or female narrator.
_NOTE = {"C": -9, "C#": -8, "D": -7, "D#": -6, "E": -5, "F": -4, "F#": -3, "G": -2, "G#": -1, "A": 0, "A#": 1, "B": 2}


def hz(note: str, octave: int) -> float:
    return 440.0 * 2 ** ((_NOTE[note] + 12 * (octave - 4)) / 12)


# --------------------------------------------------------------------------- dsp helpers
def _t(n: int) -> np.ndarray:
    return np.arange(n, dtype=np.float64) / SR


def saw(freq: float, n: int, *, harmonics: int = 18, phase: float = 0.0) -> np.ndarray:
    """Band-limited sawtooth (additive)."""
    t = _t(n)
    out = np.zeros(n)
    for k in range(1, harmonics + 1):
        if freq * k > SR * 0.45:
            break
        out += np.sin(2 * np.pi * freq * k * t + phase * k) / k
    return out * (2 / np.pi)


def sine(freq: float | np.ndarray, n: int, phase: float = 0.0) -> np.ndarray:
    if np.isscalar(freq):
        return np.sin(2 * np.pi * float(freq) * _t(n) + phase)
    return np.sin(2 * np.pi * np.cumsum(freq) / SR + phase)


def adsr(n: int, a: float, d: float, s: float, r: float) -> np.ndarray:
    """Attack/decay/release in seconds, sustain level 0-1; the release ends at ``n``."""
    env = np.full(n, s)
    na, nd, nr = int(a * SR), int(d * SR), int(r * SR)
    na = min(na, n)
    env[:na] = np.linspace(0, 1, na, endpoint=False) if na else env[:na]
    nd = min(nd, max(0, n - na))
    if nd:
        env[na : na + nd] = np.linspace(1, s, nd, endpoint=False)
    nr = min(nr, n)
    if nr:
        env[n - nr :] *= np.linspace(1, 0, nr)
    return env


def exp_decay(n: int, seconds: float) -> np.ndarray:
    return np.exp(-_t(n) / max(1e-4, seconds))


def lowpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    from scipy.signal import butter, sosfilt

    sos = butter(order, min(cutoff, SR * 0.45), btype="low", fs=SR, output="sos")
    return sosfilt(sos, x, axis=0)


def highpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    from scipy.signal import butter, sosfilt

    sos = butter(order, cutoff, btype="high", fs=SR, output="sos")
    return sosfilt(sos, x, axis=0)


def bandpass(x: np.ndarray, lo: float, hi: float, order: int = 2) -> np.ndarray:
    from scipy.signal import butter, sosfilt

    sos = butter(order, [lo, min(hi, SR * 0.45)], btype="band", fs=SR, output="sos")
    return sosfilt(sos, x, axis=0)


def sweep_filter(x: np.ndarray, start_hz: float, end_hz: float, *, kind: str = "low", block: int = 1024) -> np.ndarray:
    """Time-varying filter (exponential cutoff sweep), processed in overlapping blocks."""
    from scipy.signal import butter, sosfilt

    n = len(x)
    out = np.zeros(n)
    hop = block // 2
    win = np.hanning(block)
    for i, start in enumerate(range(0, n, hop)):
        seg = x[start : start + block]
        if len(seg) < 8:
            break
        frac = start / max(1, n - 1)
        fc = start_hz * (end_hz / start_hz) ** frac
        sos = butter(2, min(max(fc, 20.0), SR * 0.45), btype=kind, fs=SR, output="sos")
        y = sosfilt(sos, np.concatenate([np.zeros(256), seg]))[256:]
        out[start : start + len(y)] += y * win[: len(y)]
    return out


def reverb(x: np.ndarray, rng: np.random.Generator, *, seconds: float = 1.6, wet: float = 0.25, stereo: bool = True) -> np.ndarray:
    """Convolution with decaying noise: a neutral, original room. Returns (n, 2) when stereo."""
    from scipy.signal import fftconvolve

    n_ir = int(seconds * SR)
    decay = np.exp(-_t(n_ir) * (6.9 / seconds))
    mono = x if x.ndim == 1 else x.mean(axis=1)
    chans = []
    for _ in range(2 if stereo else 1):
        ir = lowpass(rng.standard_normal(n_ir) * decay, 7000)
        ir /= np.sqrt(np.sum(ir**2)) + 1e-9
        chans.append(fftconvolve(mono, ir)[: len(mono) + n_ir])
    tail = np.stack(chans, axis=1) if stereo else chans[0]
    dry = np.zeros_like(tail)
    if stereo:
        src = x if x.ndim == 2 else np.stack([x, x], axis=1)
        dry[: len(src)] = src
    else:
        dry[: len(x)] = x
    return dry * (1 - wet) + tail * wet


def pan(x: np.ndarray, position: float) -> np.ndarray:
    """Constant-power pan, position -1 (left) .. 1 (right)."""
    a = (position + 1) * np.pi / 4
    return np.stack([x * np.cos(a), x * np.sin(a)], axis=1)


def normalize_rms(x: np.ndarray, target_db: float, peak_db: float = -1.0) -> np.ndarray:
    rms = np.sqrt(np.mean(np.square(x)) + 1e-12)
    y = x * (10 ** (target_db / 20) / rms)
    peak = np.max(np.abs(y)) + 1e-12
    limit = 10 ** (peak_db / 20)
    return y * (limit / peak) if peak > limit else y


def fold_tail(x: np.ndarray, loop_len: int) -> np.ndarray:
    """Make a seamless loop: everything after ``loop_len`` is added back onto the start."""
    out = x[:loop_len].copy()
    rest = x[loop_len:]
    while len(rest):
        k = min(len(rest), loop_len)
        out[:k] += rest[:k]
        rest = rest[k:]
    return out


def _place(buf: np.ndarray, sound: np.ndarray, at: int) -> None:
    end = min(len(buf), at + len(sound))
    if end > at:
        buf[at:end] += sound[: end - at]


# --------------------------------------------------------------------------- drum voices
def kick(rng: np.random.Generator, length: float = 0.42, punch: float = 1.0) -> np.ndarray:
    n = int(length * SR)
    t = _t(n)
    f = 46 + 95 * np.exp(-t / 0.035)
    body = sine(f, n) * exp_decay(n, 0.16)
    click = highpass(rng.standard_normal(n), 2500) * exp_decay(n, 0.004) * 0.25
    return np.tanh((body + click) * 1.6 * punch) * 0.9


def snare(rng: np.random.Generator, length: float = 0.3) -> np.ndarray:
    n = int(length * SR)
    tone = sine(185, n) * exp_decay(n, 0.05) * 0.5
    noise = bandpass(rng.standard_normal(n), 1200, 9000) * exp_decay(n, 0.09)
    return (tone + noise) * 0.55


def hat(rng: np.random.Generator, length: float = 0.06, open_: bool = False) -> np.ndarray:
    n = int((0.22 if open_ else length) * SR)
    return highpass(rng.standard_normal(n), 7500, order=4) * exp_decay(n, 0.07 if open_ else 0.018) * 0.32


def pluck(freq: float, length: float, rng: np.random.Generator) -> np.ndarray:
    n = int(length * SR)
    x = saw(freq, n, harmonics=10) * exp_decay(n, length * 0.35)
    return lowpass(x, 2600) * 0.5


def pad_chord(freqs: list[float], length: float, *, cutoff: float = 1500, detune: float = 0.006) -> np.ndarray:
    n = int(length * SR)
    x = np.zeros(n)
    for f in freqs:
        for d in (-detune, 0.0, detune):
            x += saw(f * (1 + d), n, harmonics=12, phase=hash((f, d)) % 7)
    x = lowpass(x / (len(freqs) * 3), cutoff, order=2)
    return x * adsr(n, 0.35, 0.4, 0.75, 0.45)


# --------------------------------------------------------------------------- beds
def _progression(names: list[tuple[str, str]], octave: int = 3) -> list[list[float]]:
    """('A','m') -> triad frequencies."""
    out = []
    for root, kind in names:
        r = hz(root, octave)
        third = r * 2 ** ((3 if kind == "m" else 4) / 12)
        out.append([r, third, r * 2 ** (7 / 12), r * 2])
    return out


def make_bed(mood: str, *, bars: int = 16, seed: int = 0) -> np.ndarray:
    """One seamless stereo loop for ``mood``."""
    rng = np.random.default_rng({"pulse": 101, "tension": 202, "drive": 303}[mood] + seed)
    if mood == "pulse":
        bpm, chords = 108, _progression([("A", "m"), ("F", ""), ("C", ""), ("G", "")])
    elif mood == "tension":
        bpm, chords = 88, _progression([("A", "m"), ("A", "m"), ("F", ""), ("E", "")])
    else:
        bpm, chords = 122, _progression([("D", "m"), ("A#", ""), ("F", ""), ("C", "")])
    beat = 60.0 / bpm
    bar = 4 * beat
    loop_len = int(round(bars * bar * SR))
    total = loop_len + int(2.0 * SR)
    drums = np.zeros(total)
    bass = np.zeros(total)
    keys = np.zeros(total)
    pads = np.zeros(total)

    for b in range(bars):
        chord = chords[b % len(chords)]
        t0 = b * bar
        at = lambda beats: int(round((t0 + beats * beat) * SR))  # noqa: E731
        # pads: one sustained chord per bar
        pad = pad_chord(chord, bar * 1.02, cutoff=900 if mood == "tension" else 1400)
        _place(pads, pad * (0.55 if mood == "tension" else 0.45), at(0))
        root = chord[0] / 2
        if mood == "pulse":
            for q in range(4):
                if q in (0, 2) or (q == 3 and b % 4 == 3):
                    _place(drums, kick(rng, punch=0.8), at(q))
                if q in (1, 3):
                    _place(drums, snare(rng) * 0.6, at(q))
            for e in range(8):
                _place(drums, hat(rng) * (1.0 if e % 2 else 0.6), at(e / 2))
            for e in range(8):
                n = int(beat / 2 * SR * 0.9)
                tone = saw(root, n, harmonics=6) * adsr(n, 0.005, 0.05, 0.6, 0.04)
                _place(bass, lowpass(tone, 420) * 0.6, at(e / 2))
            arp = [chord[0] * 2, chord[2] * 2, chord[3] * 2, chord[1] * 2]
            for s in range(16):
                if rng.random() < 0.8:
                    _place(keys, pluck(arp[s % 4], beat / 2, rng) * 0.32, at(s / 4))
        elif mood == "tension":
            # pulsing low strings on eighths, sparse ticks, a heartbeat kick every bar
            for e in range(8):
                n = int(beat / 2 * SR)
                tone = (saw(root, n, harmonics=8) + saw(root * 1.5, n, harmonics=8) * 0.4) * adsr(n, 0.01, 0.12, 0.35, 0.08)
                _place(bass, lowpass(tone, 300 + 40 * (b % 4)) * 0.55, at(e / 2))
            _place(drums, kick(rng, length=0.6, punch=0.7), at(0))
            _place(drums, kick(rng, length=0.5, punch=0.45), at(0.5))
            for s in range(16):
                if s % 4 == 2 or rng.random() < 0.15:
                    _place(drums, hat(rng) * 0.55, at(s / 4))
            if b % 2 == 1:
                _place(keys, pluck(chord[3] * 2, beat * 2, rng) * 0.25, at(2.5))
        else:  # drive
            for q in range(4):
                _place(drums, kick(rng, punch=1.0), at(q))
                if q in (1, 3):
                    _place(drums, snare(rng) * 0.75, at(q))
                _place(drums, hat(rng, open_=True) * 0.45, at(q + 0.5))
            for e in range(8):
                n = int(beat / 2 * SR * 0.85)
                f = root * (2 if e % 2 else 1)
                tone = saw(f, n, harmonics=10) * adsr(n, 0.004, 0.08, 0.5, 0.03)
                _place(bass, lowpass(tone, 650) * 0.5, at(e / 2))
            lead = [chord[3] * 2, chord[2] * 2, chord[1] * 2, chord[2] * 2]
            for s in range(0, 16, 2):
                _place(keys, pluck(lead[(s // 2) % 4], beat, rng) * 0.22, at(s / 4))

    pads_st = reverb(pads, rng, seconds=2.4, wet=0.45)
    keys_st = reverb(keys, rng, seconds=1.4, wet=0.35)
    drums_st = np.stack([drums, drums], axis=1)
    bass_st = np.stack([bass, bass], axis=1)
    keys_st = keys_st * np.array([0.92, 1.0])  # nudge the arp slightly right of the pads
    n = max(len(pads_st), len(keys_st), total)

    def fit(x: np.ndarray) -> np.ndarray:
        out = np.zeros((n, 2))
        out[: len(x)] = x
        return out

    mix = fit(drums_st) * 0.9 + fit(bass_st) * 0.85 + fit(pads_st) * 0.8 + fit(keys_st)
    # gentle bus glue, then fold the tail so the loop point is seamless
    mix = np.tanh(mix * 1.1) / 1.1
    loop = fold_tail(mix, loop_len)
    return normalize_rms(loop, -16.0, peak_db=-1.0).astype(np.float32)


# --------------------------------------------------------------------------- sound effects
def make_sfx(name: str) -> np.ndarray:
    rng = np.random.default_rng(sum(map(ord, name)))
    if name == "whoosh":
        n = int(0.75 * SR)
        noise = rng.standard_normal(n)
        env = np.sin(np.linspace(0, np.pi, n)) ** 2
        x = sweep_filter(noise, 400, 5200, kind="low") * env
        x = np.concatenate([x[: n // 2], x[n // 2 :] * np.linspace(1, 0.6, n - n // 2)])
        st = np.stack([x * np.linspace(1, 0.4, n), x * np.linspace(0.4, 1, n)], axis=1)  # left -> right
        out = st
    elif name == "impact":
        n = int(1.6 * SR)
        t = _t(n)
        boom = sine(42 + 60 * np.exp(-t / 0.05), n) * exp_decay(n, 0.45)
        crack = lowpass(rng.standard_normal(n), 3000) * exp_decay(n, 0.03) * 0.6
        out = reverb(np.tanh((boom + crack) * 1.8), rng, seconds=1.8, wet=0.3)
    elif name == "riser":
        n = int(1.6 * SR)
        t = _t(n)
        f = 180 * (2400 / 180) ** (t / t[-1])
        tone = sine(f, n) * 0.35 + sine(f * 1.5, n) * 0.15
        noise = sweep_filter(rng.standard_normal(n), 300, 9000, kind="low") * 0.6
        env = (t / t[-1]) ** 2.2
        x = (tone + noise) * env
        x[-int(0.01 * SR) :] *= np.linspace(1, 0, int(0.01 * SR))
        out = reverb(x, rng, seconds=0.6, wet=0.2)
    elif name == "tick":
        n = int(0.09 * SR)
        x = (sine(2200, n) * 0.6 + highpass(rng.standard_normal(n), 4000) * 0.4) * exp_decay(n, 0.012)
        out = pan(x, 0.0)
    elif name == "pop":
        n = int(0.16 * SR)
        t = _t(n)
        x = sine(520 + 520 * (t / t[-1]), n) * exp_decay(n, 0.045)
        out = pan(x * 0.9, 0.0)
    elif name == "glitch":
        n = int(0.32 * SR)
        x = np.zeros(n)
        pos = 0
        while pos < n:
            seg = int(rng.uniform(0.008, 0.035) * SR)
            kind = rng.integers(0, 3)
            if kind == 0:
                chunk = np.sign(sine(rng.uniform(200, 1400), seg)) * 0.35
            elif kind == 1:
                chunk = bandpass(rng.standard_normal(seg), 800, 6000) * 0.6
            else:
                chunk = np.zeros(seg)
            _place(x, chunk, pos)
            pos += seg
        x *= np.linspace(1, 0.3, n)
        out = np.stack([x, np.roll(x, 90)], axis=1)
    elif name == "swell":
        n = int(1.9 * SR)
        t = _t(n)
        chord = pad_chord([hz("A", 3), hz("C", 4), hz("E", 4), hz("A", 4)], n / SR, cutoff=2400)
        air = highpass(rng.standard_normal(n), 5000) * 0.25
        env = (t / t[-1]) ** 1.6
        x = (chord + air) * env
        x[-int(0.06 * SR) :] *= np.linspace(1, 0, int(0.06 * SR))
        out = reverb(x, rng, seconds=1.2, wet=0.35)
    else:
        raise KeyError(f"unknown sound effect '{name}'")
    if out.ndim == 1:
        out = pan(out, 0.0)
    peak = np.max(np.abs(out)) + 1e-9
    return (out / peak * 10 ** (-3 / 20)).astype(np.float32)


# --------------------------------------------------------------------------- files
def write_stereo(path: Path, x: np.ndarray) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(x, -1, 1), SR, subtype="PCM_16")


def build_library(assets_dir: Path, *, force: bool = False, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Generate every bed and effect that is missing. Returns a manifest (also written to disk)."""
    import json

    made, kept = [], []
    for mood in MOODS:
        p = assets_dir / "music" / f"{mood}.wav"
        if p.exists() and not force:
            kept.append(p.name)
            continue
        log(f"synthesising music bed '{mood}' ...")
        write_stereo(p, make_bed(mood))
        made.append(p.name)
    for name in SFX_NAMES:
        p = assets_dir / "sound_effects" / f"{name}.wav"
        if p.exists() and not force:
            kept.append(p.name)
            continue
        write_stereo(p, make_sfx(name))
        made.append(p.name)
    manifest = {
        "origin": "Procedurally synthesised by gta_factory (modules/asset_management/audio_library.py). No samples or third-party recordings.",
        "license": "Original work of the channel operator; free for commercial use.",
        "music": [f"{m}.wav" for m in MOODS],
        "sound_effects": [f"{s}.wav" for s in SFX_NAMES],
    }
    (assets_dir / "music").mkdir(parents=True, exist_ok=True)
    (assets_dir / "music" / "LIBRARY.json").write_text(json.dumps(manifest, indent=2))
    (assets_dir / "sound_effects" / "LIBRARY.json").write_text(json.dumps(manifest, indent=2))
    return {"generated": made, "kept": kept, "manifest": manifest}


def library_ready(assets_dir: Path) -> list[str]:
    """Names of missing library files (empty list = ready)."""
    missing = [f"music/{m}.wav" for m in MOODS if not (assets_dir / "music" / f"{m}.wav").exists()]
    missing += [f"sound_effects/{s}.wav" for s in SFX_NAMES if not (assets_dir / "sound_effects" / f"{s}.wav").exists()]
    return missing
