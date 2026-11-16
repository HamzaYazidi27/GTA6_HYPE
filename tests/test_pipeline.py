"""End-to-end pipeline runs with the offline fakes (scripted LLM, synthetic voice).

The fast test stops after the timeline step. The full test also renders with
Remotion, mixes, builds thumbnails, metadata, QC and the package; it needs Node
and a headless browser, so it only runs with GTA_FACTORY_RENDER_TESTS=1.
"""
from __future__ import annotations

import json

import pytest

from conftest import RENDER, job_options
from gta_factory.app.core.pipeline import create_job, run_job
from gta_factory.app.core.utils import read_json
from gta_factory.app.database.db import get_db


def test_pipeline_until_timeline(project):
    s = project["settings"]
    job = create_job(s, [project["url"]], fmt="short", options=job_options(project))
    assert run_job(s, job, until="timeline") == "paused"
    db = get_db(s.db_path)
    steps = {st["name"]: st for st in db.get_steps(job)}
    assert all(steps[n]["status"] == "done" for n in ("extract", "analyze", "corroborate", "fact_check", "select", "script", "scenes", "rights", "voice", "timeline"))
    root = s.jobs_dir / job
    fc = read_json(root / "research" / "fact_check.json")
    assert {c["id"]: c["classification"] for c in fc} == {"C1": "confirmed", "C2": "confirmed", "C3": "confirmed", "C4": "speculation"}
    script = read_json(root / "script" / "script.json")
    assert [x["id"] for x in script["sections"]] == ["hook", "story", "payoff", "cta"]
    assert script["quality"]["remaining_lint_issues"] == [], script["quality"]["remaining_lint_issues"]
    props = read_json(root / "render" / "props.json")
    assert props["format"] == "short" and props["width"] == 1080 and props["height"] == 1920
    assert props["scenes"][0]["startFrame"] == 0
    for a, b in zip(props["scenes"], props["scenes"][1:]):
        assert b["startFrame"] == a["startFrame"] + a["durationFrames"]  # gapless, frame exact
    last = props["scenes"][-1]
    assert last["startFrame"] + last["durationFrames"] == props["totalFrames"]
    assert all(sc["phrases"] for sc in props["scenes"])
    pc_card = next(sc for sc in props["scenes"] if sc["template"] == "claim" and "PC" in sc["data"]["claim"])
    assert pc_card["data"]["status"] == "speculation"  # the planner said "confirmed": the fact-check wins
    timeline_scene = next(sc for sc in props["scenes"] if sc["template"] == "timeline")
    assert all(f >= 0 for f in timeline_scene["cueFrames"])
    srt = (root / "subtitles" / "captions.srt").read_text()
    assert srt.startswith("1\n00:00:") and "December 4" in srt
    # the voice cache makes a redo of the voice step free
    assert run_job(s, job, from_step="voice", until="voice") == "paused"
    assert db.get_steps(job)[8]["detail"]["reused"] == len(props["scenes"])


def test_duplicate_story_is_stopped_then_forced(project):
    s = project["settings"]
    opts = job_options(project)
    first = create_job(s, [project["url"]], fmt="short", options=opts)
    assert run_job(s, first, until="select") == "paused"
    # simulate that the first video was published: its claims enter the story memory
    db = get_db(s.db_path)
    fc = read_json(s.jobs_dir / first / "research" / "fact_check.json")
    story = read_json(s.jobs_dir / first / "story" / "story.json")
    db.add_story(job_id=first, headline="Rockstar sets the pre-order date and plans a third trailer", angle="date", fmt="short", fingerprint=story["fingerprint"], claims=[c["claim"] for c in fc], source_urls=[project["url"]])
    second = create_job(s, [project["url"]], fmt="short", options=opts)
    assert run_job(s, second) == "stopped"
    job = db.get_job(second)
    assert "skip_duplicate" in json.dumps(job["summary"]) and job["current_step"] == "select"
    # --force: the decision is re-run and the job continues
    db.update_job(second, options={**opts, "force": True})
    assert run_job(s, second, until="script") == "paused"


def test_failed_step_reports_a_readable_error(project):
    s = project["settings"]
    job = create_job(s, ["https://www.rockstargames.com/newswire/missing"], fmt="short", options={**job_options(project), "text_files": {"https://www.rockstargames.com/newswire/missing": str(project["root"] / "nope.txt")}})
    assert run_job(s, job) == "failed"
    j = get_db(s.db_path).get_job(job)
    assert j["error"].startswith("[extract]") and "could be read" in j["error"]


@pytest.mark.render
@pytest.mark.skipif(not RENDER, reason="set GTA_FACTORY_RENDER_TESTS=1 to run the Remotion render")
def test_full_pipeline_with_render(project):
    s = project["settings"]
    job = create_job(s, [project["url"]], fmt="short", options=job_options(project))
    status = run_job(s, job)
    root = s.jobs_dir / job
    qc = read_json(root / "qc" / "qc_report.json")
    failed = [c for c in qc["checks"] if not c["passed"]]
    assert status in ("completed", "needs_review"), get_db(s.db_path).get_job(job)["error"]
    assert not [c for c in failed if c["severity"] == "critical"], failed
    out = s.outputs_dir / job
    assert list(out.glob("*.mp4")) and (out / "REVIEW.md").exists() and (out / "captions.srt").exists()
    assert len(list((out / "thumbnails").glob("*.png"))) == 3
    meta = read_json(out / "metadata.json")
    assert meta["title"] == "GTA 6 pre-orders open December 4"
    assert get_db(s.db_path).list_stories()  # story memory updated
