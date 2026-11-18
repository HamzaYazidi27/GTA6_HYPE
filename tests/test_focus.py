"""One story per video, a Short that keeps moving, and no other channel's logo in the footage.

These guard the problems found in a published Short: it opened on a side story
(a promotion unrelated to its title), ended on generic filler, held cards for
10 s, said "G T uh Six", and showed a re-upload with another channel's watermark.
"""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import numpy as np

from gta_factory.modules.footage.library import static_overlay_box
from gta_factory.modules.news_research.research import split_off_topic, verify_evidence
from gta_factory.modules.script_generation.scenes import _ground_takeaway, plan_scenes
from gta_factory.modules.script_generation.writer import lint_script
from gta_factory.modules.voice_generation.engine import build_timeline, load_lexicon, spoken_text, write_wav
from gta_factory.providers.tts import get_tts

CORE = {"id": "C1", "claim": "Take-Two CEO Strauss Zelnick said generative AI has zero part in GTA 6.", "classification": "reported", "confidence": "high", "original_publisher": "Insider Gaming", "original_source": "https://x", "relevance": "core"}
SIDE = {"id": "C2", "claim": "Xbox and DJ Khaled offered fans doctor's notes for November 19.", "classification": "reported", "confidence": "medium", "original_publisher": "Insider Gaming", "original_source": "https://x", "relevance": "context"}


# ----------------------------------------------------------------- pronunciation
def test_gta_is_read_as_one_word_never_with_a_lone_article():
    lex = load_lexicon(SimpleNamespace(root=Path("/nonexistent")))
    said = spoken_text("GTA 6 and GTA VI, after GTA 3 and GTA V.", lex)
    assert said == "GTA Six and GTA Six, after GTA Three and GTA Five."
    assert " A " not in f" {said} "  # "G T A Six" made the voice say "gee tee uh six"
    assert spoken_text("Take-Two CEO Strauss Zelnick and DJ Khaled", lex) == "Take-Two C E O Strouse Zell-nick and DJ Kah-led"


# ----------------------------------------------------------------- research: side stories leave the pipeline
def test_off_topic_claims_and_quotes_are_dropped_but_never_everything():
    items = [{"relevance": "core"}, {"relevance": "off_topic"}, {"relevance": "context"}, {}]
    kept, off = split_off_topic(items, keep_at_least=3)
    assert len(kept) == 3 and len(off) == 1
    kept, off = split_off_topic([{"relevance": "off_topic"}, {"relevance": "core"}], keep_at_least=3)
    assert len(kept) == 2 and off == []  # a mislabelled analysis must not empty the video


def test_verify_evidence_keeps_relevance_and_moves_side_stories_out():
    text = (
        "Take-Two CEO Strauss Zelnick said generative AI has zero part in what Rockstar Games is building. "
        "Microsoft paid DJ Khaled to give fans doctor's notes for taking November 19 off. "
        "Rockstar builds its worlds street by street, neighborhood by neighborhood. "
        "It was 25 years ago this month that I reviewed GTA 3 for the BBC."
    )
    art = SimpleNamespace(text=text, url="https://insider-gaming.com/a", publisher="Insider Gaming", source_tier="secondary")
    data = {
        "claims": [
            {"id": "C1", "claim": "Zelnick: no generative AI in GTA 6.", "source_index": 1, "evidence": "Take-Two CEO Strauss Zelnick said generative AI has zero part in what Rockstar Games is building.", "relevance": "core"},
            {"id": "C2", "claim": "Xbox doctor's notes.", "source_index": 1, "evidence": "Microsoft paid DJ Khaled to give fans doctor's notes for taking November 19 off.", "relevance": "off_topic"},
            {"id": "C3", "claim": "Built by hand.", "source_index": 1, "evidence": "Rockstar builds its worlds street by street, neighborhood by neighborhood.", "relevance": "context"},
            {"id": "C4", "claim": "Hand-made world.", "source_index": 1, "evidence": "", "relevance": "CORE"},
        ],
        "quotes": [
            {"text": "generative AI has zero part in what Rockstar Games is building", "speaker": "Strauss Zelnick", "source_index": 1, "relevance": "core"},
            {"text": "It was 25 years ago this month that I reviewed GTA 3 for the BBC", "speaker": "Daniel Coles", "source_index": 1, "relevance": "off_topic"},
        ],
    }
    out = verify_evidence(data, [art])
    assert [c["claim"] for c in out["claims"]] == ["Zelnick: no generative AI in GTA 6.", "Built by hand.", "Hand-made world."]
    assert [c["id"] for c in out["claims"]] == ["C1", "C2", "C3"]  # renumbered after the side story left
    assert [c.get("relevance") for c in out["claims"]] == ["core", "context", "core"]
    assert out["off_topic_claims"][0]["claim"] == "Xbox doctor's notes."
    assert [q["speaker"] for q in out["quotes"]] == ["Strauss Zelnick"]
    assert out["off_topic_quotes"][0]["speaker"] == "Daniel Coles"


# ----------------------------------------------------------------- script lint
def _sections(hook_ids, hook="Take-Two says generative AI has zero part in GTA 6, and that matters.", cta="What would you want Rockstar to automate?"):
    return [
        {"id": "hook", "narration": hook, "claim_ids": hook_ids},
        {"id": "story", "narration": "Insider Gaming reports the CEO said every street is built by hand.", "claim_ids": ["C1"]},
        {"id": "payoff", "narration": "For players, that means a world designed by people, block by block.", "claim_ids": []},
        {"id": "cta", "narration": cta, "claim_ids": []},
    ]


def _types(issues):
    return {i["type"] for i in issues}


def test_hook_on_a_side_story_is_flagged():
    fc = [CORE, SIDE]
    bad = lint_script(_sections(["C2"], hook="Xbox and DJ Khaled offered fans doctor's notes for launch day."), fc, [], "", min_words=1, max_words=999, short=True)
    assert "off_topic_hook" in _types(bad)
    good = lint_script(_sections(["C1"]), fc, [], "", min_words=1, max_words=999, short=True)
    assert "off_topic_hook" not in _types(good)


def test_generic_wrap_up_and_long_short_cta_are_flagged():
    secs = _sections(["C1"], cta="So tell me down below what you think about all of this and which tools you would like to see used next?")
    secs[2]["narration"] = "This approach balances innovation with tradition, shaping how future games evolve."
    issues = lint_script(secs, [CORE, SIDE], [], "", min_words=1, max_words=999, short=True)
    assert {"banned_phrase", "cta_too_long"} <= _types(issues)
    assert "cta_too_long" not in _types(lint_script(secs, [CORE, SIDE], [], "", min_words=1, max_words=999, short=False))


# ----------------------------------------------------------------- storyboard
def test_takeaway_lines_must_come_from_the_narration():
    payload = {"title": "What it means", "cause": "No generative AI", "effects": ["Maintaining the series' legacy", "Embracing new technologies", "Every street built by hand"], "cues": ["", "", ""]}
    narration = "Generative AI has zero part in GTA 6: every street is built by hand, and AI only speeds up the studio's work."
    assert _ground_takeaway(payload, narration) is None  # only one grounded line is left
    payload["effects"][1] = "AI speeds up the work"
    kept = _ground_takeaway(payload, narration)
    assert kept["effects"] == ["AI speeds up the work", "Every street built by hand"] and len(kept["cues"]) == 2


class _Planner:
    """A scene planner that puts a BREAKING label on old news and keeps one card for the whole Short."""

    def complete_json(self, system, prompt, **kw):
        return {"scenes": [
            {"sentences": [1, 1], "template": "kinetic", "data": {"lines": ["NO GEN AI", "IN GTA 6"], "accent": "GTA 6", "kicker": "Breaking"}},
            {"sentences": [2, 5], "template": "kinetic", "data": {"lines": ["BUILT BY HAND"], "kicker": "What it means"}},
            {"sentences": [6, 6], "template": "outro", "data": {"question": "Would you play a GTA made by AI?"}},
        ]}


def test_short_scenes_are_split_and_old_news_is_not_breaking(project):
    sentences = [
        "Take-Two says generative AI has zero part in GTA 6.",
        "Insider Gaming reports the company still uses AI to cut costs across its studios today.",
        "Strauss Zelnick says Rockstar builds its world street by street and block by block.",
        "He also says Take-Two runs hundreds of AI pilots that save the company real money.",
        "For players, that means every corner of Leonida was placed there by a person on purpose.",
        "Would you play a GTA made by AI?",
    ]
    script = {"sections": [{"id": "hook", "narration": sentences[0], "claim_ids": []}, {"id": "story", "narration": " ".join(sentences[1:5]), "claim_ids": []}, {"id": "cta", "narration": sentences[5], "claim_ids": []}]}
    old = [{"publisher": "Insider Gaming", "published_at": "2026-11-05", "title": "Take-Two Addresses AI In GTA 6's Development Again", "author": "x"}]
    scenes = plan_scenes(_Planner(), project["settings"], script=script, fact_check=[], quotes=[], articles=old, fmt="short", today="2026-11-18")
    assert scenes[0]["data"]["kicker"] == ""  # 13-day-old news is not "breaking"
    words_per_scene = [len(sc["narration"].split()) for sc in scenes if sc["template"] != "outro"]
    assert max(words_per_scene) <= 6 * 165 / 60 + 1  # about 6 s of narration at most per Short scene
    assert len(scenes) >= 4
    fresh = [{**old[0], "published_at": "2026-11-17"}]
    again = plan_scenes(_Planner(), project["settings"], script=script, fact_check=[], quotes=[], articles=fresh, fmt="short", today="2026-11-18")
    assert again[0]["data"]["kicker"] == "Breaking"


# ----------------------------------------------------------------- timing and voice
def test_short_timeline_is_tighter_than_long_form(project, tmp_path):
    sr = 24000
    voice = {"scenes": []}
    scenes = []
    for n, (tpl, secs) in enumerate([("kinetic", 2.0), ("kinetic", 2.5), ("outro", 1.5)], 1):
        t = np.arange(int(secs * sr)) / sr
        write_wav(tmp_path / f"scene_{n:03d}.wav", (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32), sr)
        voice["scenes"].append({"scene_number": n, "file": f"scene_{n:03d}.wav", "duration": secs})
        scenes.append({"scene_number": n, "template": tpl})
    long_form = build_timeline(project["settings"], scenes, voice, tmp_path, 30)
    short = build_timeline(project["settings"], scenes, voice, tmp_path, 30, vertical=True)
    assert short["total_seconds"] < long_form["total_seconds"] - 2.0
    assert 2.9 <= short["scenes"][-1]["duration"] <= 3.2  # end card ~3 s, not 5.5 s
    assert long_form["scenes"][-1]["duration"] >= 5.5


def test_short_voice_is_a_little_faster_for_kokoro_only(project):
    s = project["settings"]
    base = float(s.get("providers.tts.kokoro.speed") or 1.0)
    assert get_tts(s, "kokoro").config["speed"] == base
    assert abs(get_tts(s, "kokoro", speed_factor=1.06).config["speed"] - round(base * 1.06, 3)) < 1e-9
    assert get_tts(s, "kokoro", speed_factor=1.06).signature() != get_tts(s, "kokoro").signature()  # new voice cache entry


# ----------------------------------------------------------------- footage: other channels' logos
def _moving_frames(n=24, h=90, w=160, seed=3):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    frames = []
    for _ in range(n):  # every sample is a different "shot": a random mix of soft waves, nothing at a fixed place
        img = np.zeros((h, w), dtype=np.float32)
        for _k in range(6):
            fx, fy, ph = rng.uniform(0.02, 0.25), rng.uniform(0.02, 0.25), rng.uniform(0, 2 * np.pi)
            img += np.sin(fx * xx + fy * yy + ph)
        frames.append((127 + 18 * img).clip(0, 255).astype(np.uint8))
    return np.stack(frames)


def test_a_logo_that_never_moves_is_found():
    frames = _moving_frames()
    assert static_overlay_box(frames) is None
    marked = frames.copy()
    for f in marked:  # a semi-transparent "logo" at the top centre: bright strokes on whatever is underneath
        region = f[6:16, 64:96].astype(np.float32)
        region[::3, :] = region[::3, :] * 0.4 + 255 * 0.6
        region[:, ::4] = region[:, ::4] * 0.4 + 255 * 0.6
        f[6:16, 64:96] = region.astype(np.uint8)
    found = static_overlay_box(marked)
    assert found is not None
    x0, y0, x1, y1 = found["box"]
    assert 0.35 < (x0 + x1) / 2 < 0.65 and (y0 + y1) / 2 < 0.25


def test_a_still_video_is_not_judged():
    still = np.repeat(_moving_frames(n=1), 20, axis=0)
    assert static_overlay_box(still) is None


def test_a_reupload_with_a_watermark_is_left_out_of_the_library(project):
    import subprocess

    from gta_factory.app.core.utils import which, write_json
    from gta_factory.modules.footage.library import build_index, load_items
    from test_footage import make_trailer

    if not which("ffmpeg"):
        return
    root = project["root"] / "assets" / "gta6_footage"
    clean = make_trailer(root / "videos" / "Grand Theft Auto VI Trailer 1 [QdBZY2fkU-0].mp4")
    marked = root / "videos" / "GTA 6 gameplay compilation.mp4"
    logo = "drawbox=x=280:y=18:w=80:h=28:color=white@0.6:t=3,drawbox=x=292:y=26:w=56:h=12:color=white@0.5:t=2"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clean), "-vf", logo, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", str(marked)], check=True)
    s = project["settings"]
    index = build_index(s)
    by_file = {v["file"]: v for v in index["videos"]}
    assert by_file["videos/" + clean.name]["overlay"] is None
    assert by_file["videos/" + marked.name]["overlay"] is not None
    assert not any(sh["ok"] for sh in by_file["videos/" + marked.name]["shots"])
    assert any(x["file"] == "videos/" + marked.name and "watermark" in x["reason"] for x in index["skipped"])
    assert {it["file"] for it in load_items(s, index)["shots"]} == {"videos/" + clean.name}
    # the operator can still allow it (a game HUD taken for a logo, footage they own)
    write_json(marked.with_name(marked.name + ".json"), {"allow_overlay": True})
    again = build_index(s)
    assert any(sh["ok"] for v in again["videos"] if v["file"] == "videos/" + marked.name for sh in v["shots"])
    assert not again["skipped"]


def test_shots_where_the_vision_model_saw_subtitles_are_not_used(project):
    from gta_factory.app.core.utils import which, write_json
    from gta_factory.modules.footage.autotag import clean_answer
    from gta_factory.modules.footage.library import autotags_path, build_index, load_items, shot_key
    from gta_factory.modules.footage.vocabulary import load_vocabulary
    from test_footage import make_trailer

    s = project["settings"]
    vocab = load_vocabulary(s)
    assert clean_answer({"tags": [], "overlay": True}, vocab)["overlay"] is True
    assert clean_answer({"tags": [], "overlay": "false"}, vocab)["overlay"] is False
    assert clean_answer({"tags": []}, vocab)["overlay"] is False
    if not which("ffmpeg"):
        return
    make_trailer(project["root"] / "assets" / "gta6_footage" / "videos" / "Grand Theft Auto VI Trailer 1 [QdBZY2fkU-0].mp4")
    index = build_index(s)
    v = index["videos"][0]
    usable = [sh for sh in v["shots"] if sh["ok"]]
    before = len(load_items(s, index)["shots"])
    write_json(autotags_path(s), {"items": {shot_key(v["file"], v["size"], usable[0]): {"tags": [], "objects": [], "caption": "", "overlay": True}}})
    assert len(load_items(s, index)["shots"]) == before - 1
