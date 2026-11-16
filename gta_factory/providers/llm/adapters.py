"""Concrete LLM adapters: Ollama (local, free - the default), Claude Code CLI (subscription), Anthropic API, OpenAI API."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any

from ...app.core.errors import ConfigError, ProviderError
from ...app.core.utils import which
from .base import LLMProvider
from .local import OllamaProvider


class ClaudeCodeProvider(LLMProvider):
    """Uses the official Claude Code CLI in headless mode (``claude -p``).

    This is how a Claude subscription (Pro/Max/Team) is used without an API key:
    the CLI must be installed and logged in on this machine (``claude login``).
    The call runs with every tool disabled and a replaced system prompt, so the
    CLI behaves as a plain text-completion endpoint.
    """

    name = "claude_code"

    def check(self) -> None:
        binary = self.config.get("binary") or "claude"
        if not which(binary):
            raise ConfigError(
                f"Claude Code CLI '{binary}' was not found on PATH.",
                hint="Install it (https://code.claude.com/docs) and run `claude login`, or switch providers.llm.default to anthropic / openai / ollama.",
            )

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8000) -> str:
        self.check()
        cmd = [
            self.config.get("binary") or "claude",
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--no-session-persistence",
            "--system-prompt",
            system,
        ]
        if self.config.get("model"):
            cmd += ["--model", str(self.config["model"])]
        timeout = float(self.config.get("timeout_seconds") or 1200)
        with tempfile.TemporaryDirectory(prefix="gta_factory_llm_") as neutral_cwd:
            try:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=neutral_cwd)
            except subprocess.TimeoutExpired as exc:
                raise ProviderError(f"Claude Code CLI timed out after {timeout:.0f}s") from exc
        if proc.returncode != 0 and not proc.stdout.strip():
            tail = (proc.stderr or "").strip()[-600:]
            raise ProviderError(
                f"Claude Code CLI failed (exit {proc.returncode}): {tail}",
                hint="Run `claude -p \"hello\"` in a terminal to confirm the CLI is logged in.",
            )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Claude Code CLI returned non-JSON output: {proc.stdout[:300]}") from exc
        if isinstance(payload, list):  # some versions emit the event list; the last element is the result
            payload = next((p for p in reversed(payload) if isinstance(p, dict) and p.get("type") == "result"), {})
        if payload.get("is_error") or payload.get("subtype") not in (None, "success"):
            raise ProviderError(
                f"Claude Code CLI reported an error: {str(payload.get('result') or payload.get('subtype'))[:400]}",
                hint="Run `claude login`, or use the free local model: set providers.llm.default: ollama in config.yaml and run `python -m gta_factory setup`.",
            )
        return str(payload.get("result") or "")


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API through the official SDK (needs ANTHROPIC_API_KEY)."""

    name = "anthropic"

    def check(self) -> None:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise ConfigError("ANTHROPIC_API_KEY is not set.", hint="Add it to .env, or pick another provider in config.yaml -> providers.llm.default.")

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8000) -> str:
        self.check()
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise ConfigError("The 'anthropic' package is not installed.", hint="pip install anthropic") from exc
        client = anthropic.Anthropic()
        model = self.config.get("model") or "claude-opus-5-5"
        kwargs: dict[str, Any] = {
            "model": model,
            # thinking shares the output budget, so leave generous head-room and stream to avoid HTTP timeouts
            "max_tokens": max(int(self.config.get("max_tokens") or 32000), max_tokens * 2),
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.config.get("effort"):
            kwargs["output_config"] = {"effort": self.config["effort"]}
        try:
            with client.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise ConfigError("Anthropic rejected the API key.", hint="Check ANTHROPIC_API_KEY in .env.") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("Anthropic rate limit reached.", hint="Wait a minute and resume the job.") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Could not reach the Anthropic API (network).") from exc
        if message.stop_reason == "refusal":
            raise ProviderError("The model declined this request (stop_reason=refusal).", hint="Rephrase the story brief or switch model.")
        text = "".join(block.text for block in message.content if block.type == "text")
        if message.stop_reason == "max_tokens":
            raise ProviderError("The model hit max_tokens before finishing.", hint="Raise providers.llm.anthropic.max_tokens in config.yaml.")
        return text


class OpenAIProvider(LLMProvider):
    """OpenAI (or any OpenAI-compatible endpoint) through the official SDK."""

    name = "openai"

    def check(self) -> None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise ConfigError("OPENAI_API_KEY is not set.", hint="Add it to .env, or pick another provider in config.yaml -> providers.llm.default.")

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8000) -> str:
        self.check()
        try:
            import openai
        except ImportError as exc:  # pragma: no cover
            raise ConfigError("The 'openai' package is not installed.", hint="pip install openai") from exc
        client = openai.OpenAI(base_url=self.config.get("base_url") or None)
        try:
            resp = client.chat.completions.create(
                model=self.config.get("model") or "gpt-5",
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                max_completion_tokens=max(int(self.config.get("max_tokens") or 16000), max_tokens),
            )
        except openai.AuthenticationError as exc:
            raise ConfigError("OpenAI rejected the API key.", hint="Check OPENAI_API_KEY in .env.") from exc
        except openai.RateLimitError as exc:
            raise ProviderError("OpenAI rate limit reached.", hint="Wait a minute and resume the job.") from exc
        except openai.APIError as exc:
            raise ProviderError(f"OpenAI API error: {exc}") from exc
        return resp.choices[0].message.content or ""


REGISTRY: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "claude_code": ClaudeCodeProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}
