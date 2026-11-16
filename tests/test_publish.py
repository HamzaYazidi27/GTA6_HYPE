"""YouTube publishing against a fake YouTube that speaks the real resumable-upload protocol."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import httpx
import pytest

from gta_factory.app.config.settings import Settings
from gta_factory.app.core.errors import FactoryError, ProviderError
from gta_factory.app.core.pipeline import REVIEW_TEMPLATE
from gta_factory.app.database.db import get_db
from gta_factory.modules.publishing import youtube as yt

JOB = "20261116-120000-test-publish"


class FakeYouTube:
    def __init__(self, *, fail_chunk_once: int | None = None, die_after_chunks: int | None = None, thumb_status: int = 200):
        self.received = bytearray()
        self.total = 0
        self.chunks = 0
        self.fail_chunk_once = fail_chunk_once
        self.die_after_chunks = die_after_chunks
        self.thumb_status = thumb_status
        self.calls: list[str] = []
        self.insert_body: dict | None = None
        self.playlists: list[dict] = []
        self.captions: bytes = b""

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url, path = str(req.url), req.url.path
        self.calls.append(f"{req.method} {path}")
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(200, json={"access_token": "ya29.fake-access-token-0123456789", "expires_in": 3600})
        if req.headers.get("authorization") != "Bearer ya29.fake-access-token-0123456789":
            return httpx.Response(401)
        if path == "/youtube/v3/channels":
            return httpx.Response(200, json={"items": [{"id": "UC123", "snippet": {"title": "NEON DISPATCH", "customUrl": "@neon"}, "statistics": {"subscriberCount": "42"}}]})
        if path == "/upload/youtube/v3/videos" and req.method == "POST":
            self.insert_body = json.loads(req.content)
            self.total = int(req.headers["x-upload-content-length"])
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=SESSION1"})
        if path == "/upload/youtube/v3/videos" and req.method == "PUT":
            cr = req.headers["content-range"]
            if cr.startswith("bytes */"):
                if len(self.received) >= self.total:
                    return httpx.Response(200, json={"id": "vid123"})
                return httpx.Response(308, headers={"Range": f"bytes=0-{len(self.received) - 1}"} if self.received else {})
            if self.die_after_chunks is not None and self.chunks >= self.die_after_chunks:
                raise httpx.ConnectError("network down")
            self.chunks += 1
            if self.fail_chunk_once is not None and self.chunks == self.fail_chunk_once:
                self.fail_chunk_once = None
                return httpx.Response(503)
            start, end, total = map(int, re.match(r"bytes (\d+)-(\d+)/(\d+)", cr).groups())
            assert start == len(self.received), "the client must continue exactly where YouTube stands"
            self.received += req.content
            if len(self.received) >= total:
                return httpx.Response(201, json={"id": "vid123", "status": {"uploadStatus": "uploaded"}})
            return httpx.Response(308, headers={"Range": f"bytes=0-{len(self.received) - 1}"})
        if path == "/upload/youtube/v3/thumbnails/set":
            if self.thumb_status != 200:
                return httpx.Response(self.thumb_status, json={"error": {"message": "The authenticated user doesnt have permissions to upload and set custom video thumbnails.", "errors": [{"reason": "forbidden"}]}})
            return httpx.Response(200, json={"items": []})
        if path == "/upload/youtube/v3/captions":
            self.captions = req.content
            return httpx.Response(200, json={"id": "cap1"})
        if path == "/youtube/v3/playlists" and req.method == "GET":
            return httpx.Response(200, json={"items": self.playlists})
        if path == "/youtube/v3/playlists" and req.method == "POST":
            body = json.loads(req.content)
            self.playlists.append({"id": "PL1", "snippet": body["snippet"]})
            return httpx.Response(200, json={"id": "PL1"})
        if path == "/youtube/v3/playlistItems":
            return httpx.Response(200, json={"id": "PLI1"})
        if path == "/youtube/v3/videos":
            return httpx.Response(200, json={"items": [{"status": {"uploadStatus": "processed", "privacyStatus": "private"}, "processingDetails": {"processingStatus": "succeeded"}, "snippet": {"title": "t"}}]})
        return httpx.Response(404, json={"error": {"message": f"unexpected {req.method} {url}"}})


@pytest.fixture()
def pub(project, monkeypatch):
    monkeypatch.setattr(yt, "CHUNK", 256 * 1024)
    root: Path = project["root"]
    data = copy.deepcopy(project["settings"].data)
    data["youtube"].update({"upload_enabled": True, "client_secrets_file": "client_secret.json", "token_file": ".secrets/youtube_token.json"})
    s = Settings(data, root)
    (root / "client_secret.json").write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csecret"}}))
    (root / ".secrets").mkdir()
    (root / ".secrets" / "youtube_token.json").write_text(json.dumps({"refresh_token": "1//refresh-token-abcdefghijklmnopqrstuvwxyz"}))
    db = get_db(s.db_path)
    db.create_job(JOB, urls=["https://example.com/a"], fmt="long", language="en", options={}, steps=["package"], output_dir=str(s.outputs_dir / JOB))
    db.update_job(JOB, status="completed")
    db.add_story(job_id=JOB, headline="h", angle="a", fmt="long", fingerprint={}, claims=[], source_urls=[])
    out = s.outputs_dir / JOB
    (out / "thumbnails").mkdir(parents=True)
    (out / "pre-orders-open.mp4").write_bytes(bytes(range(256)) * 2800)  # ~700 KB -> 3 chunks
    (out / "thumbnails" / "thumb_1_stamp.png").write_bytes(b"\x89PNG fake")
    (out / "thumbnails" / "thumb_2_number.png").write_bytes(b"\x89PNG fake 2")
    (out / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n")
    meta = {
        "title": "Pre-orders open December 4",
        "title_options": ["Pre-orders open December 4", "GTA 6 pre-orders: the date", "What the December 4 date means"],
        "description": "Desc",
        "tags": ["gta 6"],
        "playlist": "GTA 6 Breaking news",
        "pinned_comment": "Will you pre-order?",
        "api_body": {"snippet": {"title": "x", "description": "Desc", "tags": ["gta 6"], "categoryId": "20"}, "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}},
    }
    (out / "metadata.json").write_text(json.dumps(meta))
    (out / "qc_report.json").write_text(json.dumps({"verdict": "pass", "checks": [{"id": "file.exists", "severity": "critical", "passed": True, "detail": "ok"}]}))
    (out / "REVIEW.md").write_text(REVIEW_TEMPLATE.format(title="t", job_id=JOB, fmt="long", duration="1:00", verdict="PASS", video="v.mp4", lufs=-14.0, n_thumbs=2, n_titles=3, qc_points="-"))
    return {"settings": s, "out": out, "db": db}


def run(pub, fake, **kw):
    return yt.publish(pub["settings"], JOB, transport=httpx.MockTransport(fake), sleep=lambda _s: None, log=lambda _m: None, **kw)


def test_checklist_parse_and_tick(pub):
    review = pub["out"] / "REVIEW.md"
    items = yt.review_checklist(review)
    assert len(items) == 4 and not any(i["done"] for i in items)
    items = yt.set_review_checklist(review, [True, False, True, True])
    assert [i["done"] for i in items] == [True, False, True, True]
    assert "Publier" in review.read_text()  # the publish instructions are not checkboxes


def test_gates_block_unreviewed_disabled_and_failed_qc(pub):
    s, out = pub["settings"], pub["out"]
    with pytest.raises(FactoryError, match="unchecked item"):
        run(pub, FakeYouTube())
    yt.set_review_checklist(out / "REVIEW.md", True)
    qc = {"verdict": "fail", "checks": [{"id": "picture.black", "severity": "critical", "passed": False, "detail": "1 black segment"}, {"id": "rights.cleared", "severity": "critical", "passed": False, "detail": "1 asset"}]}
    (out / "qc_report.json").write_text(json.dumps(qc))
    with pytest.raises(FactoryError) as e:
        run(pub, FakeYouTube(), overrides=["picture.black", "rights.cleared"])
    assert "rights.cleared failed and cannot be overridden" in e.value.message and "picture.black" not in e.value.message
    s.data["youtube"]["upload_enabled"] = False
    r = yt.readiness(s, pub["db"].get_job(JOB), yt.Package(s, JOB))
    assert any("upload_enabled" in b for b in r["blockers"])


def test_full_publish_with_a_transient_error(pub):
    yt.set_review_checklist(pub["out"] / "REVIEW.md", True)
    fake = FakeYouTube(fail_chunk_once=2)
    rec = run(pub, fake, title_index=2, thumbnail=2)
    video = next(pub["out"].glob("*.mp4")).read_bytes()
    assert bytes(fake.received) == video
    assert fake.insert_body["snippet"]["title"] == "GTA 6 pre-orders: the date"
    assert fake.insert_body["status"]["privacyStatus"] == "private" and "publishAt" not in fake.insert_body["status"]
    assert rec["state"] == "done" and rec["url"] == "https://youtu.be/vid123"
    assert rec["steps"] == {"thumbnail": "done", "captions": "done", "playlist": "done", "video": "done"}
    assert rec["thumbnail"].endswith("thumb_2_number.png") and b"Hello" in fake.captions
    job = pub["db"].get_job(JOB)
    assert job["summary"]["youtube"]["video_id"] == "vid123"
    with pub["db"].connect() as con:
        assert con.execute("SELECT published FROM stories WHERE job_id=?", (JOB,)).fetchone()[0] == 1
    public = json.loads((pub["out"] / "youtube.json").read_text())
    assert "session_uri" not in public
    with pytest.raises(FactoryError, match="Already published"):
        run(pub, FakeYouTube())


def test_interrupted_upload_resumes_without_duplicating(pub):
    yt.set_review_checklist(pub["out"] / "REVIEW.md", True)
    fake = FakeYouTube(die_after_chunks=1)
    with pytest.raises(ProviderError):
        run(pub, fake)
    rec = yt.Package(pub["settings"], JOB).record()
    assert rec["state"] == "failed" and rec.get("session_uri") and not rec.get("video_id")
    fake.die_after_chunks = None  # network back: same server keeps the bytes it already has
    rec = run(pub, fake)
    assert rec["state"] == "done" and bytes(fake.received) == next(pub["out"].glob("*.mp4")).read_bytes()
    assert sum(c == "POST /upload/youtube/v3/videos" for c in fake.calls) == 1  # one video, never two


def test_thumbnail_refusal_does_not_lose_the_video(pub):
    yt.set_review_checklist(pub["out"] / "REVIEW.md", True)
    rec = run(pub, FakeYouTube(thumb_status=403))
    assert rec["state"] == "done" and rec["steps"]["thumbnail"].startswith("failed") and "verified channel" in rec["steps"]["thumbnail"]


def test_dry_run_schedule_and_title_rules(pub):
    res = run(pub, FakeYouTube(), dry_run=True, privacy="public", publish_at="2099-01-01T10:00:00+01:00", thumbnail=1)
    st = res["request"]["status"]
    assert res["dry_run"] and st["privacyStatus"] == "private" and st["publishAt"] == "2099-01-01T09:00:00.000Z"
    with pytest.raises(FactoryError, match="Title refused"):
        run(pub, FakeYouTube(), dry_run=True, title_text="GTA 6 LEAKED MAP")
    with pytest.raises(FactoryError, match="in the past"):
        run(pub, FakeYouTube(), dry_run=True, publish_at="2001-01-01T10:00")


def test_oauth_url_uses_pkce_and_offline_access():
    verifier, challenge = yt.pkce_pair()
    url = yt.build_auth_url({"client_id": "cid", "auth_uri": yt.AUTH_URL}, "http://127.0.0.1:5555", "st", challenge)
    q = httpx.URL(url).params
    assert q["code_challenge_method"] == "S256" and q["access_type"] == "offline" and q["redirect_uri"].startswith("http://127.0.0.1")
    assert "youtube.upload" in q["scope"] and len(verifier) >= 43


def test_tokens_are_redacted_from_logs():
    from gta_factory.app.config.settings import redact

    line = redact("got ya29.a0AfB_byC1234567890abcdefghij and 1//0gAbCdEfGhIjKlMnOpQrStUv")
    assert "ya29.a0" not in line and "1//0g" not in line
