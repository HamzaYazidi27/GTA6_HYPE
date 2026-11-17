"""Real GTA 6 footage: library index, 80 % selection, per-scene clips and render props.

A synthetic "trailer" stands in for the official one: bright shots separated by
hard cuts, plus a black title card that must be skipped.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import RENDER, job_options
from gta_factory.app.core.pipeline import create_job, run_job
from gta_factory.app.core.utils import read_json, which
from gta_factory.app.database.db import get_db
from gta_factory.modules.footage.library import build_index, library_summary, name_tags
from gta_factory.modules.footage.select import _pick_scenes, assign_footage

pytestmark = pytest.mark.skipif(not which("ffmpeg"), reason="needs ffmpeg")

SOURCES = ["testsrc2", "mandelbrot", "smptebars", "rgbtestsrc", "testsrc", "cellauto", "smptehdbars", "life", "pal75bars", "yuvtestsrc"]


def make_trailer(path: Path, shots: int = 10, shot_seconds: float = 3.0) -> Path:
    """Distinct bright shots with hard cuts, a black 'title card' in the middle, a logo head and an end card."""
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = ["black:3.5"] + [f"{SOURCES[i]}:{shot_seconds}" for i in range(shots // 2)] + ["black:2.0"] + [f"{SOURCES[i]}:{shot_seconds}" for i in range(shots // 2, shots)] + ["black:10.5"]
    inputs = []
    for item in seq:
        src, dur = item.split(":")
        spec = f"color=c=black:s=640x360:r=30:d={dur}" if src == "black" else f"{src}=s=640x360:r=30,trim=duration={dur}"
        inputs += ["-f", "lavfi", "-i", spec]
    chains = "".join(f"[{i}:v]scale=640:360,format=yuv420p,setsar=1,fps=30[v{i}];" for i in range(len(seq)))
    graph = chains + "".join(f"[v{i}]" for i in range(len(seq))) + f"concat=n={len(seq)}:v=1:a=0[out]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[out]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", str(path)], check=True)
    return path


def make_photo(path: Path, color: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=1280x720", "-frames:v", "1", str(path)], check=True)
    return path


@pytest.fixture()
def footage_project(project):
    root = project["root"] / "assets" / "gta6_footage"
    make_trailer(root / "videos" / "Grand Theft Auto VI Trailer 1 [QdBZY2fkU-0].mp4")
    make_photo(root / "photos" / "lucia_beach_01.jpg", "0xF2C14E")
    make_photo(root / "photos" / "vice_city_night.jpg", "0x8E5BD9")
    make_photo(root / "photos" / "leaked_build.jpg", "0x333333")  # must be refused
    return project


def test_index_detects_shots_and_skips_dark_and_blocked(footage_project):
    s = footage_project["settings"]
    index = build_index(s)
    summ = library_summary(index)
    assert summ["videos"] == 1 and summ["photos"] == 2
    assert any("leak" in sk["reason"] for sk in summ["skipped"])
    shots = index["videos"][0]["shots"]
    ok = [x for x in shots if x["ok"]]
    assert 6 <= len(ok) <= 10, shots
    assert all(x["luma"] >= 28 for x in ok)
    assert all(x["s"] >= 3.0 for x in ok)  # logo head skipped
    assert all(x["e"] <= index["videos"][0]["duration"] - 10 for x in ok)  # end card skipped
    # cached: a second scan does not re-run detection and returns the same index
    assert build_index(s) == index
    assert name_tags(Path("Lucia_beach-01 [QdBZY2fkU-0].jpg")) == ["lucia", "beach"]


def test_pick_scenes_targets_the_ratio():
    scenes = [{"scene_number": i + 1, "template": t, "narration": " ".join(["word"] * 20)} for i, t in enumerate(["kinetic", "title", "claim", "bars", "kinetic", "timeline", "sources", "stat", "kinetic", "outro"])]
    chosen = _pick_scenes(scenes, 0.8, 165)
    assert len(chosen) == 8 and 1 in chosen and 4 not in chosen and 7 not in chosen  # bars + sources stay graphics
    assert len(_pick_scenes(scenes, 1.0, 165)) == 10
    assert _pick_scenes(scenes, 0.0, 165) == {1, 2, 5, 9, 10}  # kinetic/title/outro always get footage


def test_assign_footage_reaches_80_percent(footage_project):
    s = footage_project["settings"]
    scenes = [{"scene_number": i + 1, "template": t, "narration": f"Lucia and Jason drive through Vice City in scene {i}. " * 2, "on_screen_text": "", "emphasis": ""} for i, t in enumerate(["kinetic", "title", "claim", "bars", "kinetic", "timeline", "sources", "stat", "bullets", "kinetic", "headline", "outro"])]
    out = assign_footage(s, scenes)
    summ = out["summary"]
    assert 0.75 <= summ["share"] <= 0.9, summ
    assert summ["video_scenes"] > summ["photo_scenes"] >= 0
    with_footage = [sc for sc in scenes if sc.get("footage")]
    for sc in with_footage:
        fo = sc["footage"]
        assert fo["credit"] == "Footage: Rockstar Games"
        if fo["kind"] == "video":
            assert all(p["end"] - p["start"] <= 6.0 + 1e-6 for p in fo["pieces"])
        else:
            assert "leaked" not in fo["file"]
    assert all(e["review_status"] == "approved" and e["attribution"] for e in out["ledger"])


def test_pipeline_puts_footage_in_the_render_props(footage_project):
    s = footage_project["settings"]
    job = create_job(s, [footage_project["url"]], fmt="short", options=job_options(footage_project))
    assert run_job(s, job, until="timeline") == "paused", get_db(s.db_path).get_job(job)["error"]
    root = s.jobs_dir / job
    rights = read_json(root / "assets" / "rights_review.json")
    assert rights["footage"]["share"] >= 0.7, rights["footage"]
    assert "Footage: Rockstar Games" in rights["credits"]
    props = read_json(root / "render" / "props.json")
    fps = props["fps"]
    with_footage = [sc for sc in props["scenes"] if sc.get("footage")]
    covered = sum(sc["durationFrames"] for sc in with_footage) / props["totalFrames"]
    assert covered >= 0.7, covered
    public = s.root / "templates" / ".bundle" / "public"
    for sc in with_footage:
        f = public / sc["footage"]["src"]
        assert f.exists(), f
        if sc["footage"]["kind"] == "video":
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)], capture_output=True, text=True).stdout)
            assert dur >= sc["durationFrames"] / fps - 0.05  # the clip covers the whole scene
            w, h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(f)], capture_output=True, text=True).stdout.strip().split(",")
            assert (int(w), int(h)) == (1080, 1920)  # cover-cropped to 9:16


@pytest.mark.render
@pytest.mark.skipif(not RENDER, reason="set GTA_FACTORY_RENDER_TESTS=1 to run the Remotion render")
def test_full_render_with_footage(footage_project):
    s = footage_project["settings"]
    job = create_job(s, [footage_project["url"]], fmt="short", options=job_options(footage_project))
    status = run_job(s, job)
    assert status in ("completed", "needs_review"), get_db(s.db_path).get_job(job)["error"]
    out = s.outputs_dir / job
    assert list(out.glob("*.mp4"))
    concepts = read_json(s.jobs_dir / job / "thumbnails" / "concepts.json")
    assert all(c["props"].get("photo") for c in concepts)
