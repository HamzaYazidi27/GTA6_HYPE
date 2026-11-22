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


@pytest.mark.parametrize(("answer", "words"), [("bad score", ["bad", "score"]), (["score"], ["score"]), ({"bad": 1}, [])])
def test_model_emphasis_keeps_whole_words(answer, words) -> None:
    beat = plan.segment_script("One bad score.")["beats"][0]
    assert plan._merge(beat, {"emphasis": answer})["emphasis"] == words


def test_lead_numbers() -> None:
    assert plan.lead_number("Seven: go viral") == 7
    assert plan.lead_number("Number 3. Dive") == 3
    assert plan.lead_number("Six-star wanted levels") is None
    assert plan.lead_number("#4 - airboats") == 4
    assert plan.lead_number("14.9 million views.") is None
    assert plan.lead_number("17,500,000 views.") is None


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


def test_sequence_fills_the_beat_and_holds_when_the_shot_is_short() -> None:
    m = {"beat": 3, "segments": [
        {"kind": "shot", "path": "/a.mp4", "start": 1.0, "end": 2.0, "shot_end": 2.0},
        {"kind": "shot", "path": "/b.mp4", "start": 5.0, "end": 5.6, "shot_end": 5.6},
    ]}
    seq = match.sequence_for(m, 2.0, offset=0.5)
    assert [s["t"] for s in seq] == [0.5, 1.5]
    assert sum(s["d"] for s in seq) == pytest.approx(2.0)
    assert seq[-1]["hold"] == pytest.approx(0.4)  # 0.6 s of shot for a 1.0 s remainder
    assert "hold" not in seq[0]


def test_photo_only_library_fills_rejected_beats(project, monkeypatch) -> None:
    from gta_factory.app.core.errors import FactoryError
    from gta_factory.modules.footage import clip as footage_clip
    from gta_factory.modules.footage.library import footage_dir
    from PIL import Image

    s = project["settings"]
    folder = footage_dir(s) / "photos"
    folder.mkdir(parents=True)
    Image.new("RGB", (640, 480), "green").save(folder / "park.jpg")

    def indexed(settings, items, **kwargs):
        rows = [dict(key=p["key"], kind="photo", t=None) for p in items["photos"]]
        return rows, np.ones((len(rows), 2))

    class Clip:
        def embed_text(self, texts):
            return np.ones((len(texts), 2))

    monkeypatch.setattr(footage_clip, "index_frames", indexed)
    beat = {"text": "Shoot hoops.", "concept": "a basketball hoop", "specific": True, "game": "gta6"}
    matches = match.match_beats(s, [beat], [1.5], clip=Clip(), allow_photos=True, log=lambda _: None)
    assert matches[0]["considered"] == 1 and matches[0]["status"] == "missing"
    with pytest.raises(FactoryError, match="No usable gta6 visual"):
        match.fill_illustrations(s, matches, job="test", clip=Clip(), allow_photos=False, log=lambda _: None)
    match.fill_illustrations(s, matches, job="test", clip=Clip(), allow_photos=True, log=lambda _: None)
    result = matches[0]
    assert result["status"] == "illustration" and result["confidence"] == 0
    assert match.sequence_for(result, 1.5) == [
        {"beat": 0, "d": 1.5, "t": 0.0, "type": "photo", "file": str(folder / "park.jpg"), "start": 0.0}
    ]


@pytest.mark.parametrize("has_alternate", [True, False])
def test_long_focus_still_cuts_on_spoken_clause_only_with_eligible_alternate(project, monkeypatch, has_alternate) -> None:
    from copy import deepcopy
    from PIL import Image
    from gta_factory.modules.footage import clip as footage_clip
    from gta_factory.modules.footage.library import footage_dir, sidecar_path

    settings = project["settings"]
    folder = footage_dir(settings) / "photos"
    folder.mkdir(parents=True)
    vectors = {"first": [0.8, 0.6, 0.0], "copy": [0.8, 0.6, 0.0],
               "unlicensed": [0.9, -0.4, 0.0], "unrelated": [0.1, 0.0, 0.99]}
    if has_alternate:
        vectors["second"] = [0.79, -0.6, 0.0]
    for name in vectors:
        photo = folder / f"{name}.jpg"
        Image.new("RGB", (400, 300), "green").save(photo)
        if name != "unlicensed":
            sidecar_path(photo).write_text(json.dumps({"own_capture": True}))

    def indexed(settings, items, **kwargs):
        rows = [dict(key=p["key"], kind="photo", t=None) for p in items["photos"]]
        return rows, np.array([vectors[Path(row["key"]).stem] for row in rows])

    class Clip:
        def embed_text(self, texts):
            return np.tile([1.0, 0.0, 0.0], (len(texts), 1))

    monkeypatch.setattr(footage_clip, "index_frames", indexed)
    first = dict(file="photos/first.jpg", path=str(folder / "first.jpg"), kind="photo", start=0.0, end=6.3, rights="own")
    match_ = dict(beat=4, need=6.3, game="gta6", text="A bad score, pulling them into trouble.",
                  concept="a car chase", segments=[first], status="illustration", confidence=0, reason="unverified")
    kwargs = dict(job="test", clip=Clip(), allow_photos=True, log=lambda _: None)
    timing = [dict(beat=4, start=9.133, need=6.3, words=[
        {"w": "score,", "s": 10.973, "e": 11.373}, {"w": "pulling", "s": 11.813, "e": 12.033},
        {"w": "them", "s": 12.033, "e": 12.233}, {"w": "into", "s": 12.233, "e": 12.433}])]

    original = deepcopy(match_)
    match.fill_illustrations(settings, [match_], **kwargs)
    assert match_ == original
    verified = dict(original, status="verified_clip")
    match.fill_illustrations(settings, [verified], focus_beats=timing, **kwargs)
    assert verified == dict(original, status="verified_clip")

    match.fill_illustrations(settings, [match_], focus_beats=timing, **kwargs)
    sequence = match.sequence_for(match_, 6.3)
    assert match_["status"] == "illustration" and match_["reason"] == "unverified" and match_["confidence"] == 0
    assert [sg["d"] for sg in sequence] == ([2.68, 3.62] if has_alternate else [6.3])
    assert [Path(sg["file"]).stem for sg in sequence] == (["first", "second"] if has_alternate else ["first"])
    assert all(a["review_status"] == "cleared" for a in produce.rights_review(settings, [match_])["assets"])
    saved = deepcopy(match_)
    match.fill_illustrations(settings, [match_], focus_beats=timing, **kwargs)
    assert match_ == saved


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


def test_focus_sound_starts_on_picture_and_spaces_out_speech_accents() -> None:
    ed = style.editor_params(None, "focus")
    plan = {"beats": [{"role": "hook"}, {"role": "item", "number": 2}, {"role": "item", "number": 1}, {"role": "cta"}]}
    times = [{"beat": 0, "start": 0.0}, {"beat": 1, "start": 2.7, "number_at": 2.82},
             {"beat": 2, "start": 3.9, "number_at": 4.0}, {"beat": 3, "start": 6.2}]
    cues = produce.sfx_events(plan, times, ed)
    assert [(c["sfx"], c["at"]) for c in cues] == [("impact", 0.0), ("tick", 2.82), ("pop", 6.2)]
    assert all(c["timing"] == "start_at" and c["trim_db"] < 0 for c in cues)

    plan = {"beats": [{"role": "hook"}, {"role": "claim"}, {"role": "claim"}, {"role": "comparison"}]}
    assert [(c["sfx"], c["at"]) for c in produce.sfx_events(plan, times, ed)] == [("impact", 0.0), ("tick", 2.7), ("tick", 6.2)]


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


def test_reencoded_picture_is_reserved_across_beats_and_web_passes(project, monkeypatch):
    from gta_factory.modules.footage import clip as footage_clip
    from gta_factory.modules.footage.library import footage_dir
    from PIL import Image

    folder = footage_dir(project["settings"]) / "photos"
    folder.mkdir(parents=True)
    for name in ["a", "copy", "other", "noise"]:
        Image.new("RGB", (320, 180), "blue").save(folder / f"{name}.jpg")

    def indexed(settings, items, **kwargs):
        rows = [dict(key=p["key"], kind="photo", t=None) for p in items["photos"]]
        vectors = {"a": [1., 0.], "copy": [1., 0.], "other": [0.96, 0.28], "noise": [0., 1.]}
        return rows, np.array([vectors[Path(r["key"]).stem] for r in rows])

    class Clip:
        def embed_text(self, texts):
            return np.tile([1., 0.], (len(texts), 1))

    monkeypatch.setattr(footage_clip, "index_frames", indexed)
    opts = dict(clip=Clip(), allow_photos=True, log=lambda _: None)
    beat = {"game": "gta6", "text": "Look closely.", "concept": "a car", "specific": False}
    # Reserve a photo from pass one. Its duplicate must be rejected even when it ranks highest.
    got = match.match_beats(project["settings"], [beat], [1.5], used={"photos/a.jpg": [(0, 2)]}, **opts)
    assert got[0]["segments"][0]["file"] == "photos/other.jpg"
    missing = dict(beat, beat=0, need=1.5, segments=[], status="missing")
    reserved = dict(beat, beat=1, need=1, segments=[dict(path=str(folder/"a.jpg"), start=0, end=1)], status="general")
    match.fill_illustrations(project["settings"], [missing, reserved], job="new", **opts)
    assert missing["segments"][0]["file"] == "photos/other.jpg"


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
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/script-short", data=json.dumps({"script": EXAMPLE, "options": {"fps": "60", "allow_weak": True, "llm": "none", "edit_style": "focus"}}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Factory": "1"})
        job_id = json.loads(urllib.request.urlopen(req).read())["id"]
        d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs/{job_id}").read())
        assert d["job"]["options"]["edit_style"] == "focus" and d["short"]["style"] == "focus"
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


def test_source_still_and_parent_shot_reserve_the_same_picture():
    from types import SimpleNamespace

    lib = SimpleNamespace(by_key={
        "shot": dict(file="t.mp4", path="/t.mp4", kind="shot", s=140.0, e=141.5),
        "still": dict(file="pic.jpg", path="/pic.jpg", kind="photo"),
        "next": dict(file="t.mp4", path="/t.mp4", kind="shot", s=141.5, e=143.0)
    }, origin={"shot": "trailer@140", "still": "trailer@140", "next": "trailer@142"})
    assert match._used_origins(lib, {"pic.jpg": [(0, 2)]}) == {"trailer@140"}
    assert match._used_origins(lib, {"t.mp4": [(140.3, 141.1)]}) == {"trailer@140"}


def test_clip_alone_does_not_verify_a_near_tie_with_a_kayak(project, monkeypatch):
    from gta_factory.modules.footage import clip as footage_clip
    from gta_factory.modules.footage.library import footage_dir
    from PIL import Image

    settings = project["settings"]
    folder = footage_dir(settings) / "photos"
    folder.mkdir(parents=True)
    Image.new("RGB", (320, 180)).save(folder / "boat.jpg")

    def indexed(s, items, **kw):
        return [{"key": items["photos"][0]["key"], "t": None, "kind": "photo"}], np.array([[1., 0.]])

    class Clip:
        def embed_text(self, texts):
            return np.array([[0.325 if "jet ski" in t else 0.324 if "kayak" in t else 0.1, 0.] for t in texts])

    monkeypatch.setattr(footage_clip, "index_frames", indexed)
    beat = dict(text="Another jet ski.", concept="a person on a jet ski", specific=True)
    result, = match.match_beats(settings, [beat], [1.], clip=Clip(), allow_photos=True, log=lambda _: None)
    assert result["status"] == "missing" and not result["segments"]


def test_view_count_uses_previous_action_for_footage():
    from gta_factory.modules.script_short.websource import queries
    beats = plan.segment_script("Number three: the airport. Jason walks through security. 14.9 million views.")["beats"]
    produce.number_items(beats)
    metric = beats[-1]
    assert metric["text"] == "14.9 million views."
    assert metric["number"] is None and metric["item"] == 3
    assert metric["concept"] == beats[1]["concept"] and metric["visual_text"] == beats[1]["text"]
    assert all("views" not in query for query in queries(metric, "GTA 6"))


def test_replay_script_sources_stay_out_of_voice_and_metrics_stay_with_item(project) -> None:
    from gta_factory.modules.script_short.replay import replay_cards

    url = "https://www.tiktok.com/@someone/video/1234567890123456789"
    script = f"Number two: the airport. Walk through security. 14.9 million views. {url}#t=18\nNumber one: the beach. Which one would you visit?"
    s = project["settings"]
    job = produce.create_script_job(s, script)
    opts = json.loads((s.jobs_dir / job / "job.json").read_text())["options"]
    assert opts["edit_style"] == "replay"
    assert "https" not in opts["script_text"]
    assert opts["replay_sources"] == [dict(first=0, last=9, url=url, start=18)]
    beats = plan.segment_script(opts["script_text"])["beats"]
    produce.number_items(beats)
    cards = replay_cards(beats, "fallback")
    assert [c["value"] for c in cards[:3]] == ["14.9M"] * 3
    assert all(c["headline"] == "the airport" for c in cards[:3])
    assert all("value" not in c for c in cards[3:])
    assert beats[-1]["item"] is None
    assert cards[-1]["headline"] == "Which one would you visit?"


def test_replay_rank_spoken_alone_uses_the_next_subject():
    from gta_factory.modules.script_short.replay import replay_cards

    beats = plan.segment_script("Four. Open the truck. Three. Through airport security.")["beats"]
    produce.number_items(beats)
    assert [c["headline"] for c in replay_cards(beats, "unused")] == [
        "Open the truck", "Open the truck", "Through airport security", "Through airport security"]
