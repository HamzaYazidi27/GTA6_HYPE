"""Listen before choosing: the voices each provider offers, and a short sample of any of them.

The same sample sentence is read by every voice, exactly as a video would read it
(same lexicon, same scene reading), so voices can be compared side by side in the
dashboard ("Voix de la narration") or with ``python -m gta_factory voices --sample``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ...app.core.errors import ConfigError, FactoryError
from ...app.core.utils import stable_hash
from ...providers.tts import get_tts, provider_names

SAMPLE_TEXT = (
    "In Red Dead 2, Arthur could carry two long guns, and everything else stayed on his horse. "
    "In GTA 6, that idea moves to the trunk of your car. So, is this the end of the bottomless pockets?"
)

# Kokoro's English voices worth hearing first (its own quality notes: the US female "heart" voice is the most natural)
KOKORO_PICKS = [
    ("am_michael", "US male · Michael"),
    ("am_fenrir", "US male · Fenrir"),
    ("am_puck", "US male · Puck"),
    ("am_echo", "US male · Echo"),
    ("af_heart", "US female · Heart (the most natural Kokoro voice)"),
    ("af_bella", "US female · Bella"),
    ("af_nicole", "US female · Nicole (soft)"),
    ("bm_george", "UK male · George"),
    ("bm_fable", "UK male · Fable"),
    ("bf_emma", "UK female · Emma"),
]
OPENAI_LABELS = {"ash": "male, warm", "onyx": "male, deep", "echo": "male, light", "verse": "male, expressive", "ballad": "male, soft", "fable": "neutral", "alloy": "neutral", "sage": "female, calm", "coral": "female, bright", "nova": "female, young", "shimmer": "female, clear"}
VOICE_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def catalog(settings: Any) -> dict[str, Any]:
    """{"default": {"tts", "voice"}, "providers": [{"name", "label", "ready", "note", "voices": [{"id", "label"}]}]}."""
    default_tts = str(settings.get("providers.tts.default", "kokoro"))
    out = []
    kokoro = get_tts(settings, "kokoro")
    try:
        kokoro.check()
        ready, note = True, "free, runs on this computer"
    except FactoryError as exc:
        ready, note = False, exc.describe()
    out.append({"name": "kokoro", "label": "Kokoro (gratuit, local)", "ready": ready, "note": note, "voices": [{"id": v, "label": label} for v, label in KOKORO_PICKS]})
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    out.append({
        "name": "openai",
        "label": "OpenAI (payant, ~0,015 $ / minute de voix)",
        "ready": has_openai,
        "note": "natural US voices, steered by providers.tts.openai.instructions" if has_openai else "add OPENAI_API_KEY to .env",
        "voices": [{"id": v, "label": f"{v} · {OPENAI_LABELS.get(v, '')}".rstrip(" ·")} for v in get_tts(settings, "openai").list_voices()],
    })
    voices: list[dict[str, str]] = []
    note = "add ELEVENLABS_API_KEY to .env"
    if os.environ.get("ELEVENLABS_API_KEY"):
        note = "the most human voices; uses your ElevenLabs character quota"
        try:
            for row in get_tts(settings, "elevenlabs").list_voices():
                vid, _, rest = row.partition("  ")
                voices.append({"id": vid.strip(), "label": rest.strip() or vid.strip()})
        except FactoryError as exc:
            note = f"could not list your voices: {exc.message}"
    out.append({"name": "elevenlabs", "label": "ElevenLabs (payant, le plus humain)", "ready": bool(voices), "note": note, "voices": voices})
    cfg = settings.get(f"providers.tts.{default_tts}", {}) or {}
    return {"default": {"tts": default_tts, "voice": str(cfg.get("voice") or cfg.get("voice_id") or "")}, "providers": out, "text": SAMPLE_TEXT}


def check_choice(tts_name: str | None, voice: str | None) -> None:
    """Refuse a provider or a voice id that cannot be one (they reach file names and API URLs)."""
    if tts_name and tts_name not in provider_names():
        raise ConfigError(f"Unknown voice provider '{tts_name}'.", hint=f"Use one of: {', '.join(provider_names())}")
    if voice and not VOICE_ID.match(voice):
        raise ConfigError(f"Invalid voice id '{voice[:40]}'.")


def sample(settings: Any, tts_name: str, voice: str, *, out_dir: Path, text: str = SAMPLE_TEXT) -> Path:
    """The sample sentence read by one voice (cached WAV)."""
    from .engine import load_lexicon, synthesize_scene, whole_scene, write_wav

    check_choice(tts_name, voice)
    rh = settings.rhythm(vertical=False)  # the sample sounds like a 16:9 video
    tts = get_tts(settings, tts_name, voice or None, speed_factor=rh["voice_speed_factor"])
    tts.check()
    lexicon = load_lexicon(settings)
    whole = whole_scene(settings, tts)
    pause = rh["sentence_pause_seconds"]
    dest = out_dir / f"{tts_name}_{voice or 'default'}_{stable_hash({'sig': tts.signature(), 'text': text, 'whole': whole, 'pause': pause})[:8]}.wav"
    if not dest.exists():
        result = synthesize_scene(tts, text, lexicon, sentence_pause=pause, whole=whole)
        write_wav(dest, result["samples"])
    return dest
