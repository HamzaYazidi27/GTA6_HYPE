"""Script to Short never leaves the screen empty, and does not show the same clips Short after Short."""
from __future__ import annotations

from shutil import which

import numpy as np
import pytest

from gta_factory.modules.footage.library import build_index
from gta_factory.modules.script_short import match, produce

pytestmark = pytest.mark.skipif(not which("ffmpeg"), reason="needs ffmpeg")


@pytest.fixture()
def library(project):
    from test_footage import make_photo, make_trailer

    root = project["root"] / "assets" / "gta6_footage"
    make_trailer(root / "videos" / "Grand Theft Auto VI Trailer 1 [QdBZY2fkU-0].mp4")
    make_photo(root / "photos" / "vice_city_night.jpg", "0x8E5BD9")
    build_index(project["settings"])
    return project


class FakeClip:
    """Texts that say "kayak" point one way, everything else elsewhere: the frames are placed between."""

    def embed_text(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), 512), np.float32)
        for i, t in enumerate(texts):
            out[i, 0 if "kayak" in t.lower() else 1 + sum(map(ord, t)) % 400] = 1.0
        return out


def fake_index(sims: dict[int, float]):
    """index_frames: shot n of the library is ``sims[n]`` like a kayak (0 when not listed), pictures 0.2."""

    def index_frames(settings, items, log=None, model=None):
        rows, vecs = [], []
        for n, it in enumerate(items["shots"]):
            for t in np.arange(it["s"] + 0.25, it["e"], 0.5):
                v = np.zeros(512, np.float32)
                v[0] = sims.get(n, 0.0)
                v[450] = 1.0
                rows.append({"key": it["key"], "kind": "shot", "t": float(t)})
                vecs.append(v / np.linalg.norm(v))
        for it in items["photos"]:
            v = np.zeros(512, np.float32)
            v[0], v[450] = 0.2, 1.0
            rows.append({"key": it["key"], "kind": "photo", "t": None})
            vecs.append(v / np.linalg.norm(v))
        return rows, np.array(vecs, np.float32)

    return index_frames


BEAT = {"text": "Four: paddle a kayak through the swamp.", "concept": "a person paddling a kayak", "prompts": ["a person paddling a kayak"], "specific": True, "game": "gta6"}


def test_the_closest_footage_fills_a_beat_nothing_verified_shows(library, monkeypatch) -> None:
    from gta_factory.modules.footage import clip as clipmod

    s = library["settings"]
    monkeypatch.setattr(clipmod, "index_frames", fake_index({3: 0.9, 6: 0.7}))
    monkeypatch.setattr(match, "NON_GAMEPLAY", [])
    matches = [{"beat": 0, "text": BEAT["text"], "concept": BEAT["concept"], "specific": True, "segments": [], "status": "missing", "reason": "no frame of the library shows it", "search_url": "https://www.youtube.com/results?search_query=x"}]
    got = match.fill_gaps(s, [BEAT], [1.5], matches, job="j1", clip=FakeClip(), log=lambda m: None)
    m = matches[0]
    assert got["related"] == [0] and m["status"] == "related" and m["specific"]
    assert m["reason"].startswith("no frame") and m["search_url"]  # why nothing verified it stays in the report
    shots = match.Library(s, "gta6", job="j1", clip=None, log=lambda x: None).items["shots"]
    assert m["segments"][0]["key"] == shots[3]["key"]  # the frames most like a kayak


def test_recent_shorts_push_their_clips_back(library, monkeypatch) -> None:
    from gta_factory.modules.footage import clip as clipmod

    s = library["settings"]
    monkeypatch.setattr(clipmod, "index_frames", fake_index({3: 0.9, 6: 0.85}))
    monkeypatch.setattr(match, "NON_GAMEPLAY", [])
    shots = match.Library(s, "gta6", job="j1", clip=None, log=lambda x: None).items["shots"]
    fresh = match.match_beats(s, [BEAT], [1.5], job="j1", clip=FakeClip(), relaxed=True, log=lambda m: None)
    assert fresh[0]["segments"][0]["key"] == shots[3]["key"]
    worn = match.match_beats(s, [BEAT], [1.5], job="j1", clip=FakeClip(), relaxed=True, history={shots[3]["key"]: 3}, log=lambda m: None)
    assert worn[0]["segments"][0]["key"] == shots[6]["key"]


def test_without_a_frame_index_the_least_shown_gameplay_fills_in(library) -> None:
    s = library["settings"]
    beats = [BEAT, {**BEAT, "text": "Three: ride an airboat.", "concept": "an airboat"}]
    matches = [{"beat": k, "text": b["text"], "specific": True, "segments": [], "status": "missing", "reason": "no CLIP"} for k, b in enumerate(beats)]
    shots = match.Library(s, "gta6", job="j1", clip=None, log=lambda x: None).items["shots"]
    history = {shots[0]["key"]: 2}
    got = match.fill_gaps(s, beats, [2.0, 2.0], matches, job="j1", clip=None, history=history, log=lambda m: None)
    assert got["filler"] == [0, 1] and all(m["status"] == "filler" and m["segments"] for m in matches)
    picked = [sg["key"] for m in matches for sg in m["segments"]]
    assert shots[0]["key"] not in picked  # shown by recent Shorts: picked last
    assert len(set(picked)) == len(picked)  # two beats, two different shots
    assert all(sum(sg["end"] - sg["start"] for sg in m["segments"]) == pytest.approx(2.0) for m in matches)


def test_usage_history_counts_other_shorts_only(project) -> None:
    s = project["settings"]
    seg = lambda k: {"segments": [{"key": k}]}  # noqa: E731
    produce.record_usage(s, "a", [seg("t1@3.5"), seg("t1@6.5")])
    produce.record_usage(s, "b", [seg("t1@3.5")])
    assert produce.load_usage(s) == {"t1@3.5": 2, "t1@6.5": 1}
    assert produce.load_usage(s, exclude_job="b") == {"t1@3.5": 1, "t1@6.5": 1}
    produce.record_usage(s, "b", [seg("t1@9.5")])  # a resumed job replaces its own row
    assert produce.load_usage(s) == {"t1@3.5": 1, "t1@6.5": 1, "t1@9.5": 1}


def test_qc_warns_but_does_not_fail_on_closest_footage(project, tmp_path) -> None:
    from types import SimpleNamespace

    from gta_factory.app.core.utils import write_json
    from gta_factory.modules.script_short import qc

    seg = {"kind": "shot", "key": "v.mp4@1.0", "file": "videos/v.mp4", "path": "/nope/v.mp4", "game": "gta6", "start": 1.0, "end": 2.0, "rights": "official"}
    f = SimpleNamespace(**{k: tmp_path / f"{k}.json" for k in ("plan", "beats", "matches", "edl_json", "claims", "style")})
    write_json(f.plan, {"words": [], "beats": [BEAT]})
    write_json(f.beats, {"beats": [{"beat": 0, "text": BEAT["text"], "start": 0.0, "end": 1.0, "speech_start": 0.05, "speech_end": 0.9}]})
    write_json(f.matches, {"matches": [{"beat": 0, "text": BEAT["text"], "concept": BEAT["concept"], "game": "gta6", "specific": True, "status": "related", "confidence": 0.4, "reason": "no frame shows it", "segments": [seg]}]})
    write_json(f.edl_json, {"events": []})
    out = qc.script_checks(project["settings"], job_root=tmp_path, files=f, video=tmp_path / "none.mp4", master={"total_seconds": 1.0})
    by = {c["id"]: c for c in out["checks"]}
    assert by["footage.coverage"]["passed"] and by["footage.verified"]["passed"]
    assert not by["footage.closest"]["passed"] and by["footage.closest"]["severity"] == "warning" and by["footage.closest"]["beats"] == [1]
    assert out["fully_verified"] is False
    assert out["sync"]["beats"][0]["reason"] == "no frame shows it"
