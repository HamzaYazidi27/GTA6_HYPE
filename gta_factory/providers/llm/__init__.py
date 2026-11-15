"""LLM provider registry."""
from __future__ import annotations

from typing import Any

from ...app.core.errors import ConfigError
from .adapters import REGISTRY
from .base import LLMProvider, extract_json

_EXTRA: dict[str, type[LLMProvider]] = {}


def register_llm(name: str, cls: type[LLMProvider]) -> None:
    """Register an additional provider (used by plugins and by the test-suite's scripted provider)."""
    _EXTRA[name] = cls


def get_llm(settings: Any, name: str | None = None) -> LLMProvider:
    chosen = name or settings.get("providers.llm.default", "claude_code")
    pool = {**REGISTRY, **_EXTRA}
    if chosen not in pool:
        raise ConfigError(f"unknown LLM provider '{chosen}'", hint=f"Use one of: {', '.join(sorted(pool))}")
    return pool[chosen](settings.get(f"providers.llm.{chosen}", {}) or {})


__all__ = ["LLMProvider", "get_llm", "register_llm", "extract_json"]
