"""Final audio mix and mux.

voice (mono, already at ``audio.voice_lufs``)
  + music bed (looped, ``audio.music_gain_db``, side-chain ducked by ``audio.music_duck_db`` while the voice speaks)
  + sound effects on scene transitions (``audio.sfx_gain_db``)
  -> gain to ``audio.master_lufs`` + look-ahead limiter at ``audio.true_peak_db`` (EBU R128 measured)
  -> muxed with the silent picture into the final H.264 / AAC MP4.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from ...app.core.errors import RenderError
from ..voice_generation.engine import WORK_SR, read_wav, resample

SFX_TIMING = {
    # seconds relative to the scene start; negative = the sound starts before the cut
    "riser": "end_at_cut",
    "swell": -0.9,
    "whoosh": -0.25,
    "impact": 0.0,
    "glitch": 0.0,
    "tick": 0.12,
    "pop": 0.18,
}
SFX_EXTRA_DB = {"riser": -2.0, "swell": -3.0, "tick": -4.0, "pop": -3.0, "glitch": -4.0, "impact": 0.0, "whoosh": -1.0}


def _read_stereo(path: Path) -> np.ndarray:
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    data = data[:, :2]
    if sr != WORK_SR:
        data = np.stack([resample(data[:, 0], sr), resample(data[:, 1], sr)], axis=1)
    return data


def _tile(loop: np.ndarray, n: int) -> np.ndarray:
    reps = int(np.ceil(n / len(loop)))
    return np.tile(loop, (reps, 1))[:n]


def voice_activity(voice: np.ndarray, *, hop: float = 0.01, threshold_db: float = -42.0, attack: float = 0.04, release: float = 0.45) -> np.ndarray:
    """Smoothed 0..1 voice-activity curve at sample rate (for side-chain ducking)."""
    h = int(WORK_SR * hop)
    n = len(voice) // h
    if n == 0:
        return np.zeros(len(voice))
    rms = np.sqrt(np.mean(voice[: n * h].reshape(n, h) ** 2, axis=1) + 1e-12)
    active = (20 * np.log10(rms) > threshold_db).astype(float)
    a_up, a_dn = np.exp(-hop / attack), np.exp(-hop / release)
    env = np.zeros(n)
    level = 0.0
    for i, x in enumerate(active):
        coef = a_up if x > level else a_dn
        level = coef * level + (1 - coef) * x
        env[i] = level
    curve = np.interp(np.arange(len(voice)), np.arange(n) * h + h / 2, env)
    return curve


def _db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x)) + 1e-12)))


def limit(x: np.ndarray, ceiling_db: float, *, lookahead: float = 0.005, release: float = 0.08) -> tuple[np.ndarray, float]:
    """Look-ahead peak limiter (stereo-linked). Returns (signal, max gain reduction in dB).

    The gain curve is computed per 1 ms block: instant attack inside the
    look-ahead window, exponential release, so speech transients are caught
    without the pumping of a dynamic loudness normaliser.
    """
    from scipy.ndimage import maximum_filter1d

    ceiling = 10 ** (ceiling_db / 20)
    peak = np.max(np.abs(x), axis=1) if x.ndim == 2 else np.abs(x)
    look = max(1, int(lookahead * WORK_SR))
    env = maximum_filter1d(peak, size=2 * look + 1)
    target = np.minimum(1.0, ceiling / np.maximum(env, 1e-9))
    blk = 48
    nb = int(np.ceil(len(target) / blk))
    padded = np.concatenate([target, np.ones(nb * blk - len(target))])
    block_gain = padded.reshape(nb, blk).min(axis=1)
    block_gain = np.minimum(block_gain, np.concatenate([block_gain[1:], [1.0]]))  # never late into the next block
    coef = np.exp(-(blk / WORK_SR) / release)
    smooth = np.empty(nb)
    level = 1.0
    for i, g in enumerate(block_gain):
        level = g if g < level else coef * level + (1 - coef) * g
        smooth[i] = level
    gain = np.repeat(smooth, blk)[: len(target)]
    y = x * (gain[:, None] if x.ndim == 2 else gain)
    y = np.clip(y, -ceiling, ceiling)  # sample-peak safety net
    return y.astype(np.float32), float(-20 * np.log10(max(1e-9, gain.min())))


def normalize_master(mix: np.ndarray, work: Path, out: Path, *, lufs: float, true_peak: float) -> dict[str, Any]:
    """Gain to the loudness target, then limit to the peak ceiling; re-measured with ffmpeg's EBU R128 meter."""
    import soundfile as sf

    from ..voice_generation.engine import measure_loudness

    work.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(work), mix, WORK_SR, subtype="FLOAT")
    measured = measure_loudness(work)
    # 0.6 dB margin under the ceiling covers inter-sample peaks after AAC encoding
    ceiling = true_peak - 0.6
    gain_db = lufs - measured["lufs"]
    y, reduction = limit(mix * (10 ** (gain_db / 20)), ceiling)
    sf.write(str(work), y, WORK_SR, subtype="FLOAT")
    after = measure_loudness(work)
    if abs(after["lufs"] - lufs) > 0.4:  # limiting removed some loudness: one corrective pass
        gain_db += lufs - after["lufs"]
        y, reduction = limit(mix * (10 ** (gain_db / 20)), ceiling)
    sf.write(str(out), y, WORK_SR, subtype="PCM_24")
    final = measure_loudness(out)
    work.unlink(missing_ok=True)
    return {"input_lufs": measured["lufs"], "applied_gain_db": round(gain_db, 2), "limiter_max_reduction_db": round(reduction, 2), "output_lufs": final["lufs"], "output_true_peak_db": final["true_peak_db"], "output_lra": final["lra"]}


def _mix_source_audio(mix: np.ndarray, activity: np.ndarray, cuts: list[dict[str, Any]]) -> int:
    """Put the live part of each selected original under the corresponding narration."""
    used = 0
    for cut in cuts:
        at = round(cut["record_in"] * WORK_SR)
        duration = cut["record_out"] - cut["record_in"] - (cut.get("hold") or 0)
        if duration <= 0 or at < 0 or at >= len(mix):
            continue
        audio = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(cut["source_in"]), "-i", cut["source_audio_path"],
                                "-t", str(duration), "-map", "0:a:0", "-ac", "2", "-ar", str(WORK_SR),
                                "-f", "f32le", "pipe:1"], capture_output=True)
        if audio.returncode or not audio.stdout:
            continue
        clip = np.frombuffer(audio.stdout, dtype="<f4").reshape(-1, 2)[:len(mix)-at]
        gain = min(1., 0.065 / max(0.001, float(np.sqrt(np.mean(clip**2)))))
        level = gain * (1 - 0.84 * activity[at:at+len(clip)])
        edge = min(round(.015 * WORK_SR), len(clip) // 2)
        if edge:
            fade = np.linspace(0, 1, edge)
            level[:edge] *= fade
            level[-edge:] *= fade[::-1]
        mix[at:at+len(clip)] += clip * level[:, None]
        used += 1
    return used


def build_mix(settings: Any, *, master: dict[str, Any], scenes: list[dict[str, Any]], voice_path: Path, out_wav: Path, events: list[dict[str, Any]] | None = None, music_gain_db: float | None = None, source_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Mix sound cues with optional explicit start/end alignment and per-event gain trim in dB."""
    voice, sr = read_wav(voice_path)
    voice = resample(voice, sr)
    n = len(voice)
    fps = master["fps"]
    total_n = int(round(master["total_frames"] / fps * WORK_SR))
    if total_n > n:
        voice = np.concatenate([voice, np.zeros(total_n - n, dtype=np.float32)])
        n = total_n
    lib = settings.assets_dir
    mood = str(settings.get("audio.music_mood", "pulse"))
    bed_path = lib / "music" / f"{mood}.wav"
    if not bed_path.exists():
        raise RenderError(f"Music bed '{mood}' is missing ({bed_path}).", hint="Run `python -m gta_factory setup` to generate the audio library.")

    # music: loop, duck, fade
    bed = _tile(_read_stereo(bed_path), n)
    activity = voice_activity(voice)
    gain_db = float(music_gain_db if music_gain_db is not None else settings.get("audio.music_gain_db", -19.0)) + float(settings.get("audio.music_duck_db", -9.0)) * activity
    music = bed * (10 ** (gain_db / 20))[:, None]
    fade_in, fade_out = int(1.2 * WORK_SR), int(2.5 * WORK_SR)
    music[:fade_in] *= np.linspace(0, 1, min(fade_in, n))[:, None]
    music[-fade_out:] *= np.linspace(1, 0, min(fade_out, n))[:, None]

    # sound effects on the cuts
    sfx = np.zeros((n, 2), dtype=np.float32)
    sfx_gain = float(settings.get("audio.sfx_gain_db", -9.0))
    placed = {p["scene_number"]: p for p in master["scenes"]}
    cache: dict[str, np.ndarray] = {}
    used: list[dict[str, Any]] = []
    cues = [(sc["scene_number"], sc.get("sound_effect") or "none", placed[sc["scene_number"]]["start_time"], None, 0.0) for sc in scenes]
    cues += [(None, ev["sfx"], float(ev["at"]), ev.get("timing"), float(ev.get("trim_db", 0.0))) for ev in events or []]
    for scene_n, name, start, how, trim_db in cues:
        if name == "none":
            continue
        path = lib / "sound_effects" / f"{name}.wav"
        if not path.exists():
            continue
        clip = cache.setdefault(name, _read_stereo(path))
        if how is not None:  # an event: "at" is already the sound's start, or its end ("end_at")
            at_s = start - len(clip) / WORK_SR if how == "end_at" else start
        else:
            timing = SFX_TIMING.get(name, 0.0)
            at_s = start - len(clip) / WORK_SR if timing == "end_at_cut" else start + float(timing)
        at = int(round(at_s * WORK_SR))
        src = clip
        if at < 0:
            src, at = clip[-at:], 0
        end = min(n, at + len(src))
        if end <= at:
            continue
        g = 10 ** ((sfx_gain + SFX_EXTRA_DB.get(name, 0.0) + trim_db) / 20)
        sfx[at:end] += src[: end - at] * g
        used.append({"scene_number": scene_n, "sfx": name, "at": round(at / WORK_SR, 3)})
        if trim_db:
            used[-1]["trim_db"] = trim_db

    voice_st = np.repeat(voice[:, None], 2, axis=1)
    mix = voice_st + music + sfx
    # stats for QC: how far the voice sits above the music while it is speaking
    speaking = activity > 0.6
    stats = {"music_mood": mood, "sfx_events": len(used), "sfx": used}
    if source_events:
        stats["source_audio_cuts"] = _mix_source_audio(mix, activity, source_events)
    if speaking.any():
        stats["voice_db_while_speaking"] = round(_db(voice[speaking]), 2)
        stats["music_db_while_speaking"] = round(_db(music[speaking].mean(axis=1)), 2)
        stats["voice_over_music_db"] = round(stats["voice_db_while_speaking"] - stats["music_db_while_speaking"], 2)
    stats.update(
        normalize_master(
            mix.astype(np.float32),
            out_wav.with_name(out_wav.stem + "_work.wav"),
            out_wav,
            lufs=float(settings.get("audio.master_lufs", -14.0)),
            true_peak=float(settings.get("audio.true_peak_db", -1.0)),
        )
    )
    stats["seconds"] = round(n / WORK_SR, 3)
    return stats


def mux(settings: Any, *, video: Path, audio: Path, out: Path) -> Path:
    v = settings.get("video") or {}
    codec = {"h264": "libx264", "hevc": "libx265", "h265": "libx265"}.get(str(v.get("codec", "h264")), "libx264")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.mp4")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", codec, "-preset", str(v.get("preset", "medium")), "-crf", str(v.get("crf", 18)),
        "-pix_fmt", str(v.get("pixel_format", "yuv420p")),
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", str(v.get("audio_codec", "aac")), "-b:a", str(v.get("audio_bitrate", "192k")), "-ar", str(WORK_SR),
        "-movflags", "+faststart", "-shortest", str(tmp),
    ]  # fmt: skip
    if codec == "libx264":
        cmd[cmd.index("-pix_fmt"):cmd.index("-pix_fmt")] = ["-profile:v", "high"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not tmp.exists():
        raise RenderError("Final encode failed: " + proc.stderr[-800:], hint="Check free disk space and that ffmpeg has libx264 (`ffmpeg -encoders | grep 264`).")
    tmp.replace(out)
    return out
