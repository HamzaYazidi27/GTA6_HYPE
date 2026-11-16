"""The free local LLM adapter (Ollama), against a fake Ollama server speaking the real HTTP API."""
from __future__ import annotations

import json

import httpx
import pytest

from gta_factory.app.core.errors import ConfigError
from gta_factory.providers.llm import local
from gta_factory.providers.llm.local import OllamaProvider, recommended_model


class FakeOllama:
    def __init__(self, models=("qwen3:14b",), replies=None, reject_think=False):
        self.models = list(models)
        self.replies = list(replies or ['{"ok": true}'])
        self.reject_think = reject_think
        self.chats: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.0"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in self.models]})
        if path == "/api/pull":
            body = json.loads(request.content)
            self.models.append(body["model"])
            lines = [{"status": "pulling", "total": 100, "completed": 50}, {"status": "pulling", "total": 100, "completed": 100}, {"status": "success"}]
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
        if path == "/api/chat":
            body = json.loads(request.content)
            if self.reject_think and "think" in body:
                return httpx.Response(400, json={"error": f'"{body["model"]}" does not support thinking'})
            if body["model"] not in self.models:
                return httpx.Response(404, json={"error": f"model '{body['model']}' not found"})
            self.chats.append(body)
            text = self.replies.pop(0) if self.replies else '{"ok": true}'
            half = len(text) // 2
            events = [{"message": {"role": "assistant", "content": text[:half]}, "done": False}, {"message": {"role": "assistant", "content": text[half:]}, "done": True, "done_reason": "stop"}]
            return httpx.Response(200, content="\n".join(json.dumps(e) for e in events).encode())
        return httpx.Response(404)


def provider(fake: FakeOllama, **cfg) -> OllamaProvider:
    return OllamaProvider({"model": "qwen3:14b", **cfg}, transport=httpx.MockTransport(fake))


def test_json_call_uses_json_mode_and_streams():
    fake = FakeOllama(replies=['{"titles": ["A", "B"], "intro": "x", "tags": []}'])
    p = provider(fake)
    p.check()
    data = p.complete_json("sys", "prompt", label="meta", required_keys=("titles", "intro", "tags"))
    assert data["titles"] == ["A", "B"]
    body = fake.chats[0]
    assert body["format"] == "json" and body["stream"] is True and body["think"] is False
    assert body["options"]["num_ctx"] in local.CTX_BUCKETS
    assert p.usage.calls == 1


def test_text_call_has_no_json_mode_and_strips_leaked_thinking():
    fake = FakeOllama(replies=["<think>plan</think>\nHello there."])
    out = provider(fake).complete_text("sys", "prompt")
    assert out == "Hello there." and "format" not in fake.chats[0]


def test_wrapped_list_is_unwrapped_and_bad_json_is_repaired():
    fake = FakeOllama(replies=['{"items": [1, 2]}', "not json", '{"a": 1}'])
    p = provider(fake)
    assert p.complete_json("s", "p", validate=lambda d: None if isinstance(d, list) else "need a list") == [1, 2]
    assert p.complete_json("s", "p", required_keys=("a",)) == {"a": 1}
    assert "rejected" in fake.chats[-1]["messages"][1]["content"]


def test_model_without_thinking_switch_is_retried_without_it():
    fake = FakeOllama(replies=["fine"], reject_think=True)
    assert provider(fake).complete_text("s", "p") == "fine"
    assert "think" not in fake.chats[0]


def test_missing_model_and_dead_server_give_actionable_errors():
    with pytest.raises(ConfigError, match="not downloaded"):
        provider(FakeOllama(models=["llama3.2:3b"])).check()

    def down(_r):
        raise httpx.ConnectError("refused")

    dead = OllamaProvider({"model": "qwen3:14b"}, transport=httpx.MockTransport(down))
    with pytest.raises(ConfigError, match="not running"):
        dead.check()


def test_auto_model_follows_ram_and_falls_back_to_installed(monkeypatch):
    assert recommended_model(64)[0] == "qwen3:32b"
    assert recommended_model(16)[0] == "qwen3:14b"
    assert recommended_model(8)[0] == "qwen3:8b"
    assert recommended_model(4)[0] == "qwen3:4b"
    monkeypatch.setattr(local, "total_ram_gb", lambda: 36.0)
    assert provider(FakeOllama(models=["qwen3:30b"]), model="auto").model == "qwen3:30b"
    # best model not downloaded yet but a smaller one is: use it rather than fail
    assert provider(FakeOllama(models=["qwen3:8b"]), model="auto").model == "qwen3:8b"


def test_pull_downloads_the_model():
    fake = FakeOllama(models=[])
    p = provider(fake)
    lines: list[str] = []
    p.pull("qwen3:14b", log=lines.append)
    assert "qwen3:14b" in fake.models and lines[-1].endswith("ready")
    p.check()


def test_context_window_never_shrinks_between_calls():
    p = provider(FakeOllama())
    big = p._context_for("s", "x" * 60000, 4000)
    small = p._context_for("s", "short", 500)
    assert big >= 16384 and small == big


def test_ollama_is_the_default_provider():
    from gta_factory.app.config.settings import get_settings
    from gta_factory.providers.llm import get_llm

    s = get_settings()  # the real project config.yaml
    assert s.get("providers.llm.default") == "ollama"
    assert get_llm(s).name == "ollama"
