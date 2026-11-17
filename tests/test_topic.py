"""Comparisons ("GTA 6 vs Red Dead 2") and top lists: games, sources, fact-check, outline, script, split screens.

The LLM is scripted (``fakes.topic_answer``); the two "previews" are fictional fixtures
read from text files, so nothing touches the network.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO, RENDER, job_options
from fakes import TOPIC_TEXTS, TOPIC_URLS
from gta_factory.app.core.errors import ConfigError
from gta_factory.app.core.pipeline import create_topic_job, run_job
from gta_factory.app.core.utils import read_json, which, write_json
from gta_factory.app.database.db import get_db
from gta_factory.modules.footage import library as lib
from gta_factory.modules.footage.games import game, game_settings, load_facts, resolve, side_of
from gta_factory.modules.footage.vocabulary import load_vocabulary
from gta_factory.modules.topic.scenes import compare_panels
from test_footage import footage_project, make_trailer  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(not which("ffmpeg"), reason="needs ffmpeg")


@pytest.fixture()
def topic_project(footage_project, tmp_path):
    """GTA 6 footage, a Red Dead 2 library, the games' facts files and two fictional previews."""
    root = footage_project["root"]
    shutil.copytree(REPO / "knowledge" / "games", root / "knowledge" / "games")
    rdr = make_trailer(root / "assets" / "game_footage" / "rdr2" / "videos" / "Red Dead Redemption 2 Trailer [gmA6MrX81z4].mp4")
    write_json(rdr.with_name(rdr.name + ".json"), {"tags": ["horse"]})
    files = {}
    for i, (url, text) in enumerate(zip(TOPIC_URLS, TOPIC_TEXTS)):
        f = tmp_path / f"preview_{i}.txt"
        f.write_text(text)
        files[url] = str(f)
    footage_project["topic_files"] = files
    return footage_project


def topic_options(p: dict, **extra) -> dict:
    return job_options(p, **{"text_files": p["topic_files"], "target_seconds": 88, **extra})


# ----------------------------------------------------------------- games
def test_games_resolve_and_scope_the_footage_library(project):
    s = project["settings"]
    assert resolve(s, "RDR 2") == resolve(s, "red dead") == resolve(s, "Red Dead Redemption 2") == "rdr2"
    assert resolve(s, "GTA V") == resolve(s, "gta5") == "gta5" and resolve(s, None) == resolve(s, "GTA VI") == "gta6"
    with pytest.raises(ConfigError, match="Unknown game"):
        resolve(s, "mario kart")
    gs = game_settings(s, "rdr2")
    assert lib.footage_dir(gs) == s.root / "assets" / "game_footage" / "rdr2" and lib.footage_dir(s) == s.root / "assets" / "gta6_footage"
    assert gs.get("footage.sources")[0].endswith("gmA6MrX81z4") and gs.get("footage")["dir"] == "assets/game_footage/rdr2"
    assert gs.jobs_dir == s.jobs_dir and gs.format("versus")["topic_only"]
    rdr, gta = game(s, "rdr2"), game(s, "gta6")
    assert side_of("In Red Dead 2, Arthur carries two sidearms.", rdr, gta) == "other"
    assert side_of("In GTA 6, Lucia carries two long guns.", rdr, gta) == "gta6"
    assert side_of("Red Dead 2 and GTA 6 both keep you light.", rdr, gta) == "both"
    assert side_of("Then there is how you aim.", rdr, gta) is None
    assert side_of("GTA V had five stars.", game(s, "gta5"), gta) == "other"  # "GTA V" is not "GTA VI"


def test_game_facts_and_vocabulary(project):
    s = project["settings"]
    with pytest.raises(ConfigError, match="No facts file"):
        load_facts(s, "rdr2")
    shutil.copytree(REPO / "knowledge" / "games", project["root"] / "knowledge" / "games")
    facts = load_facts(s, "rdr2")
    assert [f["id"] for f in facts[:3]] == ["G1", "G2", "G3"] and all(f["fact"] and f["about"] for f in facts)
    assert len(load_facts(s, "gta5")) >= 15
    # the other game's library knows its own characters and never offers GTA 6 ones to the tagger
    v = load_vocabulary(game_settings(s, "rdr2"))
    assert "arthur" in v.concepts and "horse" in v.concepts and "lucia" not in v.concepts and "police" in v.concepts
    assert "lucia" in load_vocabulary(s).concepts and "arthur" not in load_vocabulary(s).concepts


def test_compare_panels_fit_the_frame():
    for w, h in ((1920, 1080), (1080, 1920)):
        p = compare_panels(w, h)
        for x, y, pw, ph in p.values():
            assert 0 <= x and x + pw <= w and 0 <= y and y + ph <= h and pw % 2 == 0 and ph % 2 == 0
        (lx, ly, lw, lh), (rx, ry, rw, rh) = p["left"], p["right"]
        assert (lw, lh) == (rw, rh)
        assert (lx + lw <= rx) if w > h else (ly + lh <= ry)  # side by side, or stacked
    assert compare_panels(1080, 1920)["right"][1] + compare_panels(1080, 1920)["right"][3] < 1920 * 0.69  # above the Shorts captions


def test_topic_job_needs_a_known_game_and_its_facts(project):
    s = project["settings"]
    with pytest.raises(ConfigError, match="No facts file"):
        create_topic_job(s, "Red Dead 2 systems in GTA 6", vs="rdr2")
    with pytest.raises(ConfigError, match="another game"):
        create_topic_job(s, "GTA 6 vs itself", vs="gta 6")
    from gta_factory.app.core.pipeline import create_job

    with pytest.raises(ConfigError, match="comparison or a top list"):
        create_job(s, ["https://example.com/a"], fmt="versus")


# ----------------------------------------------------------------- pipeline
def test_versus_job_until_timeline(topic_project):
    s = topic_project["settings"]
    job = create_topic_job(s, "Red Dead 2 systems inside GTA 6", vs="rdr2", count=3, options=topic_options(topic_project))
    db = get_db(s.db_path)
    assert [st["name"] for st in db.get_steps(job)][:7] == ["research", "analyze", "fact_check", "outline", "script", "scenes", "rights"]
    assert job.split("-", 2)[2].startswith("vs-red-dead")
    assert run_job(s, job, until="timeline") == "paused", db.get_job(job)["error"]
    root = s.jobs_dir / job

    # every preview corroborates the other, never itself: C3 has one outside source, so it stays REPORTED
    fc = {c["id"]: c for c in read_json(root / "research" / "fact_check.json")}
    assert [x["publisher"] for x in fc["C3"]["corroborating_sources"]] == ["GamesRadar"] or len(fc["C3"]["corroborating_sources"]) == 1
    assert fc["C3"]["corroborating_sources"][0]["url"] == TOPIC_URLS[0]
    assert fc["C3"]["classification"] == "reported" and fc["C2"]["classification"] == "reported"
    assert all(c["evidence_verified"] for c in fc.values())

    outline = read_json(root / "story" / "outline.json")
    assert [it["title"] for it in outline["items"]] == ["Weapon limit", "Focus mode", "Witnesses"]  # "Made up" cites no real claim
    assert all(it["game_fact_ids"] and it["status"] == "reported" for it in outline["items"])
    assert len(read_json(root / "research" / "game_facts.json")) >= 20

    script = read_json(root / "script" / "script.json")
    assert script["kind"] == "topic" and script["vs"] == "rdr2"
    assert [x["id"] for x in script["sections"]] == ["hook", "item_01", "item_02", "item_03", "verdict", "cta"]
    assert script["quality"]["remaining_lint_issues"] == [], script["quality"]["remaining_lint_issues"]
    md = (root / "script" / "script.md").read_text()
    assert "jeu sorti" in md and "RAPPORTÉ" in md

    scenes = read_json(root / "scenes" / "scene_breakdown.json")
    templates = [sc["template"] for sc in scenes]
    assert templates[:2] == ["kinetic", "title"] and templates[-1] == "outro"
    cards = [sc for sc in scenes if sc["template"] == "compare" and sc["data"].get("n")]
    assert [c["data"]["n"] for c in cards] == [1, 2, 3] and cards[0]["data"]["left"]["game"] == "RED DEAD 2"
    assert cards[0]["data"]["right"]["status"] == "reported" and cards[0]["chapter_title"] == "Weapon limit"
    item1 = [sc for sc in scenes if sc.get("item") == 1]
    # the card, then what is said about GTA 6 over GTA 6 footage, then both games side by side again
    assert [sc["template"] for sc in item1] == ["compare", "footage", "compare"]
    assert item1[1]["footage_game"] == "gta6" and item1[1]["status"] == "reported" and "GTA 6" in item1[1]["data"]["game"]
    rdr_scenes = [sc for sc in scenes if sc.get("footage_game") == "rdr2"]
    assert rdr_scenes and all(sc["status"] is None and sc["data"]["side"] == "other" for sc in rdr_scenes)
    assert any("bounty" in sc["narration"] for sc in rdr_scenes)
    # a card stops where the narration moves from one game to the other
    card2 = next(c for c in cards if c["data"]["n"] == 2)
    assert "Dead Eye" in card2["narration"] and "focus" not in card2["narration"]

    rights = read_json(root / "assets" / "rights_review.json")
    fo = rights["footage"]
    assert fo["mode"] == "topic" and fo["share"] >= 0.95 and fo["games"]["rdr2"]["scenes"] >= len(rdr_scenes)
    assert any(a["asset_id"].startswith("rdr2-") and a.get("game") == "rdr2" for a in rights["assets"])
    assert all(sc.get("footage") for sc in scenes)
    assert all(sc.get("footage_b") for sc in scenes if sc["template"] == "compare")

    props = read_json(root / "render" / "props.json")
    public = s.root / "templates" / ".bundle" / "public"
    for sc in (x for x in props["scenes"] if x["template"] == "compare"):
        for key, side in (("footage", "right"), ("footageB", "left")):
            clip = public / sc[key]["src"]
            size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(clip)], capture_output=True, text=True).stdout.strip()
            assert size == f"{sc['data']['panels'][side][2]},{sc['data']['panels'][side][3]}"  # cut at the panel's size
    assert [c["title"] for c in props["chapters"]] == ["Intro", "Weapon limit", "Focus mode", "Witnesses", "Verdict"]
    rdr_props = [x for x in props["scenes"] if x["template"] == "footage" and x["data"]["side"] == "other"]
    assert rdr_props and all(x.get("footage") for x in rdr_props)


def test_top_list_short_until_scenes(topic_project):
    s = topic_project["settings"]
    job = create_topic_job(s, "3 things to do on day one in GTA 6", fmt="short", count=3, options=topic_options(topic_project, target_seconds=45))
    assert get_db(s.db_path).get_job(job)["format"] == "short"
    assert run_job(s, job, until="scenes") == "paused", get_db(s.db_path).get_job(job)["error"]
    root = s.jobs_dir / job
    script = read_json(root / "script" / "script.json")
    assert script["style"] == "list" and script["vs"] is None
    scenes = read_json(root / "scenes" / "scene_breakdown.json")
    cards = [sc for sc in scenes if sc["template"] == "item"]
    assert [(c["data"]["n"], c["data"]["total"], c["data"]["title"]) for c in cards] == [(1, 3, "JOIN A GYM"), (2, 3, "MASTER FOCUS"), (3, 3, "PICK YOUR SPOT")]
    assert scenes[0]["template"] == "kinetic" and scenes[0]["data"]["lines"] == ["3 THINGS", "TO DO FIRST"]
    assert not any(sc["template"] == "compare" for sc in scenes) and all(sc["footage_game"] == "gta6" for sc in scenes)
    assert cards[0]["status"] == "reported"


@pytest.mark.render
@pytest.mark.skipif(not RENDER, reason="set GTA_FACTORY_RENDER_TESTS=1 to run the Remotion render")
def test_full_versus_video(topic_project):
    s = topic_project["settings"]
    job = create_topic_job(s, "Red Dead 2 systems inside GTA 6", vs="rdr2", count=3, options=topic_options(topic_project))
    status = run_job(s, job)
    assert status in ("completed", "needs_review"), get_db(s.db_path).get_job(job)["error"]
    out = s.outputs_dir / job
    assert list(out.glob("*.mp4")) and (out / "outline.json").exists() and (out / "game_facts.json").exists()
    concepts = read_json(s.jobs_dir / job / "thumbnails" / "concepts.json")
    assert concepts[0]["props"]["concept"] == "versus" and concepts[0]["props"].get("photo") and concepts[0]["props"].get("photo2")
    assert all(Path(s.jobs_dir / job / "thumbnails" / c["file"]).stat().st_size > 10_000 for c in concepts)
    meta = read_json(out / "metadata.json")
    assert "THE COMPARISON, SYSTEM BY SYSTEM" in meta["description"] and "1. Weapon limit: Red Dead 2" in meta["description"]
    assert "gta 6 vs red dead 2" in meta["tags"]
    review = (out / "REVIEW.md").read_text()
    assert "knowledge/games/rdr2.yaml" in review and "#1 Weapon limit" in review
    qc = read_json(out / "qc_report.json")
    share = next(c for c in qc["checks"] if c["id"] == "footage.share")
    assert share["passed"], share


def test_dashboard_topic_and_other_games(topic_project, monkeypatch):
    import json
    import threading
    import urllib.error
    import urllib.request

    from gta_factory.app.dashboard import server

    started = []
    monkeypatch.setattr(server, "spawn_runner", lambda settings, job_id, **kw: started.append(job_id))
    s = topic_project["settings"]
    httpd = server.make_server(s, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(base + path, data=data, method=method, headers={"X-Factory": "1", "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read() or b"{}")

    try:
        jobs = call("GET", "/api/jobs")
        assert {g["id"] for g in jobs["games"]} == {"gta5", "rdr2"} and "versus" not in jobs["formats"]
        foot = call("GET", "/api/footage")
        rdr = next(g for g in foot["games"] if g["id"] == "rdr2")
        assert rdr["summary"]["videos"] == 1 and rdr["summary"]["usable_shots"] > 0
        img = (s.root / "assets" / "gta6_footage" / "photos" / "vice_city_night.jpg").read_bytes()
        r = call("POST", "/api/footage/upload?name=camp.jpg&tags=camp&game=rdr2", raw=img)
        assert r["file"] == "photos/camp.jpg" and (s.root / "assets" / "game_footage" / "rdr2" / "photos" / "camp.jpg").exists()
        made = call("POST", "/api/topic", {"topic": "Red Dead 2 systems inside GTA 6", "vs": "rdr2", "sources": TOPIC_URLS, "count": 3, "options": {"no_search": True, "force": True}})
        job = get_db(s.db_path).get_job(made["id"])
        assert started == [made["id"]] and job["format"] == "versus" and job["urls"] == TOPIC_URLS
        assert job["options"]["vs"] == "rdr2" and job["options"]["count"] == 3 and job["options"]["no_search"] and "force" not in job["options"]
        with pytest.raises(urllib.error.HTTPError) as e:
            call("POST", "/api/topic", {"topic": "GTA 6 vs Mario", "vs": "mario kart"})
        assert e.value.code == 400
    finally:
        httpd.shutdown()


# ----------------------------------------------------------------- the other game's footage is never missing
def test_empty_game_library_is_downloaded_before_anything_else(topic_project, monkeypatch):
    s = topic_project["settings"]
    rdr = s.root / "assets" / "game_footage" / "rdr2"
    shutil.rmtree(rdr)  # as after an interrupted install
    calls = []

    def fake_fetch(settings, urls=None, **kw):
        calls.append(list(settings.get("footage.sources")))
        return [make_trailer(lib.footage_dir(settings) / "videos" / "Red Dead Redemption 2 Trailer [gmA6MrX81z4].mp4")]

    monkeypatch.setattr(lib, "fetch", fake_fetch)
    job = create_topic_job(s, "Red Dead 2 systems inside GTA 6", vs="rdr2", count=3, options=topic_options(topic_project))
    assert run_job(s, job, until="research") == "paused", get_db(s.db_path).get_job(job)["error"]
    assert len(calls) == 1 and calls[0][0].endswith("gmA6MrX81z4")  # the official Red Dead 2 videos, not GTA 6's
    assert lib.library_summary(lib.build_index(game_settings(s, "rdr2")))["usable_shots"] > 0
    assert list((rdr / "photos").glob("*.jpg"))  # stills pulled for photo scenes and the thumbnail
    log = (s.jobs_dir / job / "pipeline.log").read_text()
    assert "library is empty - downloading its official videos" in log


def test_comparison_stops_when_the_other_game_has_no_footage(topic_project, monkeypatch):
    s = topic_project["settings"]
    shutil.rmtree(s.root / "assets" / "game_footage" / "rdr2")

    def no_internet(settings, urls=None, **kw):
        from gta_factory.app.core.errors import FactoryError

        raise FactoryError("No video could be downloaded (3 URL(s) failed).")

    monkeypatch.setattr(lib, "fetch", no_internet)
    job = create_topic_job(s, "Red Dead 2 systems inside GTA 6", vs="rdr2", count=3, options=topic_options(topic_project))
    assert run_job(s, job) == "failed"
    error = get_db(s.db_path).get_job(job)["error"]
    assert error.startswith("[research]") and "Red Dead Redemption 2 footage library is empty" in error and "--game rdr2" in error
    assert not (s.jobs_dir / job / "research" / "analysis.json").exists()  # stopped before any long work


def test_age_restricted_download_explains_the_cookies_setting(project):
    from gta_factory.modules.footage.library import _failure_hint

    s = project["settings"]
    hint = _failure_hint(s, "ERROR: [youtube] gmA6MrX81z4: Sign in to confirm your age. This video may be inappropriate for some users.")
    assert "cookies_from_browser" in hint and _failure_hint(s, "HTTP Error 404") == ""
