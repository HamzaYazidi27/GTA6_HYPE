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
    bad = lint_script(_sections(["C2"], hook="Xbox and DJ Khaled offered fans doctor's notes for launch day."), fc, [], "", min_words=1, max_words=999, cta_max_words=14)
    assert "off_topic_hook" in _types(bad)
    good = lint_script(_sections(["C1"]), fc, [], "", min_words=1, max_words=999, cta_max_words=14)
    assert "off_topic_hook" not in _types(good)


def test_generic_wrap_up_and_long_short_cta_are_flagged():
    secs = _sections(["C1"], cta="So tell me down below what you think about all of this and which tools you would like to see used next?")
    secs[2]["narration"] = "This approach balances innovation with tradition, shaping how future games evolve."
    issues = lint_script(secs, [CORE, SIDE], [], "", min_words=1, max_words=999, cta_max_words=14)
    assert {"banned_phrase", "cta_too_long"} <= _types(issues)
    assert "cta_too_long" not in _types(lint_script(secs, [CORE, SIDE], [], "", min_words=1, max_words=999, cta_max_words=25))  # 16:9 allows a longer question


def test_sentences_too_long_to_follow_by_ear_are_flagged():
    secs = _sections(["C1"])
    secs[1]["narration"] = ("Insider Gaming reports that the chief executive of the company explained during a long call with investors that every single street "
                            "and every building of the new state was placed by hand by artists rather than produced by any kind of automated tool.")
    assert "long_sentence" in _types(lint_script(secs, [CORE], [], "", min_words=1, max_words=999, max_sentence_words=30))
    assert "long_sentence" not in _types(lint_script(_sections(["C1"]), [CORE], [], "", min_words=1, max_words=999, max_sentence_words=30))


def test_every_format_has_a_rhythm_profile(project):
    s = project["settings"]
    long_rh, short_rh = s.rhythm("long"), s.rhythm("short")
    for fmt in ("breaking", "recap", "guide", "analysis", "details", "versus", "toplist"):
        assert s.rhythm(fmt) == long_rh
    assert short_rh["max_scene_seconds"] < long_rh["max_scene_seconds"] <= 9
    assert long_rh["scene_gap_seconds"] < 0.28 and long_rh["tail_seconds"] < 1.6  # tighter than before for 16:9 too
    assert long_rh["outro_seconds"] >= 5.0  # YouTube end screens need the last 5 s
    assert long_rh["voice_speed_factor"] > 1.0


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
    assert 2.4 <= short["scenes"][-1]["duration"] <= 2.7  # end card ~2.5 s, not 5.5 s
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


def test_long_form_scenes_are_split_too(project):
    sentences = [f"Sentence number {i} adds one more concrete detail about the map, the cars and the people of Leonida." for i in range(1, 9)]
    script = {"sections": [{"id": "hook", "narration": sentences[0], "claim_ids": []}, {"id": "story", "narration": " ".join(sentences[1:7]), "claim_ids": []}, {"id": "cta", "narration": sentences[7], "claim_ids": []}]}

    class OneCard:
        def complete_json(self, system, prompt, **kw):
            return {"scenes": [{"sentences": [1, 1], "template": "kinetic", "data": {"lines": ["LEONIDA"]}}, {"sentences": [2, 7], "template": "kinetic", "data": {"lines": ["THE MAP"]}}, {"sentences": [8, 8], "template": "outro", "data": {"question": "Which detail matters most?"}}]}

    scenes = plan_scenes(OneCard(), project["settings"], script=script, fact_check=[], quotes=[], articles=[], fmt="long", today="2026-11-18")
    limit = project["settings"].rhythm("long")["max_scene_seconds"] * 165 / 60
    assert all(len(sc["narration"].split()) <= max(limit, 19) for sc in scenes)  # one 18-word sentence per scene at most here
    assert len(scenes) >= 7



# ----------------------------------------------------------------- a fast Short: few text cards, clips before screenshots
class _CardHeavy:
    def complete_json(self, system, prompt, **kw):
        assert "FAST 9:16 SHORT" in prompt
        claim = {"claim": "Generative AI has zero part in GTA 6", "status": "reported", "source": "GamesRadar+"}
        return {"scenes": [
            {"sentences": [1, 1], "template": "kinetic", "data": {"lines": ["ZERO PART"]}},
            {"sentences": [2, 2], "template": "title", "data": {"title": "Take-Two on AI"}},
            {"sentences": [3, 3], "template": "headline", "data": {"publisher": "Insider Gaming", "headline": "Take-Two Addresses AI"}},
            {"sentences": [4, 4], "template": "claim", "data": claim},
            {"sentences": [5, 5], "template": "claim", "data": claim},
            {"sentences": [6, 6], "template": "claim", "data": claim},
            {"sentences": [7, 7], "template": "outro", "data": {"question": "Does it matter to you?"}},
        ]}


def test_a_short_keeps_two_text_cards_and_no_title(project):
    from gta_factory.modules.script_generation.scenes import TEXT_CARDS

    sents = ["Generative AI has zero part in GTA 6.", "That is what Take-Two's boss says.", "Insider Gaming reports it again this week.",
             "Zelnick says Rockstar builds every street by hand.", "He says Take-Two still runs hundreds of AI pilots.", "None of them is named for GTA 6.", "Does it matter to you?"]
    script = {"sections": [{"id": "hook", "narration": sents[0], "claim_ids": []}, {"id": "story", "narration": " ".join(sents[1:6]), "claim_ids": []}, {"id": "cta", "narration": sents[6], "claim_ids": []}]}
    scenes = plan_scenes(_CardHeavy(), project["settings"], script=script, fact_check=[], quotes=[], articles=[], fmt="short", today="2026-11-18")
    cards = [sc["template"] for sc in scenes if sc["template"] in TEXT_CARDS]
    assert "title" not in cards and len(cards) == 2 and set(cards) == {"claim"}  # fact-check verdicts are kept first
    assert scenes[-1]["template"] == "outro"


def test_a_clip_beats_a_screenshot_that_matches_better():
    from gta_factory.modules.footage.select import best_item

    photo = {"kind": "photo", "key": "p", "tags": {"lucia": 3.0}}
    clip = {"kind": "shot", "key": "s", "tags": {"lucia": 2.0}}
    weak_clip = {"kind": "shot", "key": "w", "tags": {"lucia": 1.0}}
    assert best_item({"lucia": 1.0}, [photo, clip], {}, "x")[0] is clip
    assert best_item({"lucia": 1.0}, [photo, weak_clip], {}, "x")[0] is photo  # a clip that barely shows it does not win


def test_filler_cuts_follow_the_rhythm():
    from gta_factory.modules.footage.select import _Fill

    long_shot = {"type": "shot", "file": "a.mp4", "s": 0.0, "e": 12.0, "start": 0.0, "end": 6.0}
    other = {"type": "shot", "file": "b.mp4", "s": 0.0, "e": 12.0, "start": 0.0, "end": 6.0}
    fast = _Fill([long_shot, other], photo_hold=1.6, min_piece=0.9, max_hold=2.2).take(0.0, 8.0)
    assert len(fast) >= 4 and max(seg["d"] for seg in fast) <= 2.2 + 0.9 + 1e-6
    slow = _Fill([long_shot, other], photo_hold=3.0, min_piece=1.2).take(0.0, 8.0)
    assert len(slow) < len(fast)


def test_own_gameplay_captures_keep_their_hud(project, tmp_path):
    from gta_factory.app.core.utils import read_json
    from gta_factory.modules.footage.library import add_files, sidecar_path

    clip = tmp_path / "my_gameplay_vice_city.mp4"
    clip.write_bytes(b"not really a video")
    added = add_files(project["settings"], [str(clip)], own=True, log=lambda m: None)
    side = read_json(sidecar_path(added[0]), {})
    assert side.get("allow_overlay") is True and side.get("own_capture") is True
    other = tmp_path / "someone_else.mp4"
    other.write_bytes(b"x")
    assert "allow_overlay" not in (read_json(sidecar_path(add_files(project["settings"], [str(other)], log=lambda m: None)[0]), {}) or {})


# ----------------------------------------------------------------- re-uploads from other people's posts
def test_third_party_sources_are_recognised(project):
    from gta_factory.modules.footage.library import third_party

    s = project["settings"]
    assert third_party(s, {"source_url": "https://x.com/LeonidaMan/status/1", "uploader": "LeonidaMan"}) == "LeonidaMan on x.com"
    assert third_party(s, {"source_url": "https://www.tiktok.com/@fan/video/2"}) == "tiktok.com"
    assert third_party(s, {"source_url": "https://www.youtube.com/watch?v=QdBZY2fkU-0"}) is None  # an official trailer (footage.sources)
    assert third_party(s, {"source_url": "https://www.youtube.com/watch?v=zz", "uploader": "Rockstar Games"}) is None
    assert third_party(s, {"source_url": "https://media.rockstargames.com/shot.jpg"}) is None  # a primary source's own site
    assert third_party(s, {"source_url": "https://x.com/me/status/3", "own_capture": True}) is None
    assert third_party(s, {}) is None  # a local file: judged by its picture, not by its origin


def test_a_post_from_x_is_not_used_unless_allowed(project):
    from gta_factory.app.core.utils import which, write_json
    from gta_factory.modules.footage.library import build_index, load_items
    from test_footage import make_trailer

    if not which("ffmpeg"):
        return
    root = project["root"] / "assets" / "gta6_footage"
    clip = make_trailer(root / "videos" / "LeonidaMan gameplay [123].mp4")
    write_json(clip.with_name(clip.name + ".json"), {"source_url": "https://x.com/LeonidaMan/status/123", "uploader": "LeonidaMan"})
    s = project["settings"]
    index = build_index(s)
    assert not any(sh["ok"] for sh in index["videos"][0]["shots"])
    assert any("third-party post (LeonidaMan on x.com)" in x["reason"] for x in index["skipped"])
    assert load_items(s, index)["shots"] == []
    write_json(clip.with_name(clip.name + ".json"), {"source_url": "https://x.com/LeonidaMan/status/123", "uploader": "LeonidaMan", "allow_third_party": True})
    assert any(sh["ok"] for sh in build_index(s)["videos"][0]["shots"])


def test_a_post_shown_during_part_of_a_video_removes_only_those_shots(project):
    import subprocess

    from gta_factory.app.core.utils import which
    from gta_factory.modules.footage.library import build_index
    from test_footage import make_trailer

    if not which("ffmpeg"):
        return
    root = project["root"] / "assets" / "gta6_footage" / "videos"
    src = make_trailer(project["root"] / "src.mp4")
    marked = root / "compilation.mp4"
    root.mkdir(parents=True, exist_ok=True)
    # a post's interface (name, "Follow" pill, caption) at the bottom-left, from 8 s to 22 s only
    ui = ",".join([
        "drawbox=x=20:y=290:w=110:h=16:color=white@0.7:t=2:enable='between(t,8,22)'",
        "drawbox=x=140:y=288:w=56:h=20:color=white@0.7:t=2:enable='between(t,8,22)'",
        "drawbox=x=20:y=318:w=300:h=10:color=white@0.6:t=2:enable='between(t,8,22)'",
    ])
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", ui, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", str(marked)], check=True)
    v = build_index(project["settings"])["videos"][0]
    assert v["overlay"] is None and v["overlay_spans"], v["overlay_spans"]
    span = v["overlay_spans"][0]
    assert span["s"] < 12 and span["e"] > 16 and span["e"] < 30
    inside = [sh for sh in v["shots"] if sh["s"] >= 9 and sh["e"] <= 21]
    outside = [sh for sh in v["shots"] if sh["e"] <= 6.5 or sh["s"] >= 31]
    assert inside and not any(sh["ok"] for sh in inside)
    assert any(sh["ok"] for sh in outside)


def test_picked_footage_is_checked_and_picked_again(project, monkeypatch):
    from gta_factory.modules.footage import autotag as at
    from gta_factory.modules.footage.select import assign_footage
    from test_footage import footage_project as _fp, scenes_of  # noqa: F401

    from gta_factory.app.core.utils import read_json, which, write_json
    from gta_factory.modules.footage.library import autotags_path, build_index
    from test_footage import make_photo, make_trailer

    if not which("ffmpeg"):
        return
    s = project["settings"]
    root = s.root / "assets" / "gta6_footage"
    make_trailer(root / "videos" / "Grand Theft Auto VI Trailer 1 [QdBZY2fkU-0].mp4")
    make_photo(root / "photos" / "lucia_beach_01.jpg", "0xF2C14E")
    build_index(s)
    # the vision model "sees" a post's interface on every shot of the trailer's first half
    monkeypatch.setattr(at, "vision_status", lambda st: (True, "test"))
    monkeypatch.setattr(at, "ollama_tagger", lambda st, model=None: (lambda image, prompt: {"tags": [], "objects": [], "caption": "", "overlay": True}))
    monkeypatch.setattr(at, "frame_jpeg", lambda path, t=None, width=768: b"jpeg")
    checker = at.overlay_checker(s, log=lambda m: None)
    assert checker is not None
    scenes = scenes_of([("Meet Lucia on the beach.", "kinetic"), ("The trailer shows the city at night.", "kinetic"), ("And a lot more.", "kinetic")])
    out = assign_footage(s, scenes, checker=checker)
    saved = read_json(autotags_path(s), {})["items"]
    assert saved and all(e["overlay"] for e in saved.values())  # everything picked the first time was checked
    used_now = {it for sc in scenes for it in ([sc["footage"]["file"]] if sc.get("footage", {}).get("kind") == "photo" else [])}
    assert out["summary"]["share"] <= 1.0 and not used_now  # all flagged: nothing with an overlay is shown
    assert checker(s, []) == 0


# ----------------------------------------------------------------- script and screen text
def test_hook_text_is_wrapped_never_cut_with_an_ellipsis():
    from gta_factory.modules.visual_research.catalog import kinetic_lines, n_kinetic

    assert kinetic_lines(["GTA 6's save system limits manual saves during missions"]) == ["GTA 6'S SAVE SYSTEM LIMITS", "MANUAL SAVES DURING", "MISSIONS"]
    assert kinetic_lines(["AllThingsHow staff reports the"]) == ["ALLTHINGSHOW STAFF REPORTS"]
    assert kinetic_lines(["AI:", "a mixed term"]) == ["AI:", "A MIXED TERM"]
    out = n_kinetic({"lines": ["one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen"], "accent": "three"})
    assert len(out["lines"]) == 3 and not any("…" in ln for ln in out["lines"]) and out["accent"] == "THREE"


def test_the_same_name_twice_in_a_sentence_is_flagged():
    from gta_factory.modules.script_generation.writer import repeated_phrase

    secs = _sections(["C1"], hook="According to AllThingsHow Staff, AllThingsHow Staff reports the save option is grayed out.")
    assert "repetition" in _types(lint_script(secs, [CORE], [], "", min_words=1, max_words=999))
    assert repeated_phrase("Red Dead 2 had witnesses, and Red Dead 2 had bounties.") == "red dead"
    assert repeated_phrase("Rockstar Games says the game launches on November 19.") is None


def test_breaking_needs_a_reported_claim(project):
    fresh = [{"publisher": "AllThingsHow", "published_at": "2026-11-18", "title": "GTA 6 saves", "author": "x"}]
    spec = [{"id": "C1", "claim": "Saves are grayed out in missions.", "classification": "speculation", "confidence": "low", "original_publisher": "AllThingsHow", "original_source": "https://x"}]

    class Planner:
        def complete_json(self, system, prompt, **kw):
            return {"scenes": [{"sentences": [1, 1], "template": "kinetic", "data": {"lines": ["NO SAVES"], "kicker": "Breaking"}, "claim_ids": ["C1"]},
                               {"sentences": [2, 2], "template": "outro", "data": {"question": "How will you save?"}}]}

    script = {"sections": [{"id": "hook", "narration": "Manual saves are grayed out during missions.", "claim_ids": ["C1"]}, {"id": "cta", "narration": "How will you save?", "claim_ids": []}]}
    scenes = plan_scenes(Planner(), project["settings"], script=script, fact_check=spec, quotes=[], articles=fresh, fmt="short", today="2026-11-19")
    assert scenes[0]["data"]["kicker"] == ""  # player reports are not breaking news
    reported = [{**spec[0], "classification": "reported"}]
    scenes = plan_scenes(Planner(), project["settings"], script=script, fact_check=reported, quotes=[], articles=fresh, fmt="short", today="2026-11-19")
    assert scenes[0]["data"]["kicker"] == "Breaking"


# ----------------------------------------------------------------- fresh: the next video to make
def _entries():
    from gta_factory.modules.fresh import Entry

    return [
        Entry("GTA 6 patch 1.02 fixes the save bug in missions", "https://support.rockstargames.com/articles/patch-102", "Rockstar Support", "support.rockstargames.com", "primary", "2026-11-19T08:00:00+00:00", "feed"),
        Entry("GTA 6 patch 1.02 fixes save bug during missions, Rockstar says", "https://www.videogameschronicle.com/news/gta-6-patch-102/", "VGC", "videogameschronicle.com", "secondary", "2026-11-19T09:00:00+00:00", "feed"),
        Entry("Someone found Bigfoot in GTA 6 already", "https://www.reddit.com/r/GTA6/comments/abc/bigfoot/", "r/GTA6 rising", "reddit.com", "community", "2026-11-19T10:00:00+00:00", "community", "", False),
        Entry("GTA 6 Bigfoot sightings ramp up as Rockstar honors a legend", "https://www.gamesradar.com/games/gta-6-bigfoot/", "GamesRadar", "gamesradar.com", "secondary", "2026-11-19T07:00:00+00:00", "feed"),
        Entry("Best GTA 6 heist payouts: every score ranked", "https://allthings.how/gta-6-heist-payouts/", "AllThingsHow", "allthings.how", "other", "2026-11-19T06:00:00+00:00", "bing_news_rss"),
        Entry("Here's every GTA 6 mission and chapter", "https://www.gamesradar.com/games/gta-6-mission-list/", "GamesRadar", "gamesradar.com", "secondary", "2026-11-19T05:00:00+00:00", "feed"),
        Entry("Clair Obscur writer has left Sandfall", "https://insider-gaming.com/clair-obscur/", "Insider Gaming", "insider-gaming.com", "secondary", "2026-11-19T09:00:00+00:00", "feed"),
        Entry("GTA 6 trailer 1 breakdown", "https://www.ign.com/old", "IGN", "ign.com", "secondary", "2026-11-10T09:00:00+00:00", "feed"),
    ]


def test_fresh_sorts_groups_and_ranks_the_stories(project):
    from datetime import datetime, timezone

    from gta_factory.modules.fresh import fresh_stories, render_text

    s = project["settings"]
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    out = fresh_stories(s, entries=_entries(), covered=[], used_urls=set(), now=now, log=lambda m: None)
    assert set(out) == {"news", "secrets", "missions", "heists"}  # the non-GTA story and the 9-day-old one are gone
    patch = out["news"][0]
    assert len(patch.entries) == 2 and patch.entries[0].tier == "primary"  # Rockstar Support + VGC: one story
    assert patch.command.startswith('python -m gta_factory new "https://support.rockstargames.com/') and "--format short" in patch.command
    bigfoot = out["secrets"][0]
    assert {e.tier for e in bigfoot.entries} == {"secondary", "community"} and "gamesradar.com" in bigfoot.command and "reddit" not in bigfoot.command
    assert out["missions"][0].command.endswith("--format guide")
    assert "RUMOR" in out["heists"][0].note  # only a site outside the reputable list
    assert patch.score > out["heists"][0].score
    text = render_text(out, s.get("fresh.categories"))
    assert "Secrets" in text and "community: https://www.reddit.com/r/GTA6/comments/abc/bigfoot/" in text


def test_fresh_marks_what_the_channel_already_made(project):
    from datetime import datetime, timezone

    from gta_factory.modules.fresh import fresh_stories

    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    covered = [{"job_id": "20261119-x", "headline": "GTA 6 patch 1.02 fixes the save bug in missions"}]
    out = fresh_stories(project["settings"], entries=_entries(), covered=covered, used_urls={"https://www.gamesradar.com/games/gta-6-mission-list"}, now=now, log=lambda m: None)
    assert out["news"][0].covered_before == "20261119-x" and out["missions"][0].covered_before
    assert all(st.score < 40 for st in (out["news"][0], out["missions"][0]))


def test_fresh_reads_feeds_community_and_news_search(project):
    from types import SimpleNamespace

    from gta_factory.modules.fresh import read_sources

    s = project["settings"]

    def feeds(url, timeout):
        if "reddit" in url:
            return [{"title": "Hidden UFO in GTA 6", "link": "https://www.reddit.com/r/GTA6/comments/u/", "updated": "2026-11-19T10:00:00+00:00"}]
        if "polygon" in url:
            raise OSError("down")
        return [{"title": "GTA 6 map size compared", "link": "https://www.ign.com/articles/gta-6-map?utm=x", "published": "Thu, 19 Nov 2026 09:00:00 GMT", "summary": "<p>Leonida</p>"}]

    def search(provider, query):
        return [SimpleNamespace(title=f"{query} news", url="https://news.google.com/x", publisher="Kotaku", domain="kotaku.com", published_at=None, snippet="", direct=False)] if provider == "google_news_rss" else []

    got = read_sources(s, log=lambda m: None, feed_entries=feeds, search=search)
    tiers = {e.tier for e in got}
    assert {"secondary", "community"} <= tiers
    assert any(e.url.startswith("https://www.ign.com/articles/gta-6-map") and e.published_at for e in got)
    assert any(e.provider == "google_news_rss" and not e.direct for e in got)


# ----------------------------------------------------------------- the articles' own pictures and videos
PAGE = """<html><head><meta property="og:image" content="https://cdn.example.com/gta-6-save-menu-1920-80.jpg"></head><body>
<article>
<figure><picture><source srcset="https://cdn.example.com/gta6-map-island-480.jpg 480w, https://cdn.example.com/gta6-map-island-1600.jpg 1600w">
<img src="https://cdn.example.com/gta6-map-island-480.jpg" alt="The GTA 6 map near the Leonida Keys"></picture><figcaption>The Keys (Image credit: Rockstar Games)</figcaption></figure>
<figure><img src="https://cdn.example.com/zelnick-stage-1600.jpg" width="1600" alt="Strauss Zelnick on stage"><figcaption>(Image credit: Getty Images)</figcaption></figure>
<p>Text.</p><img src="https://cdn.example.com/author-avatar-140.jpg" width="140" alt="Writer">
<iframe src="https://www.youtube.com/embed/QdBZY2fkU-0"></iframe>
<blockquote class="twitter-tweet"><p>wow</p><a href="https://twitter.com/RandomFan/status/1">link</a></blockquote>
<blockquote class="twitter-tweet"><a href="https://twitter.com/RockstarGames/status/2">link</a></blockquote>
</article>
<aside><img src="https://cdn.example.com/gta-5-cheats-1600.jpg" width="1600" alt="GTA 5 cheats"></aside>
</body></html>"""


def test_article_media_found_and_sorted_by_who_owns_it(monkeypatch):
    from gta_factory.modules.article_extraction import media as m

    monkeypatch.setattr(m, "main_content_pictures", lambda html, url: {"gta6-map-island", "zelnick-stage", "author-avatar-140"})
    found = m.find_media(PAGE, "https://www.gamesradar.com/gta-6-saves/")
    by = {x["url"]: x for x in found}
    assert "https://cdn.example.com/gta-5-cheats-1600.jpg" not in by  # not in the article's text
    island = by["https://cdn.example.com/gta6-map-island-1600.jpg"]  # the widest <source>
    assert island["credit"] == "Rockstar Games" and "Leonida Keys" in island["alt"]
    decide = {x["url"]: m.media_policy(x, article_tier="secondary", publisher="GamesRadar+")[0] for x in found}
    assert decide["https://cdn.example.com/gta-6-save-menu-1920-80.jpg"] == "article"  # the lead picture, from a reputable outlet
    assert decide["https://cdn.example.com/gta6-map-island-1600.jpg"] == "official"
    assert decide["https://cdn.example.com/zelnick-stage-1600.jpg"] == "skip"  # press-agency photo
    assert decide["https://www.youtube.com/watch?v=QdBZY2fkU-0"] == "check_channel"
    assert decide["https://twitter.com/RandomFan/status/1"] == "skip" and decide["https://twitter.com/RockstarGames/status/2"] == "official"
    assert m.media_policy(island, article_tier="other", publisher="AllThingsHow")[0] == "official"  # Rockstar's picture is Rockstar's anywhere
    lead = by["https://cdn.example.com/gta-6-save-menu-1920-80.jpg"]
    assert m.media_policy(lead, article_tier="other", publisher="AllThingsHow")[0] == "skip"
    assert m.media_policy(lead, article_tier="other", publisher="AllThingsHow", third_party_ok=True)[0] == "third_party"


def test_article_media_downloaded_for_its_job_only(project, monkeypatch, tmp_path):
    import subprocess
    from types import SimpleNamespace

    from gta_factory.app.core.utils import which
    from gta_factory.modules.article_extraction import media as m
    from gta_factory.modules.footage.library import build_index, load_items

    if not which("ffmpeg"):
        return
    s = project["settings"]
    monkeypatch.setattr(m, "main_content_pictures", lambda html, url: {"gta6-map-island", "zelnick-stage"})
    art = SimpleNamespace(url="https://www.gamesradar.com/gta-6-saves/", publisher="GamesRadar+", source_tier="secondary", media=m.find_media(PAGE, "https://www.gamesradar.com/gta-6-saves/"))

    def fake_download(url, dest, *, referer, max_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        out = dest.with_suffix(".jpg")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720", "-frames:v", "1", str(out)], check=True)
        return out

    report = m.download_article_media(s, [art], job_id="job-a", download=fake_download, youtube_uploader=lambda u: "Rockstar Games", fetch_videos=lambda st, url, folder: [], log=lambda x: None)
    files = {r["url"]: r for r in report["used"]}
    assert set(files) == {"https://cdn.example.com/gta-6-save-menu-1920-80.jpg", "https://cdn.example.com/gta6-map-island-1600.jpg"}
    assert files["https://cdn.example.com/gta6-map-island-1600.jpg"]["credit"] == "Image: Rockstar Games"
    reasons = " ".join(r["reason"] for r in report["left_out"])
    assert "press-agency" in reasons and "someone else's x post" in reasons
    idx = build_index(s)
    mine = [p for p in load_items(s, idx, job="job-a")["photos"] if p.get("story")]
    assert len(mine) == 2 and any("leonida_keys" in p["tags"] for p in mine)
    assert not [p for p in load_items(s, idx, job="job-b")["photos"] if "articles/" in p["file"]]  # another video never shows them


def test_article_pictures_go_where_the_narration_talks_about_them():
    from gta_factory.modules.footage.select import story_scenes

    island = {"kind": "photo", "key": "a", "caption": "The GTA 6 map near the Leonida Keys, south islands", "tags": ["leonida_keys"], "role": ""}
    lead = {"kind": "photo", "key": "b", "caption": "", "tags": [], "role": "lead"}
    scenes = [{"scene_number": 1, "narration": "Rockstar just changed how saving works."},
              {"scene_number": 2, "narration": "Patch notes mention missions."},
              {"scene_number": 3, "narration": "Down in the Leonida Keys, the south islands hide a new save spot."}]
    out = story_scenes([island, lead], scenes, free_first={1, 2})
    assert out[3] is island and out[1] is lead


def test_article_pictures_with_strong_language_are_left_out():
    from gta_factory.modules.article_extraction.media import media_policy

    meme = {"kind": "image", "url": "https://cdn.example.com/gta-6-meme-900.jpg", "width": 900, "alt": "GTA 6 in-game meme saying &quot;yeah i like Ass&quot;", "caption": "(Image credit: Rockstar Games)", "credit": "Rockstar Games"}
    decision, reason, _ = media_policy(meme, article_tier="secondary", publisher="GamesRadar+")
    assert decision == "skip" and "advertiser-friendly" in reason
    clean = {**meme, "alt": "GTA 6 police at night in Vice City"}
    assert media_policy(clean, article_tier="secondary", publisher="GamesRadar+")[0] == "official"
    assert media_policy({**meme, "alt": "GTA 6 class reunion"}, article_tier="secondary", publisher="GamesRadar+")[0] == "official"  # "class" is not "ass"


# ----------------------------------------------------------------- guides: every step's picture, in order
GUIDE = """<html><head><meta property="og:image" content="https://images.example.com/aaa111/1280x720.jpg"></head><body><article>
<h2>GTA 6 Tips</h2><p>Twelve tips that will make your first hours in Leonida much easier to handle.</p>
<h3 id="fast"><a href="#fast">How Do You Fast Travel?</a></h3><p>There is no traditional fast travel in GTA 6, but there is a way around the map.</p>
<figure class="picture"><a title="How Do You Fast Travel in GTA 6? 1" href="https://images.example.com/bbb222/large.jpg"><img src="https://images.example.com/bbb222/1280x720.jpg" width="900" height="506.25" alt="How Do You Fast Travel in GTA 6? 1"></a></figure>
<figure class="picture"><a title="How Do You Fast Travel in GTA 6? 2" href="https://images.example.com/ccc333/how-do-you-fast-travel-in-gta-6-2.large.jpg"><img src="https://images.example.com/ccc333/how-do-you-fast-travel-in-gta-6-2.900x.jpg" width="900" alt="How Do You Fast Travel in GTA 6? 2"></a></figure>
<figure class="picture"><a title="x" href="https://images.example.com/ddd444/large.jpg"><img src="https://images.example.com/ddd444/1280x720.jpg" width="900" alt="How Do You Fast Travel in GTA 6? 3"></a></figure>
</article></body></html>"""


def test_generic_picture_names_and_large_versions(monkeypatch):
    from gta_factory.modules.article_extraction import media as m

    assert m._key("https://images.example.com/c74b28fd31bb6/1280x720.jpg") == m._key("https://images.example.com/c74b28fd31bb6/large.jpg") == "c74b28fd31bb6"
    assert m._key("https://x/a/how-x-2.900x.jpg") == m._key("https://x/a/how-x-2.large.jpg") == "how-x-2"
    assert m._key("https://cdn/x/hDEp-1920-80.jpg.webp") == "hdep"
    monkeypatch.setattr(m, "main_content_pictures", lambda html, url: {"aaa111", "bbb222", "how-do-you-fast-travel-in-gta-6-2", "ddd444"})
    found = [x for x in m.find_media(GUIDE, "https://www.pushsquare.com/guides/x") if x["kind"] == "image"]
    urls = [x["url"] for x in found]
    assert "https://images.example.com/bbb222/large.jpg" in urls and "https://images.example.com/ddd444/large.jpg" in urls  # two "large.jpg", two pictures
    assert "https://images.example.com/ccc333/how-do-you-fast-travel-in-gta-6-2.large.jpg" in urls
    assert all(x["width"] >= 1600 for x in found if x.get("role") != "lead")


def test_guide_headings_come_back_into_the_text():
    from gta_factory.modules.article_extraction.extractor import restore_headings

    text = "Twelve tips that will make your first hours in Leonida much easier to handle.\n\nThere is no traditional fast travel in GTA 6, but there is a way around the map."
    out = restore_headings(GUIDE, text)
    assert "How Do You Fast Travel?\n\nThere is no traditional fast travel" in out and out.count("GTA 6 Tips") == 1


def test_step_pictures_follow_each_other_on_the_next_scenes():
    from gta_factory.modules.footage.select import story_scenes

    steps = [{"kind": "photo", "key": f"p{i}", "caption": f"How Do You Fast Travel in GTA 6? {i}", "tags": [], "role": ""} for i in (3, 1, 2)]
    scenes = [{"scene_number": 4, "narration": "Money matters: keep it in the bank."},
              {"scene_number": 5, "narration": "There is no fast travel, but you can travel fast with a RydeMe car."},
              {"scene_number": 6, "narration": "Open the app on your phone and book a ride."},
              {"scene_number": 7, "narration": "Hold Triangle to skip the trip for a hundred dollars."}]
    out = story_scenes(steps, scenes)
    assert [out[n]["caption"][-1] for n in (5, 6, 7)] == ["1", "2", "3"] and 4 not in out


def test_a_short_keeps_few_article_pictures_a_guide_many(project, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from gta_factory.app.core.utils import which
    from gta_factory.modules.article_extraction import media as m

    if not which("ffmpeg"):
        return
    monkeypatch.setattr(m, "main_content_pictures", lambda html, url: {"aaa111", "bbb222", "how-do-you-fast-travel-in-gta-6-2", "ddd444"})
    art = SimpleNamespace(url="https://www.pushsquare.com/guides/x", publisher="Push Square", source_tier="secondary", media=m.find_media(GUIDE, "https://www.pushsquare.com/guides/x"))

    def fake(url, dest, *, referer, max_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        out = dest.with_suffix(".jpg")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720", "-frames:v", "1", str(out)], check=True)
        return out

    few = m.download_article_media(project["settings"], [art], job_id="short-job", download=fake, max_pictures=2, log=lambda x: None)
    assert len(few["used"]) == 2 and any("more than 2 pictures" in r["reason"] for r in few["left_out"])
    many = m.download_article_media(project["settings"], [art], job_id="guide-job", download=fake, max_pictures=30, log=lambda x: None)
    assert len(many["used"]) == 3  # the three step pictures; the lead picture says nothing about GTA (could be anyone's photo)
    assert any("not clearly a GTA 6 picture" in r["reason"] for r in many["left_out"])


def test_a_guide_written_one_story_per_tip_is_merged_not_rejected(project):
    from gta_factory.modules.script_generation.writer import merge_repeated_sections, write_script

    ids = ["hook", "context", "story", "story", "story", "story", "evidence", "evidence", "analysis", "analysis", "analysis", "community", "conclusion", "cta"]
    merged = merge_repeated_sections([{"id": i, "narration": f"Tip text number {k} about GTA 6 for {i}.", "claim_ids": [f"C{k % 3 + 1}"]} for k, i in enumerate(ids)])
    assert [x["id"] for x in merged] == ["hook", "context", "story", "evidence", "analysis", "community", "conclusion", "cta"]
    assert merged[2]["narration"].count("Tip text number") == 4 and merged[2]["claim_ids"] == ["C3", "C1", "C2"]

    class LocalModel:  # writes the guide the way the local model did, then audits it clean
        def complete_json(self, system, prompt, *, validate=None, label="", **kw):
            if label.startswith("audit"):
                return {"issues": [], "verdict": "pass", "sentences": []}
            secs = [{"id": i, "narration": f"Step {k} of the guide: open the phone and pick the right app, then follow the marker on the map.", "claim_ids": []} for k, i in enumerate(ids)]
            data = {"title": "12 GTA 6 tips", "sections": secs}
            assert validate is None or validate(data) is None
            return data

    fc = [{"id": "C1", "claim": "RydeMe lets you skip a trip for $100.", "classification": "reported", "confidence": "medium", "original_publisher": "Push Square", "original_source": "https://x", "evidence": "", "relevance": "core"}]
    out = write_script(LocalModel(), project["settings"], fmt="guide", target_seconds=60, analysis={"story_headline": "GTA 6 tips", "quotes": []}, fact_check=fc,
                       verification={}, story={"angle": {}, "working_title": "GTA 6 tips"}, timeline=[], today="2026-11-19")
    assert [x["id"] for x in out["sections"]] == ["hook", "context", "story", "evidence", "analysis", "community", "conclusion", "cta"]
