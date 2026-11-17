from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from fakes import CLAIMS
from gta_factory.modules.asset_management import audio_library as al
from gta_factory.modules.asset_management.rights import review_assets
from gta_factory.modules.thumbnail_generation.concepts import normalize_concept
from gta_factory.modules.video_editing.mix import limit, voice_activity
from gta_factory.modules.video_editing.timeline import cue_frames, emphasis_range, find_phrase
from gta_factory.modules.youtube_metadata.metadata import _title_ok, build_description, youtube_chapters

FACT_CHECK = [{**c, "classification": "confirmed" if c["basis"] == "official" else "speculation", "confidence": "high", "original_publisher": "Rockstar Games", "original_source": "https://x"} for c in CLAIMS]


# ----------------------------------------------------------------- audio
@pytest.mark.parametrize("name", al.SFX_NAMES)
def test_sfx_are_stereo_and_below_full_scale(name):
    x = al.make_sfx(name)
    assert x.ndim == 2 and x.shape[1] == 2 and len(x) > 1000
    assert np.max(np.abs(x)) <= 0.72  # normalised to -3 dBFS


def test_bed_is_a_seamless_loop():
    x = al.make_bed("pulse", bars=2)
    assert x.ndim == 2 and x.shape[1] == 2
    beat = 60 / 108
    assert abs(len(x) / al.SR - 2 * 4 * beat) < 0.01
    # the loop point should not click: last and first samples are close
    assert np.max(np.abs(x[-1] - x[0])) < 0.2


def test_limiter_respects_the_ceiling():
    rng = np.random.default_rng(1)
    x = rng.standard_normal((48000, 2)).astype(np.float32) * 0.5
    x[1000:1010] = 1.8  # spikes
    y, red = limit(x, -1.6)
    assert np.max(np.abs(y)) <= 10 ** (-1.6 / 20) + 1e-6
    assert red > 6


def test_voice_activity_tracks_speech():
    v = np.zeros(48000 * 3, dtype=np.float32)
    v[48000:96000] = 0.2 * np.sin(np.arange(48000) / 10)
    act = voice_activity(v)
    assert act[70000] > 0.9 and act[5000] < 0.05


# ----------------------------------------------------------------- timeline
WORDS = [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate("Pre-orders open on December 4, and launch is November 19.".split())]


def test_find_phrase_and_cues_are_ordered():
    assert find_phrase(WORDS, "December 4") == 3
    frames = cue_frames({"cues": ["December", "November", "open"]}, WORDS, 30)
    assert frames[0] == 45 and frames[1] == int(round(WORDS[8]["s"] * 30))
    assert frames[2] == -1  # "open" comes before "November": out of order -> unknown


def test_emphasis_range():
    r = emphasis_range("launch is", WORDS, 30)
    assert r == {"from": int(round(3.0 * 30)), "to": int(round(3.9 * 30))}


def test_youtube_chapter_rules():
    ch = [{"start_time": 0.4, "title": "The news"}, {"start_time": 5.0, "title": "too close"}, {"start_time": 40, "title": "Context"}, {"start_time": 120, "title": "Analysis"}, {"start_time": 470, "title": "Wrap-up"}]
    out = youtube_chapters(ch, 475)
    assert out[0]["time"] == "0:00" and [c["title"] for c in out] == ["The news", "Context", "Analysis"]
    assert youtube_chapters(ch[:2], 60) == []


# ----------------------------------------------------------------- rights
def test_rights_gate(project):
    s = project["settings"]
    folder = s.assets_dir / "licensed_visuals"
    from PIL import Image

    for name in ("skyline_good", "skyline_nolicence", "press_kit"):
        Image.new("RGB", (64, 36), "navy").save(folder / f"{name}.jpg")
    (folder / "skyline_good.jpg.license.json").write_text(json.dumps({"license": "CC0", "commercial_use": True, "tags": ["miami", "skyline"], "attribution": ""}))
    (folder / "press_kit.jpg.license.json").write_text(json.dumps({"license": "press", "commercial_use": True, "kind": "official_press"}))
    scenes = [
        {"scene_number": 1, "template": "broll", "narration": "The city at night.", "emphasis": "", "data": {"query": "miami skyline"}},
        {"scene_number": 2, "template": "broll", "narration": "Another shot here.", "emphasis": "", "data": {"asset_id": "skyline_nolicence"}},
        {"scene_number": 3, "template": "broll", "narration": "Press material now.", "emphasis": "", "data": {"asset_id": "press_kit"}},
        {"scene_number": 4, "template": "broll", "narration": "Leaked footage clip.", "emphasis": "", "data": {"query": "gta 6 leaked footage"}},
    ]
    review = review_assets(s, scenes, media_dir=project["root"] / "media", tts_provider="kokoro", vertical=False)
    assert scenes[0]["template"] == "broll" and scenes[0]["media_file"].endswith("skyline_good.jpg")
    assert [sc["template"] for sc in scenes[1:]] == ["kinetic", "kinetic", "kinetic"]  # no sidecar / strict mode / blocked keyword
    assert review["verdict"] == "approved"
    assert {a["asset_id"] for a in review["rejected"]} == {"op-skyline_nolicence", "op-press_kit"}


# ----------------------------------------------------------------- thumbnails and metadata
def test_thumbnail_rules_keep_text_honest():
    c = normalize_concept({"concept": "number", "lines": ["CONFIRMED", "PC PORT"], "bigText": "$500M", "lead_claim_id": "C4"}, FACT_CHECK, "BRAND", ["FALLBACK"])
    p = c["props"]
    assert p["status"] == "speculation"
    assert p["concept"] == "stamp" and p["bigText"] == ""  # $500M is not in the claims
    assert "CONFIRMED" not in " ".join(p["lines"])
    ok = normalize_concept({"concept": "number", "bigText": "NOV 19", "lines": ["LAUNCH"], "lead_claim_id": "C2", "motif": "calendar", "calendar": {"month": "NOV", "day": "19"}}, FACT_CHECK, "BRAND", ["X"])["props"]
    assert ok["bigText"] == "NOV 19" and ok["calendar"] == {"month": "NOV", "day": "19"}
    leak = normalize_concept({"lines": ["LEAKED", "MAP"]}, FACT_CHECK, "BRAND", ["SAFE"])["props"]
    assert leak["lines"] == ["SAFE"]


def test_title_and_description_rules():
    assert _title_ok("GTA 6 pre-orders open December 4", FACT_CHECK) is None
    assert _title_ok("LEAKED: GTA 6 map", FACT_CHECK)
    assert _title_ok("Officially confirmed PC port", [c for c in FACT_CHECK if c["classification"] != "confirmed"])
    d = build_description(intro="Intro.", fact_check=FACT_CHECK, chapters=[{"time": "0:00", "title": "A"}], sources=[{"publisher": "Rockstar", "title": "T", "url": "https://r"}], credits=[], voice_label="synthetic voice", hashtags=["GTA6"], channel={"name": "X"})
    assert "[CONFIRMED]" in d and "SOURCES" in d and "0:00 A" in d and "<" not in d and d.endswith("#GTA6")


# ----------------------------------------------------------------- dashboard security
def test_dashboard_guards(project):
    from gta_factory.app.dashboard.server import make_server

    httpd = make_server(project["settings"], "127.0.0.1", 0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        assert json.loads(urllib.request.urlopen(base + "/api/jobs").read())["jobs"] == []
        req = urllib.request.Request(base + "/api/jobs", data=b'{"urls": ["https://example.com/a"]}', method="POST", headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403  # no X-Factory header
        req = urllib.request.Request(base + "/api/jobs", headers={"Host": "evil.example"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403  # DNS rebinding guard
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/files/abc/job/..%2F..%2Fconfig.yaml")
        assert e.value.code in (403, 404)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_renderer_installs_remotion_when_setup_was_skipped(tmp_path, monkeypatch):
    """A skipped `setup` must not fail the render: `npm install` runs once, by itself."""
    import subprocess as sp
    from types import SimpleNamespace

    from gta_factory.modules.video_editing import render

    calls = []

    def fake_run(cmd, cwd=None, **kw):
        calls.append((cmd, cwd))
        (tmp_path / "node_modules" / "@remotion" / "renderer").mkdir(parents=True)
        return sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(render, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(render.subprocess, "run", fake_run)
    logs = []
    render.check_renderer(SimpleNamespace(root=tmp_path), log=logs.append)
    assert calls == [(["npm", "install", "--no-audit", "--no-fund"], tmp_path)] and "Remotion installed" in logs
    render.check_renderer(SimpleNamespace(root=tmp_path), log=logs.append)
    assert len(calls) == 1  # already there: nothing to do
    monkeypatch.setattr(render, "which", lambda b: None)
    with pytest.raises(Exception, match="Node.js is not installed"):
        render.check_renderer(SimpleNamespace(root=tmp_path))
