"""Configuration loading: ``config.yaml`` + ``.env`` merged over built-in defaults.

Secrets (API keys) are read from the process environment only and are never
written to logs. ``redact()`` is used by the logger to scrub anything that
looks like a key.
"""
from __future__ import annotations

import copy
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("GTA_FACTORY_HOME") or Path(__file__).resolve().parents[3])

SECRET_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
    "PEXELS_API_KEY",
    "PIXABAY_API_KEY",
    "YOUTUBE_API_KEY",
    "YOUTUBE_CLIENT_SECRETS_FILE",
)

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|xi-api-key|bearer)([\"'=:\s]+)([A-Za-z0-9_\-\.]{12,})"),
]


def redact(text: str) -> str:
    """Remove anything that looks like a credential from a log line."""
    out = text
    for name in SECRET_ENV_NAMES:
        val = os.environ.get(name)
        if val and len(val) >= 8:
            out = out.replace(val, f"<{name}>")
    for rx in _SECRET_PATTERNS[:3]:
        out = rx.sub("<redacted>", out)
    out = _SECRET_PATTERNS[3].sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", out)
    return out


def load_dotenv_file(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE lines). Existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Settings:
    """Read-only view over the merged configuration with dotted-path access."""

    def __init__(self, data: dict[str, Any], root: Path):
        self.data = data
        self.root = root

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def __getitem__(self, dotted: str) -> Any:
        sentinel = object()
        v = self.get(dotted, sentinel)
        if v is sentinel:
            raise KeyError(f"missing configuration key: {dotted}")
        return v

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    # --- frequently used paths -------------------------------------------------
    @property
    def jobs_dir(self) -> Path:
        return self.root / "jobs"

    @property
    def outputs_dir(self) -> Path:
        return self.root / "outputs"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def db_path(self) -> Path:
        return Path(os.environ.get("GTA_FACTORY_DB") or self.root / "jobs" / "factory.sqlite3")

    def format(self, name: str) -> dict[str, Any]:
        fmts = self.get("formats", {})
        if name not in fmts:
            raise KeyError(f"unknown format '{name}'. Known formats: {', '.join(fmts)}")
        return fmts[name]

    def video_profile(self, fmt: str) -> dict[str, Any]:
        key = "short" if self.format(fmt).get("aspect") == "9:16" else "long"
        return self.get(f"video.{key}")

    def source_tier(self, domain: str) -> str:
        d = (domain or "").lower().removeprefix("www.")
        for tier in ("primary", "secondary"):
            for known in self.get(f"sources.{tier}", []):
                if d == known or d.endswith("." + known):
                    return tier
        return "other"


@lru_cache(maxsize=4)
def _load(root_str: str, config_file: str) -> Settings:
    root = Path(root_str)
    load_dotenv_file(root / ".env")
    defaults_path = Path(__file__).with_name("defaults.yaml")
    defaults = yaml.safe_load(defaults_path.read_text(encoding="utf-8")) or {}
    user_path = Path(config_file)
    user = yaml.safe_load(user_path.read_text(encoding="utf-8")) if user_path.exists() else {}
    return Settings(_deep_merge(defaults, user or {}), root)


def get_settings(root: Path | None = None, config_file: Path | None = None) -> Settings:
    r = Path(root) if root else ROOT
    return _load(str(r), str(config_file or os.environ.get("GTA_FACTORY_CONFIG") or r / "config.yaml"))


def reset_settings_cache() -> None:
    _load.cache_clear()
