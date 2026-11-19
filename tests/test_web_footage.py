"""Web footage for the beats the library cannot show: search, filters, sidecars, rights (offline fakes)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

from gta_factory.modules.footage.library import _sidecar, build_index, footage_dir, load_items
from gta_factory.modules.script_short import websource as ws
from gta_factory.modules.script_short.match import rights_of


def test_queries_use_the_words_of_the_phrase() -> None:
    beat = {"text": "Six: grab a ball and shoot hoops.", "concept": "a character shooting basketball hoops on an outdoor court"}
    qs = ws.queries(beat, "GTA 6")
    assert qs[0] == "GTA 6 grab ball shoot hoops"
    assert any("basketball" in q for q in qs)


def test_titles_that_are_not_real_footage_of_the_game_are_skipped(project) -> None:
    s = project["settings"]
    rx, other = ws.game_pattern(s, "gta6"), ws.other_games(s, "gta6")
    keep = lambda t: ws.keep_title(t, game_rx=rx, other_rx=other, blocked=["leak"])  # noqa: E731
    assert keep("GTA 6 - Jet Ski Gameplay (4K)") == ""
    assert keep("Grand Theft Auto VI Trailer 2") == ""
    assert "does not name" in keep("Jet ski racing in Miami")
    assert "not real footage" in keep("GTA 6 jet ski - fan made concept")
    assert "not real footage" in keep("GTA 6 LEAKED gameplay")
    assert "not real footage" in keep("GTA 5 mods: GTA 6 jet skis")
    assert "another game" in keep("GTA 6 vs GTA 5 jet skis")
    rx5, other5 = ws.game_pattern(s, "gta5"), ws.other_games(s, "gta5")
    assert ws.keep_title("GTA 5 airboat in the swamp", game_rx=rx5, other_rx=other5, blocked=[]) == ""
    assert "does not name" in ws.keep_title("Grand Theft Auto VI swamp", game_rx=rx5, other_rx=other5, blocked=[])


def test_bing_results_are_parsed() -> None:
    page = '<a class="iusc" m="{&quot;murl&quot;:&quot;https://img.example.com/gta6-kayak.jpg&quot;,&quot;t&quot;:&quot;GTA 6 kayak&quot;,&quot;purl&quot;:&quot;https://news.example.com/gta-6&quot;}" href="#">'
    assert ws.parse_bing(page) == [{"url": "https://img.example.com/gta6-kayak.jpg", "title": "GTA 6 kayak", "page": "https://news.example.com/gta-6"}]


def _mp4(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _jpeg(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (960, 540), (40, 120, 200))
    for x in range(0, 960, 40):  # some detail, so the picture is not taken for a blank frame
        for y in range(540):
            img.putpixel((x, y), (250, 250, 250))
    img.save(path.with_suffix(".jpg"), "JPEG")
    return path.with_suffix(".jpg")


def test_missing_beats_are_searched_downloaded_and_kept_for_their_job(project) -> None:
    s = project["settings"]
    searched: list[str] = []

    def search_videos(q: str, n: int) -> list[dict]:
        searched.append(q)
        return [
            {"id": "aaaaaaaaaaa", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "title": "GTA 6 kayak gameplay", "duration": 120, "channel": "Some Gamer"},
            {"id": "bbbbbbbbbbb", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "title": "GTA 6 kayak FAN MADE", "duration": 60, "channel": "Fan"},
            {"id": "ccccccccccc", "url": "https://www.youtube.com/watch?v=ccccccccccc", "title": "GTA 6 kayak 3 hour stream", "duration": 10800, "channel": "Streamer"},
        ]

    def download_video(url: str, folder: Path):
        return _mp4(folder / "GTA 6 kayak gameplay [aaaaaaaaaaa].mp4"), {"channel": "Some Gamer", "uploader": "Some Gamer"}

    def search_images(q: str, n: int) -> list[dict]:
        return [{"url": "https://img.example.com/kayak.jpg", "title": "GTA 6 kayak screenshot", "page": "https://news.example.com/gta-6-kayak"},
                {"url": "https://img.example.com/real.jpg", "title": "A real kayak", "page": "https://outdoors.example.com/"}]

    rep = ws.source_missing(s, [{"index": 4, "text": "or paddle a kayak.", "concept": "a person paddling a kayak", "game": "gta6"}], job="job1", log=lambda m: None,
                            search_videos=search_videos, download_video=download_video, search_images=search_images,
                            download_image=lambda url, dest, referer: _jpeg(dest))
    assert searched and searched[0].startswith("GTA 6 paddle kayak")
    row = rep["beats"][0]
    assert [v["url"] for v in row["videos"]] == ["https://www.youtube.com/watch?v=aaaaaaaaaaa"]
    whys = {x["url"]: x["why"] for x in row["skipped"]}
    assert "not real footage" in whys["https://www.youtube.com/watch?v=bbbbbbbbbbb"]
    assert "too long" in whys["https://www.youtube.com/watch?v=ccccccccccc"]
    assert "does not name" in whys["https://img.example.com/real.jpg"]
    assert len(row["photos"]) == 1
    root = footage_dir(s)
    video = root / row["videos"][0]["file"]
    side = _sidecar(video)
    assert side["web_found"] and side["article_media"] == "job1" and side["credit"] == "Footage: Some Gamer (YouTube)"
    assert rights_of(s, side) == "web_found"
    # the picture is in the library for this job only, never for another one
    photo = root / row["photos"][0]["file"]
    assert rights_of(s, _sidecar(photo)) == "web_found"
    index = build_index(s)
    assert any(p["file"] == row["photos"][0]["file"] for p in index["photos"])
    assert any(p["file"] == row["photos"][0]["file"] for p in load_items(s, index, job="job1")["photos"])
    assert not any(p["file"] == row["photos"][0]["file"] for p in load_items(s, index, job="job2")["photos"])
    assert any(v["file"] == row["videos"][0]["file"] for v in index["videos"])


def test_a_broken_download_is_dropped(project) -> None:
    def download_video(url: str, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "x [ddddddddddd].mp4").write_bytes(b"not a video")
        return folder / "x [ddddddddddd].mp4", {}

    rep = ws.source_missing(project["settings"], [{"index": 0, "text": "Three: scuba dive with dolphins.", "concept": "dolphins", "game": "gta6"}], job="j", log=lambda m: None,
                            search_videos=lambda q, n: [{"id": "ddddddddddd", "url": "https://www.youtube.com/watch?v=ddddddddddd", "title": "GTA 6 scuba diving", "duration": 90, "channel": "X"}],
                            download_video=download_video, search_images=lambda q, n: [], download_image=None)
    assert rep["videos"] == 0 and "cannot be read" in rep["beats"][0]["skipped"][0]["why"]
    build_index(project["settings"])  # the library still indexes


def test_a_rockstar_upload_found_by_the_search_is_official(project) -> None:
    s = project["settings"]
    rep = ws.source_missing(
        s, [{"index": 0, "text": "Five: race jet skis,", "concept": "jet skis", "game": "gta6"}], job="job1", log=lambda m: None,
        search_videos=lambda q, n: [{"id": "rrrrrrrrrrr", "url": "https://www.youtube.com/watch?v=rrrrrrrrrrr", "title": "Grand Theft Auto VI Trailer 3", "duration": 150, "channel": "Rockstar Games"}],
        download_video=lambda url, folder: (_mp4(folder / "t3 [rrrrrrrrrrr].mp4"), {"channel": "Rockstar Games"}),
        search_images=lambda q, n: [], download_image=lambda *a, **k: None)
    side = _sidecar(footage_dir(s) / rep["beats"][0]["videos"][0]["file"])
    assert "web_found" not in side and rights_of(s, side) == "official"


def test_a_search_engine_that_cannot_be_reached_is_reported(project) -> None:
    def boom(q: str, n: int):
        raise OSError("no network")

    rep = ws.source_missing(project["settings"], [{"index": 0, "text": "Four: tear through the swamps on an airboat.", "concept": "an airboat", "game": "gta6"}],
                            job="j", log=lambda m: None, search_videos=boom, search_images=boom, download_video=None, download_image=None)
    assert rep["videos"] == rep["photos"] == 0 and len(rep["errors"]) == 2


def test_find_on_the_web_fills_missing_beats_videos_first_then_pictures(project, monkeypatch) -> None:
    from types import SimpleNamespace

    from gta_factory.modules.script_short import match as match_mod
    from gta_factory.modules.script_short import produce

    beats = [{"text": "Five: race jet skis,", "concept": "jet skis", "specific": True},
             {"text": "or paddle a kayak.", "concept": "kayak", "specific": True},
             {"text": "Three: scuba dive with dolphins.", "concept": "dolphins", "specific": True}]
    seg = lambda f, kind="shot", rights="official": {"file": f, "path": f, "kind": kind, "start": 0.0, "end": 1.0, "rights": rights}  # noqa: E731
    first = [{"beat": 0, "text": beats[0]["text"], "specific": True, "segments": [seg("videos/t2.mp4")], "status": "verified_vision", "confidence": 0.9},
             {"beat": 1, "text": beats[1]["text"], "specific": True, "segments": [], "status": "missing", "confidence": 0.0, "reason": "no frame"},
             {"beat": 2, "text": beats[2]["text"], "specific": True, "segments": [], "status": "missing", "confidence": 0.0, "reason": "no frame"}]
    calls: list[tuple[list[str], bool, dict]] = []

    def fake_match(settings, bs, needs, *, allow_photos=False, used=None, **kw):
        calls.append(([b["text"] for b in bs], allow_photos, used))
        out = []
        for b in bs:
            m = {"text": b["text"], "specific": True, "segments": [], "status": "missing", "confidence": 0.0, "reason": "still not"}
            if "kayak" in b["text"] and not allow_photos:
                m.update(segments=[seg("videos/web/j/kayak.mp4", rights="web_found")], status="verified_vision", confidence=0.8)
            if "dolphins" in b["text"] and allow_photos:
                m.update(segments=[seg("photos/web/j/dolphins.jpg", kind="photo", rights="web_found")], status="verified_vision", confidence=0.7)
            out.append(m)
        return out

    found = {"beats": [{"beat": 1, "queries": ["GTA 6 paddle kayak"], "videos": [{}], "photos": []}, {"beat": 2, "queries": ["GTA 6 scuba dive dolphins"], "videos": [], "photos": [{}]}], "videos": 1, "photos": 1, "errors": []}
    monkeypatch.setattr(match_mod, "match_beats", fake_match)
    monkeypatch.setattr(ws, "source_missing", lambda *a, **k: found)
    ctx = SimpleNamespace(settings=project["settings"], options={}, job_id="j", log=lambda m: None, warn=lambda m: None)
    rep = produce.find_on_the_web(ctx, beats, [1.0, 1.0, 1.0], first, clip=None, verify=None)
    assert rep["filled"] == [1] and rep["photo_filled"] == [2]
    assert calls[0][0] == [beats[1]["text"], beats[2]["text"]] and calls[0][1] is False
    assert calls[0][2] == {"videos/t2.mp4": [(0.0, 1.0)]}  # what is already on screen is never shown twice
    assert calls[1][0] == [beats[2]["text"]] and calls[1][1] is True
    assert first[1]["web"] and first[1]["beat"] == 1 and first[2]["segments"][0]["kind"] == "photo"


def test_offline_jobs_do_not_search_the_web(project, monkeypatch) -> None:
    from types import SimpleNamespace

    from gta_factory.modules.script_short import produce

    monkeypatch.setattr(ws, "source_missing", lambda *a, **k: (_ for _ in ()).throw(AssertionError("searched")))
    s = project["settings"]
    ctx = SimpleNamespace(settings=s, options={"offline": True}, job_id="j", log=lambda m: None, warn=lambda m: None)
    from gta_factory.modules.script_short import match as match_mod

    monkeypatch.setattr(match_mod, "match_beats", lambda *a, **k: [{"text": "x", "segments": [], "status": "missing", "reason": "r"}])
    m = [{"beat": 0, "text": "x", "concept": "x", "segments": [], "status": "missing", "reason": "r"}]
    rep = produce.find_on_the_web(ctx, [{"text": "x", "concept": "x"}], [1.0], m, clip=None, verify=None)
    assert "off" in rep["note"] and m[0]["search_url"].startswith("https://www.youtube.com/results")
