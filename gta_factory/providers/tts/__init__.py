"""Text-to-speech adapters: local Kokoro, ElevenLabs and OpenAI.

Every adapter returns mono float32 samples plus, when the service provides
them, word timings. A provider that cannot produce audio raises
:class:`ProviderError`; the pipeline never substitutes silence for narration.

Voice policy: only stock voices shipped by the provider or voices the operator
owns/licenses. No adapter offers cloning of a third party's voice.
"""
from __future__ import annotations

import base64
import io
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ...app.core.errors import ConfigError, ProviderError


@dataclass
class TTSResult:
    samples: np.ndarray  # mono float32 in [-1, 1]
    sample_rate: int
    words: list[dict[str, Any]] = field(default_factory=list)  # [{"w": str, "s": float, "e": float}] when the provider supplies timings


def decode_audio_bytes(data: bytes) -> tuple[np.ndarray, int]:
    """Decode any compressed audio (mp3/ogg/wav) to mono float32 using ffmpeg."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", "24000", "pipe:1"], input=data, capture_output=True
    )
    if proc.returncode != 0 or not proc.stdout:
        raise ProviderError("Could not decode the audio returned by the TTS provider: " + proc.stderr.decode(errors="ignore")[-300:])
    return np.frombuffer(proc.stdout, dtype=np.float32).copy(), 24000


class TTSProvider:
    name = "base"

    def __init__(self, config: dict[str, Any], root: Path):
        self.config = config or {}
        self.root = root

    def voice_id(self) -> str:
        return str(self.config.get("voice") or self.config.get("voice_id") or "default")

    def signature(self) -> dict[str, Any]:
        """Everything that changes the sound; part of the per-scene cache key."""
        return {"provider": self.name, **{k: v for k, v in self.config.items() if k not in ("model_path", "voices_path")}}

    def check(self) -> None:
        """Raise when the provider is not usable."""

    def list_voices(self) -> list[str]:
        return []

    def synthesize(self, text: str) -> TTSResult:  # pragma: no cover - interface
        raise NotImplementedError


class KokoroProvider(TTSProvider):
    """Local neural TTS (Kokoro-82M, Apache-2.0) through ONNX Runtime. No network, no key."""

    name = "kokoro"
    _engine = None
    _engine_key: tuple[str, str] | None = None

    def _paths(self) -> tuple[Path, Path]:
        return self.root / (self.config.get("model_path") or "assets/models/kokoro-v1.0.onnx"), self.root / (self.config.get("voices_path") or "assets/models/voices-v1.0.bin")

    def check(self) -> None:
        model, voices = self._paths()
        if not model.exists() or not voices.exists():
            raise ConfigError(
                f"Kokoro model files are missing ({model.name}, {voices.name}).",
                hint="Run `python -m gta_factory setup` to download them (about 350 MB, one time).",
            )

    def _get(self):
        self.check()
        model, voices = self._paths()
        key = (str(model), str(voices))
        if KokoroProvider._engine is None or KokoroProvider._engine_key != key:
            try:
                from kokoro_onnx import Kokoro
            except ImportError as exc:  # pragma: no cover
                raise ConfigError("kokoro-onnx is not installed.", hint="pip install kokoro-onnx") from exc
            KokoroProvider._engine = Kokoro(str(model), str(voices))
            KokoroProvider._engine_key = key
        return KokoroProvider._engine

    def list_voices(self) -> list[str]:
        return sorted(self._get().get_voices())

    def synthesize(self, text: str) -> TTSResult:
        engine = self._get()
        voice = self.config.get("voice") or "am_michael"
        try:
            samples, sr = engine.create(text, voice=voice, speed=float(self.config.get("speed") or 1.0), lang="en-us")
        except Exception as exc:
            raise ProviderError(f"Kokoro failed on voice '{voice}': {type(exc).__name__}: {exc}", hint="Check the voice name with `python -m gta_factory voices`.") from exc
        if samples is None or len(samples) < sr * 0.15:
            raise ProviderError(f"Kokoro returned no audio for: {text[:80]!r}")
        return TTSResult(np.asarray(samples, dtype=np.float32), int(sr))


class ElevenLabsProvider(TTSProvider):
    """ElevenLabs API with character-level timestamps (needs ELEVENLABS_API_KEY and a voice you may use)."""

    name = "elevenlabs"

    def check(self) -> None:
        if not os.environ.get("ELEVENLABS_API_KEY"):
            raise ConfigError("ELEVENLABS_API_KEY is not set.", hint="Add it to .env or switch providers.tts.default to kokoro.")
        if not self.config.get("voice_id"):
            raise ConfigError("providers.tts.elevenlabs.voice_id is empty.", hint="Set it in config.yaml to a voice from your ElevenLabs account that you are licensed to use.")

    def list_voices(self) -> list[str]:
        import httpx

        if not os.environ.get("ELEVENLABS_API_KEY"):  # listing needs the key only, not a chosen voice
            raise ConfigError("ELEVENLABS_API_KEY is not set.", hint="Add it to .env or switch providers.tts.default to kokoro.")
        r = httpx.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=30)
        if r.status_code != 200:
            raise ProviderError(f"ElevenLabs voices request failed: HTTP {r.status_code}")
        return [f"{v['voice_id']}  {v.get('name')}  ({v.get('category')})" for v in r.json().get("voices", [])]

    def synthesize(self, text: str) -> TTSResult:
        import httpx

        self.check()
        body = {
            "text": text,
            "model_id": self.config.get("model") or "eleven_multilingual_v2",
            "voice_settings": {
                "stability": float(self.config.get("stability", 0.45)),
                "similarity_boost": float(self.config.get("similarity_boost", 0.8)),
                "style": float(self.config.get("style", 0.25)),
                "use_speaker_boost": True,
            },
        }
        try:
            r = httpx.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{self.config['voice_id']}/with-timestamps",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                json=body,
                timeout=180,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach ElevenLabs: {type(exc).__name__}") from exc
        if r.status_code == 401:
            raise ConfigError("ElevenLabs rejected the API key.", hint="Check ELEVENLABS_API_KEY in .env.")
        if r.status_code != 200:
            raise ProviderError(f"ElevenLabs error HTTP {r.status_code}: {r.text[:300]}", hint="Check your character quota and the voice id.")
        payload = r.json()
        samples, sr = decode_audio_bytes(base64.b64decode(payload["audio_base64"]))
        words: list[dict[str, Any]] = []
        al = payload.get("alignment") or {}
        chars, starts, ends = al.get("characters") or [], al.get("character_start_times_seconds") or [], al.get("character_end_times_seconds") or []
        cur, s0, e0 = "", 0.0, 0.0
        for ch, s, e in zip(chars, starts, ends):
            if ch.isspace():
                if cur:
                    words.append({"w": cur, "s": float(s0), "e": float(e0)})
                cur = ""
                continue
            if not cur:
                s0 = s
            cur += ch
            e0 = e
        if cur:
            words.append({"w": cur, "s": float(s0), "e": float(e0)})
        return TTSResult(samples, sr, words)


class OpenAITTSProvider(TTSProvider):
    """OpenAI text-to-speech (needs OPENAI_API_KEY). Uses the provider's stock voices."""

    name = "openai"

    def check(self) -> None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise ConfigError("OPENAI_API_KEY is not set.", hint="Add it to .env or switch providers.tts.default to kokoro.")

    def list_voices(self) -> list[str]:
        return ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]

    def synthesize(self, text: str) -> TTSResult:
        self.check()
        try:
            import openai
        except ImportError as exc:  # pragma: no cover
            raise ConfigError("The 'openai' package is not installed.", hint="pip install openai") from exc
        client = openai.OpenAI()
        kwargs: dict[str, Any] = {"model": self.config.get("model") or "gpt-4o-mini-tts", "voice": self.config.get("voice") or "ash", "input": text, "response_format": "wav"}
        if self.config.get("instructions") and "tts-1" not in kwargs["model"]:
            kwargs["instructions"] = self.config["instructions"]
        try:
            resp = client.audio.speech.create(**kwargs)
        except openai.AuthenticationError as exc:
            raise ConfigError("OpenAI rejected the API key.", hint="Check OPENAI_API_KEY in .env.") from exc
        except openai.APIError as exc:
            raise ProviderError(f"OpenAI TTS error: {exc}") from exc
        import soundfile as sf

        data, sr = sf.read(io.BytesIO(resp.read()), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        return TTSResult(np.asarray(data, dtype=np.float32), int(sr))


REGISTRY: dict[str, type[TTSProvider]] = {"kokoro": KokoroProvider, "elevenlabs": ElevenLabsProvider, "openai": OpenAITTSProvider}
_EXTRA: dict[str, type[TTSProvider]] = {}


def register_tts(name: str, cls: type[TTSProvider]) -> None:
    _EXTRA[name] = cls


def provider_names() -> list[str]:
    return sorted({**REGISTRY, **_EXTRA})


def get_tts(settings: Any, name: str | None = None, voice: str | None = None, *, speed_factor: float = 1.0) -> TTSProvider:
    """The configured voice. ``speed_factor`` (Shorts read a little faster) applies to Kokoro, the only engine with a speed knob here."""
    chosen = name or settings.get("providers.tts.default", "kokoro")
    pool = {**REGISTRY, **_EXTRA}
    if chosen not in pool:
        raise ConfigError(f"unknown TTS provider '{chosen}'", hint=f"Use one of: {', '.join(sorted(pool))}")
    cfg = dict(settings.get(f"providers.tts.{chosen}", {}) or {})
    if voice:
        cfg["voice_id" if chosen == "elevenlabs" else "voice"] = voice
    if chosen == "kokoro" and speed_factor and abs(speed_factor - 1.0) > 1e-3:
        cfg["speed"] = round(min(1.35, float(cfg.get("speed") or 1.0) * speed_factor), 3)
    return pool[chosen](cfg, settings.root)
