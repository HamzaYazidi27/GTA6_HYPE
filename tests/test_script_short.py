"""Script to Short: script understanding, beat timing, footage windows, frame-exact cutting, EDL, SFX,
the reference analyzer's measurements, and the job wiring (dashboard / CLI / pipeline)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from gta_factory.modules.script_short import match, plan, produce, reference, style

EXAMPLE = (
    "GTA 5 never let you do any of these. Seven: go viral on in-game social media. Six: grab a ball and shoot hoops. "
    "Five: race jet skis, or paddle a kayak. Four: tear through the swamps on an airboat. Three: scuba dive with dolphins. "
    "Two: your body changes. Overeat and you gain weight. Hit the gym and you build muscle. And number one changes everything. "
    "Six-star wanted levels are back. GTA 5 stopped at five. Comment the number you're trying first."
)
FFMPEG = shutil.which("ffmpeg") is not None


# ----------------------------------------------------------------------------- script understanding
def test_rule_plan_of_the_acceptance_script() -> None:
    p = plan.segment_script(EXAMPLE)
    beats = p["beats"]
    # every word in exactly one beat, in order
    assert plan.validate_beats([{"words": b["words"]} for b in beats], len(p["words"])) is None
    numbers = [b["number"] for b in beats if b["number"]]
    assert numbers == [7, 6, 5, 4, 3, 2, 1]  # "Six-star" is not item six; "number one" is carried to the next sentence
    by_text = {b["text"]: b for b in beats}
    assert by_text["GTA 5 never let you do any of these."]["role"] == "hook"
    assert by_text["GTA 5 never let you do any of these."]["game"] == "gta6"  # the hook shows what GTA 6 does
    assert by_text["Five: race jet skis,"]["concept"] == "race jet skis"
    assert by_text["or paddle a kayak."]["concept"] == "paddle a kayak"  # two things to show: two beats
    assert by_text["And number one changes everything."]["role"] == "suspense"
    assert by_text["Six-star wanted levels are back."]["number"] == 1
    assert by_text["GTA 5 stopped at five."]["game"] == "gta5" and by_text["GTA 5 stopped at five."]["role"] == "comparison"
    assert beats[-1]["role"] == "cta" and not beats[-1]["specific"]


def test_validate_beats_rejects_gaps_and_overlaps() -> None:
    assert plan.validate_beats([{"words": [0, 3]}, {"words": [5, 9]}], 10)  # gap at 4
    assert plan.validate_beats([{"words": [0, 3]}, {"words": [3, 9]}], 10)  # overlap
    assert plan.validate_beats([{"words": [0, 3]}, {"words": [4, 8]}], 10)  # stops early
    assert plan.validate_beats([{"words": [0, 4]}, {"words": [5, 9]}], 10) is None


def test_lead_numbers() -> None:
    assert plan.lead_number("Seven: go viral") == 7
    assert plan.lead_number("Number 3. Dive") == 3
    assert plan.lead_number("Six-star wanted levels") is None
    assert plan.lead_number("#4 - airboats") == 4


def test_voice_units_keep_beats_inside_one_take() -> None:
    words = plan.words_of("One two. Three four. Five six.")
    beats = [{"words": [0, 2]}, {"words": [3, 3]}, {"words": [4, 5]}]  # beat 0 runs across the first sentence end
    units = produce.voice_units(beats, words)
    assert units == [(0, 3, [0, 1]), (4, 5, [2])]


def test_number_items_carries_the_badge() -> None:
    beats = [{"role": "hook", "number": None}, {"role": "item", "number": 5}, {"role": "item", "number": None}, {"role": "cta", "number": None}]
    produce.number_items(beats)
    assert [b["item"] for b in beats] == [None, 5, 5, None]


# ----------------------------------------------------------------------------- timing
def _plan_two_takes() -> tuple[dict, list[dict], dict, dict]:
    p = {"beats": [
        {"words": [0, 2], "text": "Five: race jet", "number": 5},
        {"words": [3, 5], "text": "or paddle kayak.", "number": None},
        {"words": [6, 8], "text": "Four: an airboat.", "number": 4},
    ]}
    scenes = [{"scene_number": 1, "words": [0, 5], "beats": [0, 1]}, {"scene_number": 2, "words": [6, 8], "beats": [2]}]
    placed = {1: {"start_time": 0.0, "duration": 2.0, "voice_offset": 0.1}, 2: {"start_time": 2.0, "duration": 1.5, "voice_offset": 0.05}}
    w = lambda *ts: [{"w": "x", "s": a, "e": a + 0.2} for a in ts]  # noqa: E731
    words = {1: w(0.0, 0.3, 0.6, 1.0, 1.3, 1.6), 2: w(0.0, 0.4, 0.8)}
    return p, scenes, placed, words


def test_beat_times_cut_on_the_words_and_cover_the_video() -> None:
    p, scenes, placed, words = _plan_two_takes()
    bt = produce.beat_times(p, scenes, placed, words, cut_lead=0.05)
    assert [b["beat"] for b in bt] == [0, 1, 2]
    # contiguous: every beat ends where the next starts, the last ends with its take
    assert bt[0]["start"] == 0.0 and bt[0]["end"] == bt[1]["start"] and bt[1]["end"] == 2.0 and bt[2]["end"] == 3.5
    # the inner cut lands 0.05 s before "or" (take audio starts at 0.1, "or" at 1.0)
    assert bt[1]["start"] == pytest.approx(1.05)
    assert bt[1]["speech_start"] == pytest.approx(1.1)
    assert bt[0]["number_at"] == pytest.approx(0.1) and bt[1]["number_at"] is None
    assert sum(b["need"] for b in bt) == pytest.approx(3.5)


def test_place_scenes_uses_gap_after(project) -> None:
    from gta_factory.modules.voice_generation.engine import place_scenes

    s = project["settings"]
    voice = {"scenes": [{"scene_number": 1, "duration": 1.0}, {"scene_number": 2, "duration": 1.0}]}
    scenes = [{"scene_number": 1, "template": "shot", "gap_after": 0.0, "voice_lead": 0.1}, {"scene_number": 2, "template": "shot"}]
    placed, total = place_scenes(s, scenes, voice, 30, vertical=True)
    assert placed[0]["voice_offset"] == pytest.approx(0.1)
    assert placed[0]["duration"] == pytest.approx(1.1, abs=1 / 30)  # no breath after a take with gap_after 0
    assert placed[1]["start_frame"] == placed[0]["duration_frames"]


# ----------------------------------------------------------------------------- footage windows
def test_zero_shot_prefers_the_concept_over_lookalikes() -> None:
    pos = np.array([0.30, 0.26])
    neg = np.array([[0.25, 0.20], [0.28, 0.29]])
    p = match.zero_shot(pos, neg)
    assert p[0] > 0.6 and p[1] < 0.4


def test_best_window_starts_on_the_action() -> None:
    times = [0.25 + 0.5 * k for k in range(12)]  # a 6 s shot
    probs = [0.1, 0.1, 0.1, 0.2, 0.9, 0.95, 0.9, 0.9, 0.3, 0.2, 0.1, 0.1]  # the action is at 2-4 s
    a, z, mean, first = match.best_window(times, probs, 0.0, 6.0, 1.5)
    assert 1.75 <= a <= 2.25 and z - a == pytest.approx(1.5)
    assert first >= 0.85
    # a window already used is avoided
    a2, *_ = match.best_window(times, probs, 0.0, 6.0, 1.5, used=[(a, z)])
    assert abs(a2 - a) >= 0.5


def test_best_window_never_reads_past_the_shot() -> None:
    a, z, *_ = match.best_window([0.25, 0.75], [0.9, 0.9], 0.0, 1.0, 3.0)
    assert (a, z) == (0.0, 1.0)


def test_sequence_fills_the_beat_in_slow_motion_when_the_shot_is_short() -> None:
    m = {"beat": 3, "segments": [
        {"kind": "shot", "path": "/a.mp4", "start": 1.0, "end": 2.0, "shot_end": 2.0},
        {"kind": "shot", "path": "/b.mp4", "start": 5.0, "end": 5.6, "shot_end": 5.6},
    ]}
    seq = match.sequence_for(m, 2.0, offset=0.5)
    assert [s["t"] for s in seq] == [0.5, 1.5]
    assert sum(s["d"] for s in seq) == pytest.approx(2.0)
    assert seq[-1]["speed"] == pytest.approx(0.6) and "hold" not in seq[-1]  # 0.6 s of shot played over 1.0 s, still moving
    assert "hold" not in seq[0] and "speed" not in seq[0]
    # slower than half speed would look broken: half speed, then the last frame is held
    m["segments"][1]["shot_end"] = 5.3
    last = match.sequence_for(m, 2.0)[-1]
    assert last["speed"] == pytest.approx(0.5) and last["hold"] == pytest.approx(0.4)


# ----------------------------------------------------------------------------- cutting, EDL, sound
@pytest.mark.skipif(not FFMPEG, reason="ffmpeg missing")
def test_cut_sequence_is_frame_exact(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    segs = [
        {"t": 0.0, "d": 0.5, "type": "shot", "file": str(src), "start": 1.0, "beat": 0},
        {"t": 0.5, "d": 0.4, "type": "blank", "beat": 1},
        {"t": 0.9, "d": 0.6, "type": "shot", "file": str(src), "start": 3.7, "beat": 2, "hold": 0.3},
    ]
    out = produce.cut_sequence(segs, 45, out=tmp_path / "o.mp4", cache_dir=tmp_path / "cache", width=180, height=320, fps=30)
    n = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames,width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert n == "180,320,45"


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg missing")
def test_cut_sequence_slow_motion_and_moving_pictures_are_frame_exact(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    pic = tmp_path / "pic.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360", "-frames:v", "1", str(pic)], check=True)
    segs = [
        {"t": 0.0, "d": 1.0, "type": "shot", "file": str(src), "start": 1.0, "beat": 0, "speed": 0.5},
        {"t": 1.0, "d": 1.0, "type": "photo", "file": str(pic), "beat": 1},
        {"t": 2.0, "d": 0.5, "type": "shot", "file": str(src), "start": 3.7, "beat": 2, "speed": 0.5, "hold": 0.2},
    ]
    out = produce.cut_sequence(segs, 75, out=tmp_path / "o.mp4", cache_dir=tmp_path / "cache", width=180, height=320, fps=30)
    n = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames,width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert n == "180,320,75"
    # the picture moves: its first and last frames differ (a still picture would be identical)
    a, b = (subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-vf", f"select=eq(n\\,{k})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True).stdout for k in (31, 58))
    assert a and b and a != b


def test_edl_lists_every_piece() -> None:
    edl = produce.edit_decision_list("Test", 30, [
        {"beat": 0, "text": "Seven: go viral", "type": "shot", "file": "videos/trailer_2.mp4", "source_in": 12.5, "record_in": 0.0, "record_out": 1.5},
        {"beat": 1, "text": "Six: hoops", "type": "blank", "file": None, "source_in": None, "record_in": 1.5, "record_out": 2.0},
    ])
    assert "001  TRAILER2 V     C        00:00:12:15 00:00:14:00 00:00:00:00 00:00:01:15" in edl
    assert "002  BL" in edl and "* BEAT 2: Six: hoops" in edl


def test_sfx_events_hit_the_numbers() -> None:
    p = {"beats": [{"role": "hook", "number": None, "item": None}, {"role": "item", "number": 7, "item": 7}, {"role": "cta", "number": None, "item": None}]}
    bt = [{"beat": 0, "start": 0.0, "speech_start": 0.1}, {"beat": 1, "start": 2.0, "speech_start": 2.05, "number_at": 2.05}, {"beat": 2, "start": 5.0, "speech_start": 5.1}]
    ev = produce.sfx_events(p, bt, style.DEFAULT_EDITOR)
    assert {"sfx": "riser", "at": 2.0, "timing": "end_at"} in ev
    assert {"sfx": "impact", "at": 2.05} in ev and {"sfx": "whoosh", "at": 1.75} in ev
    assert {"sfx": "pop", "at": 5.1} in ev


# ----------------------------------------------------------------------------- style profile and reference analyzer
def test_style_profile_is_not_measured_until_analysed(tmp_path: Path) -> None:
    class S:
        root = tmp_path

        def get(self, k, d=None):
            return d

    prof = style.load_profile(S())
    assert prof["status"] == "not_measured" and prof["editor"]["captions"]["max_words"] == 3
    measured = {"pacing": {"shot_median_s": 1.2, "shot_p90_s": 2.0}, "captions": {"present_share": 0.9, "center_y": 0.5, "height_frac": 0.05, "words_on_screen_median": 2}, "audio": {"music_db_below_voice": 14}, "transitions": {"flash_share": 0.3}}
    ed = style.derive_editor(measured)
    assert ed["shot_hold_s"] == pytest.approx(1.38) and ed["captions"]["max_words"] == 2 and ed["music_gain_db"] == -14.0 and ed["item_transition"] == "flash"


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg missing")
def test_reference_analyzer_measures_cuts_and_captions(tmp_path: Path) -> None:
    fonts = [f for d in ("/usr/share/fonts", "/System/Library/Fonts", "/Library/Fonts") if Path(d).exists() for f in Path(d).rglob("*.ttf")]
    font = next((f for f in fonts if "Bold" in f.name), fonts[0] if fonts else None)
    if font is None:
        pytest.skip("no font for drawtext")
    v = tmp_path / "ref.mp4"
    graph = (f"[0:v]setsar=1[a];[1:v]setsar=1[b];[2:v]setsar=1[c];[a][b][c]concat=n=3:v=1:a=0,"
             f"drawtext=fontfile={font}:text='SIX STAR LEVELS':fontsize=64:fontcolor=white:borderw=6:bordercolor=black:x=(w-tw)/2:y=h*0.68[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x1040c0:s=720x1280:r=30:d=1.5", "-f", "lavfi", "-i", "color=c=0xd0d040:s=720x1280:r=30:d=1.7", "-f", "lavfi", "-i", "color=c=0x803020:s=720x1280:r=30:d=1.8",
                    "-f", "lavfi", "-i", "sine=f=300:d=5", "-filter_complex", graph, "-map", "[v]", "-map", "3:a", "-t", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)], check=True)
    r = reference.analyze_video(v, url="test", log=lambda m: None)
    assert [round(c, 1) for c in r["cuts"]] == [1.5, 3.2]
    assert r["pacing"]["shots"] == 3
    assert r["captions"]["present_share"] >= 0.9
    assert r["captions"]["center_y"] == pytest.approx(0.7, abs=0.03)
    assert r["captions"]["words_on_screen_median"] == 3


def test_reference_report_says_when_nothing_was_measured(tmp_path: Path) -> None:
    md = reference.render_report(style.default_profile("YouTube blocked"), [])
    assert "NOT MEASURED" in md and "YouTube blocked" in md


# ----------------------------------------------------------------------------- job wiring
def test_script_job_has_the_short_steps(project) -> None:
    from gta_factory.app.database.db import get_db

    s = project["settings"]
    with pytest.raises(Exception):
        produce.create_script_job(s, "too short")
    job_id = produce.create_script_job(s, EXAMPLE, options={"llm": "none"})
    steps = [st["name"] for st in get_db(s.db_path).get_steps(job_id)]
    assert steps == produce.SCRIPT_STEP_NAMES
    job = json.loads((s.jobs_dir / job_id / "job.json").read_text())
    assert job["format"] == "script_short" and job["options"]["script_text"].startswith("GTA 5 never")


def test_script_plan_step_writes_takes_and_scenes(project) -> None:
    from gta_factory.app.core.pipeline import run_job
    from gta_factory.app.core.utils import read_json

    s = project["settings"]
    job_id = produce.create_script_job(s, EXAMPLE, options={"llm": "none", "no_search": True})
    assert run_job(s, job_id, until="claims") == "paused"
    root = s.jobs_dir / job_id
    p = read_json(root / "plan" / "edit_plan.json")
    scenes = read_json(root / "scenes" / "scene_breakdown.json")
    assert p["method"] == "rules" and len(p["beats"]) == 14
    assert " ".join(sc["narration"] for sc in scenes) == " ".join(EXAMPLE.split())
    assert all(sc["template"] == "shot" for sc in scenes)
    claims = read_json(root / "research" / "claims.json")
    assert claims["status"] == "not_checked" and all(c["status"] == "unverified" for c in claims["claims"])


def test_dashboard_creates_a_script_job(project) -> None:
    import threading
    import urllib.request

    from gta_factory.app.dashboard import server as srv

    s = project["settings"]
    srv.spawn_runner = lambda *a, **k: None  # do not start the job here
    httpd = srv.make_server(s, port=0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/script-short", data=json.dumps({"script": EXAMPLE, "options": {"fps": "60", "allow_weak": True, "llm": "none"}}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Factory": "1"})
        job_id = json.loads(urllib.request.urlopen(req).read())["id"]
        d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs/{job_id}").read())
        assert d["short"]["script"].startswith("GTA 5") and d["job"]["options"]["fps"] == "60" and d["job"]["options"]["allow_weak"] is True
    finally:
        httpd.shutdown()


# ----------------------------------------------------------------------------- open-vocabulary verification
def test_vocabulary_synonyms_are_not_competitors() -> None:
    from gta_factory.modules.script_short.vocabulary import SCENE_LABELS, synonyms

    def same(concept: str) -> set[str]:
        return {lab for lab, f in zip(SCENE_LABELS, synonyms([concept])) if f}

    assert {"a jet ski", "a person on a jet ski"} <= same("race jet skis")
    assert "a seaplane" not in same("race jet skis") and "a speedboat" not in same("race jet skis")  # look-alikes stay competitors
    assert {"a person scuba diving", "dolphins"} <= same("scuba dive with dolphins")
    assert same("Hit the gym and you build muscle") == {"a gym"}
    assert "a person shooting a gun" not in same("grab a ball and shoot hoops")


def test_texts_are_compared_in_one_frame() -> None:
    assert match.phrase("a video game screenshot: race jet skis") == "race jet skis"
    assert match.phrase("GTA VI gameplay: an airboat in a swamp") == "an airboat in a swamp"
    assert match.templated(["a jet ski"]) == ["a screenshot from a video game showing a jet ski"]
    assert match.NULL in match.templated(["x"])[0]


def test_model_only_describes_the_rule_beats() -> None:
    class LLM:
        def __init__(self, answer):
            self.answer, self.calls = answer, 0

        def complete_json(self, system, prompt, **kw):
            self.calls += 1
            assert kw["retries"] == 1 and kw["max_tokens"] <= 4000
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

    rules = plan.segment_script(EXAMPLE)["beats"]
    answer = {"title": "7 things GTA 5 never let you do", "beats": [
        {"i": 4, "role": "hook", "concept": "a person riding a jet ski fast on the ocean", "prompts": ["a jet ski on the ocean"], "distractors": ["a motorboat", "a seaplane"], "game": "gta6", "specific": True, "emphasis": ["jet skis"]},
        {"i": 1, "concept": "anything", "specific": True, "game": "gta5"},
    ]}
    llm = LLM(answer)
    p = plan.plan_script(llm, EXAMPLE)
    assert llm.calls == 1 and p["title"] == "7 things GTA 5 never let you do" and p["method"] == "model+rules"
    jet = p["beats"][3]
    assert jet["words"] == rules[3]["words"] and jet["number"] == 5 and jet["role"] == "item"  # words, numbers and roles stay
    assert jet["concept"].startswith("a person riding a jet ski") and jet["distractors"] == ["a motorboat", "a seaplane"]
    hook = p["beats"][0]
    assert hook["role"] == "hook" and hook["specific"] is False and hook["game"] == "gta6"  # a hook stays any strong GTA 6 gameplay
    assert [b["words"] for b in p["beats"]] == [b["words"] for b in rules]
    failed = plan.plan_script(LLM(RuntimeError("timeout")), EXAMPLE)
    assert failed["method"] == "rules" and failed["title"] == "GTA 5 never let you do any of these"
