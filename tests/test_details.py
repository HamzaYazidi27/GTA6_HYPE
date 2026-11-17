""""Details you missed": find details with the vision model, check them, pick, script, storyboard, clips, render.

The vision model is replaced by a scripted one: on every frame it points at a
"sign" (a real detail, confirmed by the close-up check) and at a "ghost" that the
close-up check rejects, so the verification step is exercised too.
"""
from __future__ import annotations

import re
import subprocess

import pytest

from conftest import RENDER, job_options
from gta_factory.app.core.pipeline import create_details_job, run_job
from gta_factory.app.core.utils import read_json
from gta_factory.app.database.db import get_db
from gta_factory.modules.details import find
from gta_factory.modules.details.find import parse_box, pick_details, scan
from gta_factory.modules.footage import autotag
from gta_factory.modules.footage.library import build_index
from test_footage import footage_project  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")


class FakeVision:
    def __init__(self):
        self.calls = 0

    def __call__(self, image: bytes, prompt: str) -> dict:
        assert image[:2] == b"\xff\xd8"
        self.calls += 1
        if "close-up crop" in prompt:
            return {"visible": "ghost" not in prompt, "text": ""}
        m = re.search(r"at (\d+:\d\d)", prompt)
        w, h = (int(x) for x in re.search(r"The picture is (\d+) x (\d+) pixels", prompt).groups())
        tc = m.group(1) if m else "0:00"
        interest = 9 if tc.endswith("5") else 6
        return {"details": [
            {"label": "Neon shop sign", "what": "A small neon sign above a shop door.", "text": f"OPEN {tc}", "box": [int(w * 0.62), int(h * 0.12), int(w * 0.78), int(h * 0.26)], "interest": interest},
            {"label": "Ghost", "what": "A ghost in the window.", "text": "", "box": [10, 10, 60, 60], "interest": 10},
            {"label": "Too big", "what": "The whole sky.", "text": "", "box": [0, 0, w, h], "interest": 10},
        ]}


@pytest.fixture()
def vision(monkeypatch):
    fake = FakeVision()
    monkeypatch.setattr(autotag, "ollama_tagger", lambda settings, model=None, **kw: fake)
    return fake


def test_parse_box_reads_pixels_permille_and_fractions():
    assert parse_box([100, 56, 300, 168], 1008, 560) == [0.0992, 0.1, 0.2976, 0.3]
    assert parse_box([99, 100, 298, 300], 1008, 560) == [0.0982, 0.1786, 0.2956, 0.5357]  # pixels
    assert parse_box([600, 650, 800, 900], 1008, 560) == [0.6, 0.65, 0.8, 0.9]  # 0-1000 (y beyond the picture)
    assert parse_box([0.5, 0.5, 0.6, 0.62], 1008, 560) == [0.5, 0.5, 0.6, 0.62]
    assert parse_box({"x1": 300, "y1": 100, "x2": 100, "y2": 50}, 1008, 560) == [0.0992, 0.0893, 0.2976, 0.1786]  # swapped
    assert parse_box([0, 0, 1008, 560], 1008, 560) is None  # the whole picture is not a detail
    assert parse_box([10, 10, 12, 12], 1008, 560) is None  # too small to show
    assert parse_box(["a", 1, 2, 3], 1008, 560) is None and parse_box([1, 2], 1008, 560) is None


def test_scan_checks_every_detail_and_caches(footage_project, vision):
    s = footage_project["settings"]
    cands = scan(s, videos=["Trailer 1"], log=lambda m: None)
    shots = [sh for sh in build_index(s)["videos"][0]["shots"] if sh["ok"] and sh["e"] - sh["s"] >= 1.5]
    assert len(shots) >= 6 and vision.calls == len(shots)  # one look per shot
    assert len(cands) == 2 * len(shots)  # the sign and the ghost; the whole sky is not a detail
    signs = [c for c in cands if c["label"] == "Neon shop sign"]
    assert len(signs) == len(shots) and all(c["text"].startswith("OPEN") and 0.6 < c["box"][0] < c["box"][2] < 0.8 for c in signs)
    calls = vision.calls
    assert scan(s, videos=["Trailer 1"], log=lambda m: None) == cands and vision.calls == calls  # cached
    with pytest.raises(Exception, match="No video"):
        scan(s, videos=["Trailer 9"], log=lambda m: None)

    # the ghosts score higher but fail the close-up check: never picked, and remembered as failed
    picked = pick_details(s, cands, 4, verify=find.make_verifier(s, log=lambda m: None))
    assert [d["id"] for d in picked] == ["D1", "D2", "D3", "D4"] and all(d["label"] == "Neon shop sign" for d in picked)
    assert vision.calls - calls < 2 * len(shots)  # only the details that could be used were checked
    cands = find.candidates(s, build_index(s), build_index(s)["videos"])
    assert sum(1 for c in cands if c["label"] == "Ghost") < len(shots)
    assert find.score(picked[-1]) == max(find.score(d) for d in picked)  # the strongest detail comes last
    assert [d["t"] for d in picked[:-1]] == sorted(d["t"] for d in picked[:-1])  # the rest in footage order
    assert all(abs(a["t"] - b["t"]) >= 4.0 for i, a in enumerate(picked) for b in picked[i + 1 :])
    # once shown, a detail is not picked again while others are left
    find.remember_shown(s, picked, "job-1")
    again = pick_details(s, cands, 2, verify=find.make_verifier(s, log=lambda m: None))
    assert not {d["key"] for d in again} & {d["key"] for d in picked}


def test_details_job_until_timeline(footage_project, vision):
    s = footage_project["settings"]
    job = create_details_job(s, videos=["Trailer 1"], fmt="short", count=3, options=job_options(footage_project))
    assert [st["name"] for st in get_db(s.db_path).get_steps(job)][:4] == ["details", "details_script", "details_scenes", "rights"]
    assert run_job(s, job, until="timeline") == "paused", get_db(s.db_path).get_job(job)["error"]
    root = s.jobs_dir / job
    script = read_json(root / "script" / "script.json")
    assert script["kind"] == "details" and script["title"] == "3 details you missed in Trailer 1"
    assert [x["id"] for x in script["sections"]] == ["hook", "D1", "D2", "D3", "cta"]
    scenes = read_json(root / "scenes" / "scene_breakdown.json")
    assert [sc["template"] for sc in scenes] == ["kinetic", "detail", "detail", "detail", "outro"]
    rights = read_json(root / "assets" / "rights_review.json")
    assert rights["footage"]["mode"] == "details" and rights["footage"]["fixed_scenes"] == 3
    assert all(sc.get("footage") for sc in scenes)  # hook and outro get footage too
    props = read_json(root / "render" / "props.json")
    for sc in (x for x in props["scenes"] if x["template"] == "detail"):
        d = sc["data"]
        assert d["label"].startswith("SMALL SIGN") and d["text"].startswith("OPEN") and d["total"] == 3
        assert 0 <= d["box"][0] < d["box"][2] <= 1 and 1.4 <= d["zoom"] <= 3.2 and 0 <= d["freeze"] <= 1.2
        assert "TRAILER 1 ·" in d["source"]
        clip = s.root / "templates" / ".bundle" / "public" / sc["footage"]["src"]
        out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=width,height,nb_read_frames", "-of", "csv=p=0", str(clip)], capture_output=True, text=True).stdout.strip().split(",")
        assert (int(out[0]), int(out[1])) == (1080, 1920) and int(out[2]) >= sc["durationFrames"]
        # 9:16 crops around the detail: it stays in the picture
        assert d["box"][2] - d["box"][0] > 0.15
    assert "details" in (s.jobs_dir / job / "research" / "details.json").read_text()


@pytest.mark.render
@pytest.mark.skipif(not RENDER, reason="set GTA_FACTORY_RENDER_TESTS=1 to run the Remotion render")
def test_full_details_video(footage_project, vision):
    s = footage_project["settings"]
    job = create_details_job(s, videos=["Trailer 1"], fmt="short", count=3, options=job_options(footage_project))
    status = run_job(s, job)
    assert status in ("completed", "needs_review"), get_db(s.db_path).get_job(job)["error"]
    out = s.outputs_dir / job
    assert list(out.glob("*.mp4"))
    review = (out / "REVIEW.md").read_text()
    assert "Vérifier chaque détail" in review and "fact_check_report" not in review
    concepts = read_json(s.jobs_dir / job / "thumbnails" / "concepts.json")
    assert all(c["props"].get("ring") and c["props"].get("photo") for c in concepts)
    meta = read_json(out / "metadata.json")
    assert "THE DETAILS" in meta["description"]
    shown = read_json(find.shown_path(s))
    assert sum(1 for jobs in shown.values() if job in jobs) == 3
