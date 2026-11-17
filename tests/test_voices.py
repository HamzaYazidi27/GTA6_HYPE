"""Narration voices: whole-scene reading for premium voices, natural pauses, samples to listen and choose."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from fakes import FakeTTS
from gta_factory.app.core.errors import ConfigError
from gta_factory.modules.voice_generation.engine import WORK_SR, synthesize_scene, whole_scene
from gta_factory.modules.voice_generation.samples import SAMPLE_TEXT, catalog, check_choice, sample

TEXT = "In Red Dead 2, Arthur carried two long guns. In GTA 6, the rest goes in the trunk! What changes for you?"


class CountingTTS(FakeTTS):
    name = "openai"  # a premium provider: reads the whole scene at once

    def __init__(self):
        super().__init__({}, None)
        self.calls: list[str] = []

    def synthesize(self, text):
        self.calls.append(text)
        return super().synthesize(text)


def test_premium_voices_read_the_whole_scene(project):
    s = project["settings"]
    tts = CountingTTS()
    assert whole_scene(s, tts) and not whole_scene(s, FakeTTS({}, None))
    out = synthesize_scene(tts, TEXT, [], whole=True)
    assert tts.calls == [TEXT]  # one reading, so the melody flows across the sentences
    spans = out["sentences"]
    assert [x["text"][:8] for x in spans] == ["In Red D", "In GTA 6", "What cha"]
    assert spans[0]["start"] == 0 and abs(spans[-1]["end"] - out["duration"]) < 0.01
    assert all(a["end"] == b["start"] for a, b in zip(spans, spans[1:]))


def test_local_voice_pauses_follow_the_punctuation(project):
    tts = CountingTTS()
    out = synthesize_scene(tts, TEXT, [], sentence_pause=0.24)
    assert len(tts.calls) == 3
    s = out["sentences"]
    gap1, gap2 = s[1]["start"] - s[0]["end"], s[2]["start"] - s[1]["end"]
    assert abs(gap1 - 0.24) < 0.002 and abs(gap2 - 0.30) < 0.002  # a longer breath after "!"
    assert isinstance(out["samples"], np.ndarray) and len(out["samples"]) == pytest.approx(out["duration"] * WORK_SR, abs=2)


def test_voice_samples_and_catalog(project, monkeypatch):
    s = project["settings"]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    cat = catalog(s)
    names = [p["name"] for p in cat["providers"]]
    assert names == ["kokoro", "openai", "elevenlabs"] and cat["default"]["tts"] == "fake"
    kokoro = cat["providers"][0]
    assert any(v["id"] == "af_heart" for v in kokoro["voices"]) and not kokoro["ready"]  # no model files in the test project
    assert not cat["providers"][1]["ready"] and "OPENAI_API_KEY" in cat["providers"][1]["note"]
    out = s.jobs_dir / ".cache" / "voice_samples"
    wav = sample(s, "fake", "", out_dir=out)
    assert wav.exists() and wav.stat().st_size > 10_000 and sample(s, "fake", "", out_dir=out) == wav  # cached
    with pytest.raises(ConfigError):
        check_choice("fake", "../../etc/passwd")
    with pytest.raises(ConfigError):
        check_choice("espeak", "x")
    assert SAMPLE_TEXT.count(".") >= 2


def test_dashboard_voice_picker(project, monkeypatch):
    from gta_factory.app.dashboard import server

    started = []
    monkeypatch.setattr(server, "spawn_runner", lambda settings, job_id, **kw: started.append(job_id))
    s = project["settings"]
    httpd = server.make_server(s, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"X-Factory": "1", "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read() or b"{}")

    try:
        assert [p["name"] for p in call("GET", "/api/voices")["providers"]] == ["kokoro", "openai", "elevenlabs"]
        url = call("POST", "/api/voices/sample", {"tts": "fake", "voice": ""})["url"]
        audio = urllib.request.urlopen(base + url).read()
        assert audio[:4] == b"RIFF"
        made = call("POST", "/api/jobs", {"urls": [project["url"]], "format": "short", "options": {"tts": "fake", "voice": "narrator_1"}})
        from gta_factory.app.database.db import get_db

        opts = get_db(s.db_path).get_job(made["id"])["options"]
        assert opts["tts"] == "fake" and opts["voice"] == "narrator_1" and started == [made["id"]]
        with pytest.raises(urllib.error.HTTPError) as e:
            call("POST", "/api/voices/sample", {"tts": "fake", "voice": "a/b"})
        assert e.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/voice-samples/..%2Fconfig.wav")
        assert e.value.code == 404
    finally:
        httpd.shutdown()
