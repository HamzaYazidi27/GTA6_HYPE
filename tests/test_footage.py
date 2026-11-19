"""Real GTA 6 footage: library index, 80-90 % share, what is said is shown, tags, autotag, dashboard, render props.

A synthetic "trailer" stands in for the official one: bright shots separated by
hard cuts, plus a black title card that must be skipped.
"""
from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import RENDER, job_options
from gta_factory.app.core.pipeline import create_job, run_job
from gta_factory.app.core.utils import read_json, which, write_json
from gta_factory.app.database.db import get_db
from gta_factory.modules.footage import library as lib
from gta_factory.modules.footage.autotag import autotag, clean_answer, pending
from gta_factory.modules.footage.library import build_index, library_summary, load_items, name_tags
from gta_factory.modules.footage.select import _pick_scenes, _piece, assign_footage, build_segments, cut_scene_clips, preview
from gta_factory.modules.footage.vocabulary import load_vocabulary

pytestmark = pytest.mark.skipif(not which("ffmpeg"), reason="needs ffmpeg")

SOURCES = ["testsrc2", "mandelbrot", "smptebars", "rgbtestsrc", "testsrc", "cellauto", "smptehdbars", "life", "pal75bars", "yuvtestsrc"]
TRAILER = "Grand Theft Auto VI Trailer 1 [QdBZY2fkU-0].mp4"


def _src(src: str) -> str:
    """A lavfi test source; the random ones (life, cellauto) get a fixed seed so every run sees the same pictures."""
    return f"{src}=seed=7:" if src in ("life", "cellauto") else f"{src}="


def make_trailer(path: Path, shots: int = 10, shot_seconds: float = 3.0) -> Path:
    """Distinct bright shots with hard cuts, a black 'title card' in the middle, a logo head and an end card."""
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = ["black:3.5"] + [f"{SOURCES[i]}:{shot_seconds}" for i in range(shots // 2)] + ["black:2.0"] + [f"{SOURCES[i]}:{shot_seconds}" for i in range(shots // 2, shots)] + ["black:10.5"]
    inputs = []
    for item in seq:
        src, dur = item.split(":")
        spec = f"color=c=black:s=640x360:r=30:d={dur}" if src == "black" else f"{_src(src)}s=640x360:r=30,trim=duration={dur}"
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
    trailer = make_trailer(root / "videos" / TRAILER)
    # the shots from 12 to 18 s show "the police" (a tag on a part of the video)
    write_json(trailer.with_name(trailer.name + ".json"), {"segments": [{"start": 12.0, "end": 18.0, "tags": ["police"]}]})
    make_photo(root / "photos" / "lucia_beach_01.jpg", "0xF2C14E")
    make_photo(root / "photos" / "vice_city_night.jpg", "0x8E5BD9")
    make_photo(root / "photos" / "GTA VI Trailer 3 screenshot.jpg", "0x2DE2E6")
    make_photo(root / "photos" / "leaked_build.jpg", "0x333333")  # must be refused
    return project


def scenes_of(texts_templates: list[tuple[str, str]]) -> list[dict]:
    return [{"scene_number": i + 1, "template": tpl, "narration": text, "on_screen_text": "", "emphasis": ""} for i, (text, tpl) in enumerate(texts_templates)]


# ----------------------------------------------------------------- vocabulary
def test_vocabulary_reads_what_the_narration_names(project):
    v = load_vocabulary(project["settings"])
    found = [(m["concepts"][0], m["word"], m["said"]) for m in v.mentions("Lucia's escape from the cops ends in Vice City, near the Leonida Keys and an alligator.")]
    assert found == [("lucia", 0, "Lucia's"), ("police", 4, "cops"), ("vice_city", 7, "Vice City"), ("leonida_keys", 11, "Leonida Keys"), ("alligator", 15, "alligator")]
    assert [m["concepts"] for m in v.mentions("The second trailer showed Jason and Lucia.")] == [["trailer_2"], ["duo"]]
    assert v.mentions("Rockstar confirmed the release date for PS5 and Xbox Series X.") == []
    assert v.canonical("Vice City") == "vice_city" and v.canonical("Lucia Caminos") == "lucia" and v.canonical("flamingos") == "flamingo"
    assert v.also("duo") == ["lucia", "jason"]
    assert name_tags(Path("Lucia_beach-01 [QdBZY2fkU-0].jpg"), v) == ["beach", "lucia", "trailer_1"]
    # your own concepts, in knowledge/footage_tags.yaml
    (project["root"] / "knowledge" / "footage_tags.yaml").write_text("jet_ski:\n  say: [jet ski, watercraft]\n  also: [boats]\nmoney:\n  enabled: false\nlucia: [our heroine]\n")
    v = load_vocabulary(project["settings"])
    assert [m["concepts"] for m in v.mentions("Our heroine steals a watercraft with money")] == [["lucia"], ["jet_ski"]]


# ----------------------------------------------------------------- index and tags
def test_index_detects_shots_and_skips_dark_and_blocked(footage_project):
    s = footage_project["settings"]
    index = build_index(s)
    summ = library_summary(index)
    assert summ["videos"] == 1 and summ["photos"] == 3
    assert any("leak" in sk["reason"] for sk in summ["skipped"])
    shots = index["videos"][0]["shots"]
    ok = [x for x in shots if x["ok"]]
    assert 6 <= len(ok) <= 10, shots
    assert all(x["luma"] >= 28 for x in ok)
    assert all(x["s"] >= 3.0 for x in ok)  # logo head skipped
    assert all(x["e"] <= index["videos"][0]["duration"] - 10 for x in ok)  # end card skipped
    assert index["videos"][0]["tags"] == ["trailer_1"]  # from the YouTube id in the file name
    assert any(x.get("tags") == ["police"] for x in ok)  # the tagged part of the video
    # cached: a second scan does not re-run detection and returns the same index
    assert build_index(s) == index
    items = load_items(s, index)
    photo = next(p for p in items["photos"] if p["file"].endswith("lucia_beach_01.jpg"))
    assert photo["tags"] == {"beach": 3.0, "lucia": 3.0}
    police = [it for it in items["shots"] if "police" in it["tags"]]
    assert police and all(it["tags"]["police"] == 3.0 and it["tags"]["trailer_1"] == 2.0 for it in police)


def test_add_tag_and_remove_files(footage_project, tmp_path):
    s = footage_project["settings"]
    shot = make_photo(tmp_path / "in" / "IMG_0042.png", "0x44AA66")
    added = lib.add_files(s, [str(tmp_path / "in")], tags=["flamingo", "Vice City"], log=lambda m: None)
    assert [p.name for p in added] == ["IMG_0042.png"] and shot.exists()
    path = lib.resolve_file(s, "IMG_0042")
    assert read_json(lib.sidecar_path(path))["tags"] == ["Vice City", "flamingo"]
    side = lib.set_tags(s, path, add=["gators"], remove=["vice city"])
    assert side["tags"] == ["alligator", "flamingo"]
    trailer = lib.resolve_file(s, "Trailer 1")
    side = lib.set_tags(s, trailer, add=["Jason"], at=(22.0, 26.0))
    assert {"start": 22.0, "end": 26.0, "tags": ["jason"]} in side["segments"] and side["segments"][0]["tags"] == ["police"]
    items = load_items(s)
    assert any("jason" in it["tags"] for it in items["shots"])
    assert any(p["file"] == "photos/IMG_0042.png" and set(p["tags"]) == {"alligator", "flamingo"} for p in items["photos"])
    lib.remove_file(s, path)
    assert not path.exists() and not lib.sidecar_path(path).exists()
    with pytest.raises(Exception):
        lib.resolve_file(s, "does-not-exist")


def test_autotag_with_a_vision_model(footage_project):
    s = footage_project["settings"]
    seen = []

    def fake_vision(image: bytes, prompt: str) -> dict:
        assert image[:2] == b"\xff\xd8" and "alligator:" in prompt  # a JPEG and the concept list
        seen.append(len(image))
        return {"tags": ["alligator", "Leonida Keys", "made-up"], "objects": ["speedboat", "person", "palm tree"], "caption": "An alligator near a boat."}

    todo = pending(s)
    out = autotag(s, tagger=fake_vision, log=lambda m: None)
    assert out["tagged"] == len(todo) == len(seen) and out["left"] == 0
    assert autotag(s, tagger=fake_vision, log=lambda m: None)["tagged"] == 0  # cached
    items = load_items(s)
    shot = items["shots"][0]
    assert shot["tags"]["alligator"] == 2.0 and shot["tags"]["leonida_keys"] == 2.0 and shot["tags"]["boats"] == 2.0  # "speedboat" is a concept word
    assert shot["tags"]["palm_tree"] == 1.0 and "person" not in shot["tags"] and "made_up" not in shot["tags"]
    lucia = next(p for p in items["photos"] if p["file"].endswith("lucia_beach_01.jpg"))
    assert lucia["tags"]["lucia"] == 3.0 and lucia["tags"]["alligator"] == 2.0  # your own tags count more
    assert clean_answer({"tags": "nope", "objects": [3, "", "Gators"]}, load_vocabulary(s))["tags"] == ["alligator"]


# ----------------------------------------------------------------- selection
def test_pick_scenes_lands_in_the_band():
    scenes = [{"scene_number": i + 1, "template": t, "narration": " ".join(["word"] * 20)} for i, t in enumerate(["kinetic", "title", "claim", "bars", "kinetic", "timeline", "sources", "stat", "kinetic", "outro"])]
    chosen = _pick_scenes(scenes, 0.8, 165)
    assert len(chosen) == 8 and 1 in chosen and 4 not in chosen and 7 not in chosen  # bars + sources stay graphics
    assert len(_pick_scenes(scenes, 1.0, 165)) == 10
    # a scene that names something the library has keeps its footage; another dense one goes graphics-only instead
    chosen = _pick_scenes(scenes, 0.8, 165, matched={4})
    assert 4 in chosen and 7 not in chosen and len(chosen) == 8
    # only kinetic scenes: still 80-90 % (one goes graphics-only), never the hook, title or outro
    scenes = [{"scene_number": i + 1, "template": "title" if i == 1 else "outro" if i == 9 else "kinetic", "narration": " ".join(["word"] * 20)} for i in range(10)]
    chosen = _pick_scenes(scenes, 0.85, 165, lo=0.8, hi=0.9)
    assert 8 <= len(chosen) <= 9 and {1, 2, 10} <= chosen


def test_assign_footage_shows_what_is_said(footage_project):
    s = footage_project["settings"]
    texts = [
        ("GTA 6 is two days away and the hype is real.", "kinetic"),
        ("Here is everything Rockstar has confirmed so far.", "title"),
        ("Lucia is back on the beach in this new screenshot.", "kinetic"),
        ("Sales could top forty million copies in the first year.", "bars"),
        ("In one shot, the police close in on a getaway car.", "claim"),
        ("The story moves between Vice City and Port Gellhorn.", "headline"),
        ("Pre-orders are open on PlayStation and Xbox.", "stat"),
        ("Analysts expect record numbers.", "sources"),
        ("The third trailer is planned before launch.", "timeline"),
        ("Fans keep counting the days.", "kinetic"),
        ("More on that soon, and thanks for watching.", "bullets"),
        ("Subscribe for verified GTA 6 news.", "outro"),
    ]
    scenes = scenes_of(texts)
    out = assign_footage(s, scenes)
    summ = out["summary"]
    assert summ["ratio_min"] == 0.8 and summ["ratio_max"] == 0.9
    assert 0.78 <= summ["share"] <= 0.92, summ
    by = {sc["scene_number"]: sc for sc in scenes}
    # Lucia named at the start of a short scene: her screenshot, the whole time
    assert by[3]["footage"]["kind"] == "photo" and by[3]["footage"]["file"].endswith("lucia_beach_01.jpg")
    # "the police": a shot from the tagged part of the trailer, cut in at the word
    beat = by[5]["footage"]["beats"][0]
    assert beat["said"] == "police" and beat["word"] == 4 and beat["piece"]["type"] == "shot" and 12.0 <= beat["piece"]["start"] < 18.0
    # "Vice City": its screenshot; "Port Gellhorn": nothing in the library -> reported, any footage shown
    vc = by[6]["footage"]["beats"]
    assert vc[0]["said"] == "Vice City" and vc[0]["piece"]["file"].endswith("vice_city_night.jpg")
    assert {"scene": 6, "said": "Port Gellhorn", "concepts": ["port_gellhorn"]} in summ["missing"]
    # "the third trailer": the file whose name says so
    assert by[9]["footage"]["beats"][0]["piece"]["file"].endswith("Trailer 3 screenshot.jpg")
    said = {m["said"] for m in summ["matched"]}
    assert {"Lucia / beach", "Vice City", "third trailer"} <= said and any("police" in x for x in said)
    graphics = [sc for sc in scenes if not sc.get("footage")]
    assert graphics and all(sc["template"] in ("bars", "sources", "stat", "bullets", "headline", "timeline", "claim") for sc in graphics)
    for sc in scenes:
        fo = sc.get("footage")
        if fo and fo["kind"] == "video":
            assert all(p["end"] - p["start"] <= 6.0 + 1e-6 for p in fo["fill"] if p["type"] == "shot")
        if fo:
            assert "leaked" not in json.dumps(fo)
    assert all(e["review_status"] == "approved" and e["attribution"] == "Footage: Rockstar Games" for e in out["ledger"])
    # previews
    rows = preview(s, "Lucia runs from the cops in Port Gellhorn")
    assert [(r["said"], r["matches"] > 0) for r in rows] == [("Lucia", True), ("cops", True), ("Port Gellhorn", False)]


def test_build_segments_cuts_at_the_words():
    def shot(f, s, e):
        n = min(6, e - s)
        off = (e - s - n) / 2
        return {"type": "shot", "file": f, "s": s, "e": e, "start": s + off, "end": s + off + n}

    words = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.35} for i, w in enumerate("The cops chased Lucia across Vice City last night while an alligator watched from the swamp".split())]
    fo = {"beats": [{"word": 3, "said": "Lucia", "piece": {"type": "photo", "file": "lucia.jpg"}}, {"word": 5, "said": "Vice City", "piece": shot("T2", 40, 44)}, {"word": 11, "said": "alligator", "piece": shot("T2", 60, 62)}],
          "fill": [shot("T1", 10, 13), shot("T1", 13, 17.5), shot("T1", 17.5, 20), shot("T1", 20, 29)]}
    segs = build_segments(fo, words, 9.0, lead=0.25, min_piece=1.2)
    assert abs(sum(x["d"] for x in segs) - 9.0) < 1e-6
    assert all(abs(a["t"] + a["d"] - b["t"]) < 1e-6 for a, b in zip(segs, segs[1:]))  # contiguous
    assert all(x["d"] >= 1.2 - 1e-6 for x in segs)
    at = {x.get("said"): x for x in segs if x.get("said")}
    assert at["Lucia"]["file"] == "lucia.jpg" and at["Lucia"]["t"] == 0.0  # too close to the start for a filler cut
    assert at["Vice City"]["t"] == pytest.approx(5 * 0.4 - 0.25) and at["alligator"]["t"] == pytest.approx(11 * 0.4 - 0.25)
    assert 40 <= at["Vice City"]["start"] and at["Vice City"]["start"] + at["Vice City"]["d"] <= 44 + 1e-6  # never past its shot
    # no beats: the trailer's own edit, cut to the scene length
    segs = build_segments({"beats": [], "fill": fo["fill"]}, words, 14.3)
    assert [x["start"] for x in segs[:3]] == [10.0, 13.0, 17.5] and abs(sum(x["d"] for x in segs) - 14.3) < 1e-6


def test_cut_scene_clip_mixes_shots_and_screenshots(footage_project):
    s = footage_project["settings"]
    root = s.root / "assets" / "gta6_footage"
    trailer = str(root / "videos" / TRAILER)
    fo = {"kind": "video", "beats": [{"word": 2, "said": "Lucia", "piece": {"type": "photo", "file": str(root / "photos" / "lucia_beach_01.jpg")}}],
          "fill": [{"type": "shot", "file": trailer, "s": 4.0, "e": 7.0, "start": 4.0, "end": 7.0}, {"type": "shot", "file": trailer, "s": 7.0, "e": 10.0, "start": 7.0, "end": 10.0}]}
    scenes = [{"scene_number": 1, "template": "kinetic", "narration": "Meet the lead, Lucia, in Vice City.", "footage": fo}]
    words = [{"w": w, "s": 1.0 + i * 0.5, "e": 1.4 + i * 0.5} for i, w in enumerate(scenes[0]["narration"].split())]
    assert cut_scene_clips(s, scenes, {1: 8.0}, out_dir=s.root / "clips", width=320, height=568, fps=30, words={1: words}) == 1
    clip = Path(fo["clip"])
    frames = int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames,width,height", "-of", "csv=p=0", str(clip)], capture_output=True, text=True).stdout.strip().split(",")[-1])
    assert frames == round(8.2 * 30)
    types = [x["type"] for x in fo["segments"]]  # a vertical clip: the filler is cut every ~2 s (rhythm.short.shot_hold_seconds)
    assert types[:2] == ["shot", "photo"] and fo["segments"][1]["t"] == pytest.approx(1.75)
    assert len(types) >= 3 and set(types[2:]) == {"shot"} and max(x["d"] for x in fo["segments"] if x["type"] == "shot") <= 2.2 + 0.9 + 1e-6
    assert cut_scene_clips(s, scenes, {1: 8.0}, out_dir=s.root / "clips", width=320, height=568, fps=30, words={1: words}) == 0  # cached


# ----------------------------------------------------------------- dashboard
def test_dashboard_footage_page(footage_project):
    from gta_factory.app.dashboard.server import make_server

    s = footage_project["settings"]
    httpd = make_server(s, "127.0.0.1", 0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def call(method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(base + path, data=data, method=method, headers={"X-Factory": "1", "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read() or b"{}")

    try:
        d = call("GET", "/api/footage")
        assert d["summary"]["videos"] == 1 and len(d["photos"]) == 3 and d["ratio"]["min"] == 0.8
        assert any(c["concept"] == "lucia" and c["photos"] == 1 for c in d["concepts"])
        img = (s.root / "assets" / "gta6_footage" / "photos" / "vice_city_night.jpg").read_bytes()
        r = call("POST", "/api/footage/upload?name=" + urllib.request.quote("../flamingo shot.jpg") + "&tags=flamingo,beach", raw=img)
        assert r["file"] == "photos/flamingo shot.jpg"
        assert read_json(s.root / "assets" / "gta6_footage" / "photos" / "flamingo shot.jpg.json")["tags"] == ["beach", "flamingo"]
        call("PUT", "/api/footage/tags", {"file": "photos/flamingo shot.jpg", "tags": ["Flamingos", "Leonida Keys"]})
        rows = call("POST", "/api/footage/find", {"text": "A flamingo in the Leonida Keys"})["rows"]
        assert all(r["matches"] for r in rows) and "flamingo shot.jpg" in rows[0]["best"][0]["file"]
        assert urllib.request.urlopen(base + "/footage-files/photos/flamingo%20shot.jpg").read() == img
        poster = urllib.request.urlopen(base + "/api/footage/poster?file=" + urllib.request.quote("videos/" + TRAILER)).read()
        assert poster[:2] == b"\xff\xd8"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/footage-files/..%2F..%2Fconfig.yaml")
        assert e.value.code in (400, 403, 404)
        with pytest.raises(urllib.error.HTTPError) as e:
            call("POST", "/api/footage/upload?name=evil.sh", raw=b"#!/bin/sh")
        assert e.value.code == 400
        call("DELETE", "/api/footage?file=" + urllib.request.quote("photos/flamingo shot.jpg"))
        assert not (s.root / "assets" / "gta6_footage" / "photos" / "flamingo shot.jpg").exists()
        req = urllib.request.Request(base + "/api/footage/upload?name=x.jpg", data=img, method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403  # no X-Factory header
    finally:
        httpd.shutdown()
        httpd.server_close()


# ----------------------------------------------------------------- pipeline
def test_pipeline_puts_footage_in_the_render_props(footage_project):
    s = footage_project["settings"]
    job = create_job(s, [footage_project["url"]], fmt="short", options=job_options(footage_project))
    assert run_job(s, job, until="timeline") == "paused", get_db(s.db_path).get_job(job)["error"]
    root = s.jobs_dir / job
    rights = read_json(root / "assets" / "rights_review.json")
    assert rights["footage"]["share"] >= 0.75, rights["footage"]
    assert "Footage: Rockstar Games" in rights["credits"]
    # the narration says "third trailer": the matching screenshot is cut in at that word
    assert any(m["said"].lower() == "third trailer" for m in rights["footage"]["matched"]), rights["footage"]["matched"]
    scenes = read_json(root / "scenes" / "scene_breakdown.json")
    cut_in = [seg for sc in scenes for seg in (sc.get("footage") or {}).get("segments") or [] if seg.get("said", "").lower() == "third trailer"]
    photo_scenes = [sc for sc in scenes if (sc.get("footage") or {}).get("kind") == "photo" and "Trailer 3" in sc["footage"]["file"]]
    assert cut_in or photo_scenes
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
    review = (out / "REVIEW.md").read_text()
    assert "Ce qui est dit est montré" in review
    qc = read_json(out / "qc_report.json")
    assert any(c["id"] == "footage.share" for c in qc["checks"])


def test_update_finds_new_official_videos(footage_project, monkeypatch):
    """`footage update` downloads every GTA VI upload of the official channels that is not in the library yet."""
    s = footage_project["settings"]

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            assert "extract_flat" in self.opts and url.endswith("@RockstarGames/videos")
            return {"entries": [
                {"id": "QdBZY2fkU-0", "title": "Grand Theft Auto VI Trailer 1", "url": "https://www.youtube.com/watch?v=QdBZY2fkU-0"},  # already in the library
                {"id": "NEWTRAILER3", "title": "Grand Theft Auto VI Trailer 3", "url": "https://www.youtube.com/watch?v=NEWTRAILER3"},
                {"id": "RDR2PCxxxxx", "title": "Red Dead Redemption 2 PC", "url": "https://www.youtube.com/watch?v=RDR2PCxxxxx"},  # not GTA VI
                {"id": "LEAKEDxxxxx", "title": "GTA VI leaked build reupload", "url": "x"},  # blocked words
            ]}

    monkeypatch.setattr(lib, "_yt_dlp", lambda: type("M", (), {"YoutubeDL": FakeYDL}))
    found = lib.find_new_official(s, log=lambda m: None)
    assert [f["id"] for f in found] == ["NEWTRAILER3"] and found[0]["url"].endswith("NEWTRAILER3")


def test_letterbox_bars_are_cut_away(project):
    """Trailers with black bars (cinema framing) are cropped before framing, in clips and in pictures."""
    import numpy as np

    from gta_factory.modules.footage.library import cropped_photo, footage_dir

    s = project["settings"]
    root = footage_dir(s)
    vid = root / "videos" / "Cinematic trailer.mp4"
    vid.parent.mkdir(parents=True)
    seq = "".join(f"{_src(src)}s=1280x536:r=30,trim=duration=3,pad=1280:720:0:92:black[v{i}];" for i, src in enumerate(SOURCES[:6]))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30:d=3.5", "-filter_complex", seq + "".join(f"[v{i}]" for i in range(6)) + "concat=n=6:v=1:a=0[out]", "-map", "[out]", "-c:v", "libx264", "-preset", "ultrafast", str(vid)], check=True)
    make_photo(root / "photos" / "night_sky.jpg", "0x0A0A12")  # dark, but not letterboxed
    bars = root / "photos" / "cinematic_still.jpg"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "4", "-i", str(vid), "-frames:v", "1", str(bars)], check=True)
    index = build_index(s)
    shots = [sh for sh in index["videos"][0]["shots"] if sh["ok"]]
    assert shots and all(sh["crop"] and sh["crop"][0] == 1280 and 92 <= sh["crop"][3] <= 104 and 520 <= sh["crop"][1] <= 536 for sh in shots)
    photos = {p["file"]: p for p in index["photos"]}
    assert photos["photos/night_sky.jpg"]["crop"] is None and photos["photos/cinematic_still.jpg"]["crop"]
    items = load_items(s, index)
    still = next(p for p in items["photos"] if p["file"].endswith("cinematic_still.jpg"))
    assert ".cropped" in still["path"] and Path(still["path"]).exists()
    assert cropped_photo(root, "photos/night_sky.jpg", None) == root / "photos" / "night_sky.jpg"
    # a clip cut from a letterboxed shot has picture from edge to edge
    sc = {"scene_number": 1, "template": "kinetic", "narration": "Some words here.", "footage": {"kind": "video", "beats": [], "fill": [_piece(it, 6.0) for it in items["shots"][:2]]}}
    cut_scene_clips(s, [sc], {1: 2.0}, out_dir=s.root / "clips", width=640, height=360, fps=30)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", sc["footage"]["clip"], "-frames:v", "1", "-vf", "format=gray", "-f", "rawvideo", "pipe:1"], capture_output=True).stdout
    frame = np.frombuffer(raw, dtype=np.uint8).reshape(360, 640)
    assert frame[:3].mean() > 40 and frame[-3:].mean() > 40  # no black band at the top or the bottom
