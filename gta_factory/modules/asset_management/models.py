"""Local model files: download once, verify size, never commit (``assets/models`` is git-ignored)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import httpx

from ...app.core.errors import ConfigError

KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_FILES = {"kokoro-v1.0.onnx": 300_000_000, "voices-v1.0.bin": 20_000_000}  # minimum plausible sizes


def download(url: str, dest: Path, *, min_bytes: int = 0, log: Callable[[str], None] = print) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60.0, read=300.0)) as r:
            if r.status_code != 200:
                raise ConfigError(f"Download failed: HTTP {r.status_code} for {url}", hint="Check your connection and retry `python -m gta_factory setup`.")
            total = int(r.headers.get("content-length") or 0)
            done, step = 0, 0
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
                    done += len(chunk)
                    if total and done * 10 // total > step:
                        step = done * 10 // total
                        log(f"  {dest.name}: {done / 1e6:.0f} / {total / 1e6:.0f} MB")
    except httpx.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise ConfigError(f"Download failed for {dest.name}: {type(exc).__name__}", hint="Check your connection and retry `python -m gta_factory setup`.") from exc
    if tmp.stat().st_size < min_bytes:
        size = tmp.stat().st_size
        tmp.unlink(missing_ok=True)
        raise ConfigError(f"{dest.name} is incomplete ({size} bytes).", hint="Retry `python -m gta_factory setup`.")
    tmp.replace(dest)
    return dest


def ensure_kokoro(settings, *, log: Callable[[str], None] = print) -> list[str]:
    """Download the Kokoro ONNX model and voices if missing. Returns the files fetched."""
    cfg = settings.get("providers.tts.kokoro") or {}
    targets = {
        "kokoro-v1.0.onnx": settings.root / (cfg.get("model_path") or "assets/models/kokoro-v1.0.onnx"),
        "voices-v1.0.bin": settings.root / (cfg.get("voices_path") or "assets/models/voices-v1.0.bin"),
    }
    fetched = []
    for name, path in targets.items():
        if path.exists() and path.stat().st_size >= KOKORO_FILES[name]:
            continue
        log(f"downloading {name} (one time) ...")
        download(f"{KOKORO_RELEASE}/{name}", path, min_bytes=KOKORO_FILES[name], log=log)
        fetched.append(name)
    return fetched
