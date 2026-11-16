"""Free, fully local text generation through Ollama: no account, no API key, no subscription.

Ollama (https://ollama.com) runs open-weight models on the machine itself (Metal
on Apple Silicon, CUDA/ROCm or CPU elsewhere). The factory talks to its local
HTTP API on ``127.0.0.1:11434``.

Quality measures for local models:

* **JSON mode** (``format: "json"``) for every structured call, so the output is
  always syntactically valid; the factory's own validators and repair round then
  check the content.
* A model picked for the machine's memory when ``model: auto`` (see
  :data:`MODEL_LADDER`), the largest one that fits comfortably.
* Context window sized per request in a few fixed buckets (a change of context
  size reloads the model, so it is kept stable between calls).
* Streaming, so a slow machine never hits an HTTP timeout on a long script.
* Thinking switched off by default for hybrid models (faster, and the pipeline
  already does its own audit/revision rounds); turn it on with ``think: true``.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

import httpx

from ...app.core.errors import ConfigError, ProviderError
from .base import LLMProvider

DEFAULT_HOST = "http://127.0.0.1:11434"

# (minimum total RAM in GB, model tag, approx. download size) - best first.
# All are open-weight models that Ollama serves for free.
MODEL_LADDER: list[tuple[float, str, str]] = [
    (46, "qwen3:32b", "20 GB"),
    (30, "qwen3:30b", "19 GB"),
    (15, "qwen3:14b", "9.3 GB"),
    (7.5, "qwen3:8b", "5.2 GB"),
    (0, "qwen3:4b", "2.6 GB"),
]
CTX_BUCKETS = (8192, 16384, 32768, 65536)


def total_ram_gb() -> float:
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            return int(out.strip()) / 1024**3
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (OSError, ValueError, subprocess.SubprocessError):
        return 8.0


def recommended_model(ram_gb: float | None = None) -> tuple[str, str]:
    """The best model of :data:`MODEL_LADDER` for this machine: (tag, download size)."""
    ram = total_ram_gb() if ram_gb is None else ram_gb
    for min_ram, tag, size in MODEL_LADDER:
        if ram >= min_ram:
            return tag, size
    return MODEL_LADDER[-1][1], MODEL_LADDER[-1][2]


def _same_model(a: str, b: str) -> bool:
    norm = lambda t: t if ":" in t else f"{t}:latest"  # noqa: E731
    return norm(a) == norm(b)


class OllamaProvider(LLMProvider):
    """Local open-weight model served by Ollama (the factory's free default)."""

    name = "ollama"
    supports_json_mode = True

    def __init__(self, config: dict[str, Any], *, transport: httpx.BaseTransport | None = None):
        super().__init__(config)
        self.host = str(self.config.get("host") or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self._transport = transport
        self._model: str | None = None
        self._ctx = 0

    # ------------------------------------------------------------- server / models
    def _client(self, timeout: httpx.Timeout | float = 10) -> httpx.Client:
        return httpx.Client(timeout=timeout, transport=self._transport)

    def server_up(self) -> bool:
        try:
            with self._client(3) as c:
                return c.get(f"{self.host}/api/version").status_code == 200
        except httpx.HTTPError:
            return False

    def version(self) -> str:
        with self._client(5) as c:
            return str(c.get(f"{self.host}/api/version").json().get("version", "?"))

    def installed_models(self) -> list[str]:
        try:
            with self._client(10) as c:
                r = c.get(f"{self.host}/api/tags")
        except httpx.HTTPError as exc:
            raise self._unreachable() from exc
        return [m.get("name") or m.get("model") for m in r.json().get("models") or []]

    def _unreachable(self) -> ConfigError:
        installed = shutil.which("ollama")
        hint = (
            "Start it: open the Ollama app, or run `ollama serve` in a terminal."
            if installed
            else "Install Ollama (free): https://ollama.com/download (macOS: `brew install ollama`), then run `python -m gta_factory setup`."
        )
        return ConfigError(f"Ollama is not running at {self.host}.", hint=hint)

    @property
    def model(self) -> str:
        """The configured tag, or for ``auto`` the best model for this machine (an installed one when possible)."""
        if self._model:
            return self._model
        wanted = str(self.config.get("model") or "auto").strip()
        if wanted != "auto":
            self._model = wanted
            return wanted
        best, _ = recommended_model()
        try:
            have = self.installed_models()
        except ConfigError:
            return best
        if any(_same_model(best, h) for h in have):
            self._model = best
        else:  # use the best ladder model already downloaded rather than fail
            ladder = [tag for _, tag, _ in MODEL_LADDER]
            start = ladder.index(best)
            self._model = next((t for t in ladder[start:] if any(_same_model(t, h) for h in have)), best)
        return self._model

    def describe(self) -> str:
        return f"ollama:{self.model} (local, free)"

    def check(self) -> None:
        if not self.server_up():
            raise self._unreachable()
        have = self.installed_models()
        if not any(_same_model(self.model, h) for h in have):
            raise ConfigError(f"The local model '{self.model}' is not downloaded yet.", hint=f"Run `python -m gta_factory setup` (or `ollama pull {self.model}`).")

    def start_server(self, log: Callable[[str], None] = print, wait: float = 20) -> bool:
        """Start ``ollama serve`` in the background when the binary exists but nothing listens."""
        if self.server_up():
            return True
        binary = shutil.which("ollama")
        if not binary:
            return False
        if sys.platform == "darwin" and shutil.which("open") and os.path.exists("/Applications/Ollama.app"):
            subprocess.Popen(["open", "-g", "-a", "Ollama"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen([binary, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        log("starting the Ollama server ...")
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.server_up():
                return True
            time.sleep(0.5)
        return self.server_up()

    def pull(self, model: str | None = None, log: Callable[[str], None] = print) -> None:
        """Download a model through the local server, with a progress line."""
        tag = model or self.model
        last = -10
        try:
            with self._client(httpx.Timeout(30, read=None)) as c, c.stream("POST", f"{self.host}/api/pull", json={"model": tag, "stream": True}) as r:
                if r.status_code != 200:
                    raise ProviderError(f"Ollama could not download '{tag}' (HTTP {r.status_code}): {r.read()[:300]!r}")
                for line in r.iter_lines():
                    if not line:
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise ProviderError(f"Ollama could not download '{tag}': {ev['error']}", hint="Check the model name at https://ollama.com/library")
                    total, done = ev.get("total"), ev.get("completed")
                    if total and done is not None:
                        pct = int(done * 100 / total)
                        if pct >= last + 10:
                            log(f"  {tag}: {pct}% of {total / 1e9:.1f} GB")
                            last = pct
                    elif ev.get("status") == "success":
                        log(f"  {tag}: ready")
        except httpx.HTTPError as exc:
            raise self._unreachable() from exc

    # ------------------------------------------------------------- generation
    def _context_for(self, system: str, prompt: str, max_tokens: int) -> int:
        cap = int(self.config.get("context_tokens") or 32768)
        need = int((len(system) + len(prompt)) / 3.0) + max_tokens + 512  # ~3 chars/token, conservative for FR/EN
        size = next((b for b in CTX_BUCKETS if b >= need), CTX_BUCKETS[-1])
        size = min(max(size, self._ctx), max(cap, CTX_BUCKETS[0]))  # never shrink: avoids model reloads
        self._ctx = size
        return size

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8000, json_mode: bool = False) -> str:
        ctx = self._context_for(system, prompt, max_tokens)
        body: dict[str, Any] = {
            "model": self.model,
            "stream": True,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "keep_alive": self.config.get("keep_alive", "15m"),
            "options": {
                "num_ctx": ctx,
                "num_predict": int(max_tokens * 1.5) + 256,
                "temperature": float(self.config.get("temperature", 0.5)),
                "top_p": 0.9,
                "repeat_penalty": 1.05,
            },
        }
        if json_mode:
            body["format"] = "json"
        think = self.config.get("think", False)
        if think is not None:
            body["think"] = bool(think)
        text = self._chat(body)
        return _strip_think(text)

    def _chat(self, body: dict[str, Any], _retry: bool = True) -> str:
        timeout = httpx.Timeout(30, read=float(self.config.get("timeout_seconds") or 900))
        parts: list[str] = []
        try:
            with self._client(timeout) as c, c.stream("POST", f"{self.host}/api/chat", json=body) as r:
                if r.status_code != 200:
                    detail = r.read().decode("utf-8", "replace")[:400]
                    if _retry and "think" in body and "think" in detail.lower():
                        body = {k: v for k, v in body.items() if k != "think"}  # model without a thinking switch
                        return self._chat(body, _retry=False)
                    if r.status_code == 404 or "not found" in detail.lower():
                        raise ConfigError(f"The local model '{body['model']}' is not downloaded.", hint=f"Run `python -m gta_factory setup` (or `ollama pull {body['model']}`).")
                    raise ProviderError(f"Ollama error {r.status_code}: {detail}", hint="If the machine ran out of memory, set providers.llm.ollama.model to a smaller model (e.g. qwen3:8b).")
                for line in r.iter_lines():
                    if not line:
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise ProviderError(f"Ollama error: {ev['error']}")
                    parts.append((ev.get("message") or {}).get("content", ""))
                    if ev.get("done"):
                        if ev.get("done_reason") == "length":
                            raise ProviderError("The local model stopped at its output limit.", hint="Raise providers.llm.ollama.context_tokens, or re-run the step.")
                        break
        except httpx.TimeoutException as exc:
            raise ProviderError("The local model took too long to answer.", hint="Raise providers.llm.ollama.timeout_seconds, or pick a smaller model.") from exc
        except httpx.HTTPError as exc:
            raise self._unreachable() from exc
        return "".join(parts)


def _strip_think(text: str) -> str:
    """Drop a leaked <think>...</think> block (older Ollama versions inline the reasoning)."""
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def machine_summary() -> str:
    ram = total_ram_gb()
    chip = platform.machine()
    tag, size = recommended_model(ram)
    return f"{ram:.0f} GB RAM ({chip}) -> {tag} ({size})"
