"""LLM provider contract and the JSON helper every editorial module uses.

A provider only has to implement :meth:`LLMProvider.complete` (system prompt +
user prompt -> text). Structured calls go through :meth:`LLMProvider.complete_json`,
which extracts the JSON document, validates required keys and asks the model to
repair its own output once before giving up.
"""
from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from ...app.core.errors import ProviderError

JSON_RULES = (
    "Return ONLY one valid JSON document. No markdown fences, no commentary before or after. "
    "Use double quotes, no trailing commas, and escape line breaks inside strings as \\n."
)


@dataclass
class LLMUsage:
    calls: int = 0
    seconds: float = 0.0
    input_chars: int = 0
    output_chars: int = 0
    log: list[dict[str, Any]] = field(default_factory=list)


def extract_json(text: str) -> Any:
    """Parse the first JSON document found in ``text`` (tolerates fences and stray prose)."""
    if text is None:
        raise ValueError("empty response")
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", s, re.S)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (s.find("{"), s.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON object found in model output")
    start = min(starts)
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(s[start:])
        return obj
    except json.JSONDecodeError as exc:
        # last resort: trim to the last closing bracket
        end = max(s.rfind("}"), s.rfind("]"))
        if end > start:
            try:
                return json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"invalid JSON from model: {exc}") from exc


class LLMProvider(ABC):
    name = "base"
    # providers that can force syntactically valid JSON (e.g. Ollama's ``format: json``) set this and
    # accept a ``json_mode`` keyword in :meth:`complete`
    supports_json_mode = False

    def __init__(self, config: dict[str, Any]):
        self.config = config or {}
        self.usage = LLMUsage()

    @abstractmethod
    def complete(self, system: str, prompt: str, *, max_tokens: int = 8000) -> str:
        """Return the model's text answer. Must raise :class:`ProviderError` on failure."""

    def describe(self) -> str:
        return f"{self.name}:{self.config.get('model') or 'default'}"

    def check(self) -> None:
        """Raise :class:`ProviderError` when the provider cannot be used (missing key, binary ...)."""

    # ------------------------------------------------------------------ helpers
    def _timed(self, system: str, prompt: str, max_tokens: int, label: str, *, json_mode: bool = False) -> str:
        t0 = time.time()
        if json_mode and self.supports_json_mode:
            out = self.complete(system, prompt, max_tokens=max_tokens, json_mode=True)  # type: ignore[call-arg]
        else:
            out = self.complete(system, prompt, max_tokens=max_tokens)
        dt = time.time() - t0
        self.usage.calls += 1
        self.usage.seconds += dt
        self.usage.input_chars += len(system) + len(prompt)
        self.usage.output_chars += len(out or "")
        self.usage.log.append({"label": label, "seconds": round(dt, 1), "in_chars": len(system) + len(prompt), "out_chars": len(out or "")})
        return out

    def complete_text(self, system: str, prompt: str, *, max_tokens: int = 8000, label: str = "text") -> str:
        out = self._timed(system, prompt, max_tokens, label)
        if not out or not out.strip():
            raise ProviderError(f"{self.describe()} returned an empty answer for '{label}'")
        return out.strip()

    def complete_json(
        self,
        system: str,
        prompt: str,
        *,
        max_tokens: int = 8000,
        label: str = "json",
        required_keys: tuple[str, ...] = (),
        validate: Callable[[Any], str | None] | None = None,
        retries: int = 2,
    ) -> Any:
        """Structured call. ``validate`` returns an error string to trigger a repair round, or None."""
        sys_prompt = f"{system}\n\n{JSON_RULES}"
        attempt_prompt = prompt
        last_error = ""
        for attempt in range(retries + 1):
            raw = self._timed(sys_prompt, attempt_prompt, max_tokens, f"{label}#{attempt + 1}", json_mode=True)
            try:
                data = extract_json(raw)
                if validate and isinstance(data, dict) and validate(data):
                    # JSON mode makes some models wrap a requested top-level list in an object
                    lists = [v for v in data.values() if isinstance(v, list)]
                    if len(data) == 1 and len(lists) == 1 and not validate(lists[0]):
                        data = lists[0]
                if required_keys and isinstance(data, dict):
                    missing = [k for k in required_keys if k not in data]
                    if missing:
                        raise ValueError(f"missing required keys: {', '.join(missing)}")
                if validate:
                    problem = validate(data)
                    if problem:
                        raise ValueError(problem)
                return data
            except ValueError as exc:
                last_error = str(exc)
                attempt_prompt = (
                    f"{prompt}\n\n---\nYour previous answer was rejected: {last_error}\n"
                    f"Previous answer (truncated):\n{(raw or '')[:6000]}\n\n"
                    "Return the corrected, complete JSON document only."
                )
        raise ProviderError(
            f"{self.describe()} did not return valid JSON for '{label}' after {retries + 1} attempts: {last_error}",
            hint="Re-run the step; if it keeps failing, switch providers.llm.default in config.yaml.",
        )
