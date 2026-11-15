"""Typed errors. Every pipeline failure carries a message a human can act on."""
from __future__ import annotations


class FactoryError(Exception):
    """Base class. ``hint`` tells the operator what to do next."""

    def __init__(self, message: str, *, hint: str = "", step: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.step = step

    def describe(self) -> str:
        return self.message + (f"\n  -> {self.hint}" if self.hint else "")


class ConfigError(FactoryError):
    """Missing or invalid configuration / credentials."""


class ProviderError(FactoryError):
    """An external provider (LLM, TTS, search, stock) failed or is unavailable."""


class ExtractionError(FactoryError):
    """An article could not be fetched or contained no usable text."""


class NoveltyStop(FactoryError):
    """The story is a duplicate or too weak; the job stops on purpose (not a crash)."""


class RightsError(FactoryError):
    """An asset failed the rights review gate."""


class RenderError(FactoryError):
    """Rendering or encoding failed."""


class QualityGateError(FactoryError):
    """A critical quality-control check failed; the job must not be marked completed."""
