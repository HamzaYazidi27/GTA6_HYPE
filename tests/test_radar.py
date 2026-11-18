"""GTA VI Content Radar: real feeds served by a local HTTP server, a scripted AI, the real database."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from gta_factory.app.core.errors import FactoryError
from gta_factory.app.database.db import Database
from gta_factory.app.database.migrations import MIGRATIONS
from gta_factory.modules.radar import analyze as an
from gta_factory.modules.radar import engine
from gta_factory.modules.radar.relevance import category_hints, fingerprint, is_relevant, same_story
from gta_factory.modules.radar.sources import normalize_source
from gta_factory.modules.radar.store import RadarStore, iso, utcnow
from gta_factory.providers.llm.base import LLMProvider

NOW = datetime.now(timezone.utc)


def rfc(dt: datetime) -> str:
    return format_datetime(dt)


# ----------------------------------------------------------------------------- a tiny web with feeds

class Web:
    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, str, str]] = {}  # path -> (status, content type, body)
        self.hits: dict[str, int] = {}
        web = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                web.hits[path] = web.hits.get(path, 0) + 1
                status, ctype, body = web.pages.get(path, (404, "text/plain", "missing"))
                etag = '"%x"' % (hash(body) & 0xFFFFFFFF)
                if status == 200 and self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.end_headers()
                    return
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                if status == 200:
                    self.send_header("ETag", etag)
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def rss(self, path: str, items: list[dict[str, Any]]) -> None:
        body = "".join(
            f"<item><title>{i['title']}</title><link>{self.base}{i['link']}</link><guid>{i.get('guid', i['link'])}</guid>"
            f"<pubDate>{rfc(i['date'])}</pubDate><description>{i.get('text', '')}</description></item>"
            for i in items
        )
        self.pages[path] = (200, "application/rss+xml", f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>')

    def atom(self, path: str, items: list[dict[str, Any]]) -> None:
        body = "".join(
            f'<entry><title>{i["title"]}</title><link href="{self.base}{i["link"]}"/><id>{i["link"]}</id>'
            f"<updated>{i['date'].isoformat()}</updated><summary>{i.get('text', '')}</summary></entry>"
            for i in items
        )
        self.pages[path] = (200, "application/atom+xml", f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>{body}</feed>')

    def close(self) -> None:
        self.httpd.shutdown()


HEIST = "GTA 6 trailer reveals a new heist mechanic: drilling bank vaults"
MAP = "GTA 6 map: Port Gellhorn interiors you can explore"
BIGFOOT = "Players spotted a Bigfoot easter egg in the GTA 6 trailer"
CAR = "Which car are you buying first?"


@pytest.fixture()
def radar(project: dict):
    web = Web()
    web.rss("/press.xml", [
        {"title": HEIST, "link": "/a/heist", "date": NOW - timedelta(hours=2), "text": "Rockstar's new trailer shows Jason drilling a vault during a bank robbery."},
        {"title": MAP, "link": "/a/map", "date": NOW - timedelta(hours=3), "text": "The trailer shows interiors in Port Gellhorn, Leonida."},
        {"title": "The best Zelda deals this week", "link": "/a/zelda", "date": NOW - timedelta(hours=1), "text": "Nintendo discounts."},
        {"title": "GTA 6 was announced", "link": "/a/old", "date": NOW - timedelta(days=40), "text": "Old news."},
    ])
    web.atom("/other.xml", [{"title": "New GTA 6 heist mechanic lets you drill bank vaults", "link": "/b/heist", "date": NOW - timedelta(hours=1), "text": "Drilling a vault is part of bank heists."}])
    web.rss("/reddit.xml", [
        {"title": BIGFOOT, "link": "/r/1", "date": NOW - timedelta(hours=5), "text": "Frame 1:32, top left, in the swamp."},
        {"title": CAR, "link": "/r/2", "date": NOW - timedelta(hours=4), "text": "Poll."},
    ])
    web.pages["/broken.xml"] = (500, "text/plain", "down")
    s = project["settings"]
    s.data["radar"] = {
        **(s.data.get("radar") or {}),
        "read_articles": False,
        "notifications": {**((s.data.get("radar") or {}).get("notifications") or {}), "max_per_hour": 10},
        "sources": [
            {"key": "press", "name": "Press One", "kind": "rss", "target": web.base + "/press.xml", "trust": "press"},
            {"key": "other", "name": "Press Two", "kind": "rss", "target": web.base + "/other.xml", "trust": "press"},
            {"key": "reddit", "name": "r/GTA6", "kind": "reddit", "target": web.base + "/reddit.xml", "trust": "community", "topic_only": True},
            {"key": "broken", "name": "Broken feed", "kind": "rss", "target": web.base + "/broken.xml", "trust": "press"},
        ],
    }
    yield {"settings": s, "web": web, "store": RadarStore.of(s)}
    web.close()


# ----------------------------------------------------------------------------- scripted AI

class RadarLLM(LLMProvider):
    name = "radar-scripted"

    def __init__(self) -> None:
        super().__init__({})
        self.analysis_calls: list[str] = []

    def complete(self, system: str, prompt: str, *, max_tokens: int = 8000) -> str:
        if "RADAR TRIAGE" in prompt:
            rows = []
            for oid, title in re.findall(r"^(\d+) \| \w+ \| \d+ \| ([^|]+)\|", prompt, re.M):
                junk = "Which car" in title
                rows.append({"id": int(oid), "interest": 2 if junk else 8, "keep": not junk, "categories": [], "reason": "poll" if junk else "new"})
            return json.dumps({"items": rows})
        if "RADAR ANALYSIS" in prompt:
            src = prompt.split("SOURCES (only these exist")[1].split("The strongest verification")[0]
            self.analysis_calls.append(src)
            repair = "Your previous answer was rejected" in prompt
            if "Bigfoot" in src:
                # first claims it is official and states it as a fact: the radar must refuse and lower it
                body = ("Rockstar hid Bigfoot in the swamp at 1:32 of the trailer. It sits top left, right behind the airboat, and it is clearly part of the game world this time. "
                        "Pause the video at that exact second and look past the reeds: the shape is too tall for a bear and it walks upright. Here is exactly where to look, "
                        "and why it matters for the hunting side of the game, because Leonida is full of swamps nobody has explored yet.")
                if repair:
                    body = ("Players spotted what looks like Bigfoot in the swamp at 1:32 of the trailer, top left, right behind the airboat. It is not confirmed by Rockstar yet. "
                            "Pause the video at that exact second and look past the reeds: the shape is too tall for a bear and it seems to walk upright. If it is real, "
                            "it would fit the hunting side of the game, because Leonida is full of swamps nobody has explored yet.")
                return json.dumps(self._answer("hidden_detail", "official", ["secrets"], "short", body, src))
            if "heist" in src.lower():
                return json.dumps(self._answer("new_mechanic", "credible", ["heists", "missions"], "long", "", src, interest=9))
            return json.dumps(self._answer("none", "credible", ["map"], "short", ("Reportedly, the new trailer shows Port Gellhorn interiors you can walk into, from a diner to a pawn shop. That would make this part of Leonida far more than a backdrop, "
                 "and it changes how we will explore the coast. Here is every interior we could spot so far, frame by frame, with the doors that look open and the ones that are just painted on, "
                 "so you know where to go first on day one."), src, interest=5))
        raise AssertionError("unexpected prompt " + prompt[:80])

    @staticmethod
    def _answer(alert: str, ver: str, cats: list[str], fmt: str, body: str, src: str, interest: int = 8) -> dict[str, Any]:
        ids = re.findall(r"^\[(S\d+)\]", src, re.M)
        return {
            "headline": "headline", "what_is_new": "What is new, per the sources.", "why_interesting": "Players care.",
            "verification": ver, "verification_note": "note", "evidence": ids[:2], "categories": cats, "coverage": "low",
            "overlaps_our_videos": False, "unique_angle": "Our angle", "format": fmt, "format_reason": "why", "urgency": "medium",
            "urgency_reason": "why", "scores": {"audience_interest": interest, "novelty": interest, "timeliness": 8, "visual_potential": 8, "angle_strength": 8},
            "alert": {"kind": alert, "reason": "r"},
            "ideas": {
                "titles": ["One title https://fake.example/x", "Two", "Three"], "hooks": ["h1", "h2", "h3"], "explanation": "e", "why_now": "w", "thumbnail": "t",
                "short": {"hook": "Look at this.", "body": body, "visuals": ["v1", "v2", "v3"], "ending": "Did you see it?"} if fmt == "short" else None,
                "long": {"title": "Long", "structure": [{"section": f"S{i}", "points": ["a", "b"]} for i in range(4)], "talking_points": ["a", "b", "c"], "source_ids": ids + ["S99"]} if fmt == "long" else None,
            },
            "caution": "Do not call it confirmed.",
        }


def by_title(store: RadarStore, needle: str) -> dict[str, Any]:
    rows, _ = store.list_opportunities(include_low=True, limit=200)
    return next(o for o in rows if needle.lower() in (o["title"] + " " + " ".join(i["title"] for i in store.items_of(o["id"]))).lower())


# ----------------------------------------------------------------------------- tests

def test_migrations_are_applied_once(tmp_path) -> None:
    db = Database(tmp_path / "f.sqlite3")
    Database(tmp_path / "f.sqlite3")  # opening again applies nothing twice
    with db.connect() as con:
        names = [r[0] for r in con.execute("SELECT name FROM schema_migrations")]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == [m[0] for m in MIGRATIONS]
    assert {"radar_sources", "radar_items", "radar_opportunities", "radar_scans", "radar_notifications", "radar_state"} <= tables


def test_relevance_categories_and_same_story() -> None:
    assert is_relevant("GTA 6 heist gameplay", "")
    assert is_relevant("Grand Theft Auto VI: Lucia", "")
    assert is_relevant("Vice City returns", "Rockstar showed the beach")
    assert not is_relevant("Vice City (2002) speedrun record", "")
    assert not is_relevant("Best Zelda deals", "Nintendo")
    assert is_relevant("Which car first?", "", topic_only=True)
    assert category_hints(HEIST, "") [0] == "heists"
    assert "map" in category_hints(MAP, "")
    assert "secrets" in category_hints(BIGFOOT, "")
    assert same_story(fingerprint(HEIST), fingerprint("New GTA 6 heist mechanic lets you drill bank vaults"))
    assert not same_story(fingerprint(HEIST), fingerprint(MAP))


def test_a_scan_finds_groups_analyses_and_alerts(radar: dict) -> None:
    s, store, llm = radar["settings"], radar["store"], RadarLLM()
    res = engine.run_scan(s, trigger="cli", llm=llm, log=lambda m: None)
    assert res["status"] == "partial"  # the broken feed is reported, the rest worked
    assert res["sources_checked"] == 4 and res["sources_failed"] == 1
    assert any(e["source"] == "Broken feed" for e in res["errors"])

    heist = by_title(store, "heist")
    assert heist["source_count"] == 2  # two outlets, one card
    assert len(store.items_of(heist["id"])) == 2
    rows, _ = store.list_opportunities(include_low=True, limit=100)
    titles = " ".join(i["title"] for o in rows for i in store.items_of(o["id"]))
    assert "Zelda" not in titles and "was announced" not in titles  # off-topic and too old: never proposed

    assert heist["analysis_status"] == "done" and heist["format"] == "long" and heist["verification"] == "credible"
    assert 0 <= heist["score"] <= 100 and heist["priority"] in ("high", "medium", "low")
    assert set(heist["categories"]) == {"heists", "missions"}
    urls = {src["url"] for src in heist["analysis"]["sources"]}
    assert urls == {radar["web"].base + "/a/heist", radar["web"].base + "/b/heist"}  # only real entries
    assert "S99" not in heist["ideas"]["long"]["source_ids"]  # a source id the model invented is dropped
    assert "https://" not in heist["ideas"]["titles"][0]  # and so is a URL it wrote

    foot = by_title(store, "bigfoot")
    assert foot["verification"] == "community"  # the model said official: the sources do not allow it
    assert "lowered" in foot["analysis"]["verification_note"]
    assert an.HEDGE.search(foot["ideas"]["short"]["body"])  # the script that stated it as a fact was sent back
    assert sum("Bigfoot" in c for c in llm.analysis_calls) == 2

    car = by_title(store, "which car")
    assert car["analysis_status"] == "skipped" and car["priority"] == "low"  # judged weak in the first pass
    assert by_title(store, "port gellhorn")["analysis_status"] == "done"

    notes = store.list_notifications()
    assert notes and all(n["opportunity_id"] for n in notes)
    assert len({n["opportunity_id"] for n in notes}) == len(notes)
    assert any(n["kind"] == "new_mechanic" for n in notes)
    scan = store.list_scans(1)[0]
    assert scan["status"] == "partial" and scan["opportunities_new"] == 4 and scan["analyses"] == 3


def test_next_scans_skip_what_is_known_and_pick_up_edits(radar: dict) -> None:
    s, store, web = radar["settings"], radar["store"], radar["web"]
    engine.run_scan(s, trigger="cli", llm=RadarLLM(), log=lambda m: None)
    n_opps = store.counts()["discoveries"]
    n_alerts = len(store.list_notifications())
    res = engine.run_scan(s, trigger="manual", llm=RadarLLM(), log=lambda m: None)
    assert res["items_new"] == 0 and res["opportunities_new"] == 0 and res["analyses"] == 0
    assert store.counts()["discoveries"] == n_opps and len(store.list_notifications()) == n_alerts  # nothing twice
    # the publisher edits the heist article (same guid, new text): stored as an update and read again
    web.rss("/press.xml", [{"title": HEIST, "link": "/a/heist", "date": NOW - timedelta(hours=2), "text": "UPDATE: Rockstar confirmed vault drilling."}])
    res = engine.run_scan(s, trigger="manual", llm=RadarLLM(), log=lambda m: None)
    assert res["items_updated"] == 1 and res["analyses"] == 1
    assert len(store.list_notifications()) == n_alerts  # an update never alerts twice


def test_a_failing_source_backs_off_and_the_schedule_respects_it(radar: dict) -> None:
    s, store, web = radar["settings"], radar["store"], radar["web"]
    engine.run_scan(s, trigger="cli", llm=RadarLLM(), log=lambda m: None)
    broken = next(x for x in store.list_sources() if x["key"] == "broken")
    assert broken["consecutive_failures"] == 1 and broken["next_attempt_at"] > iso(utcnow())
    hits = web.hits.get("/broken.xml", 0)
    engine.run_scan(s, trigger="schedule", llm=RadarLLM(), log=lambda m: None)
    assert web.hits.get("/broken.xml", 0) == hits  # still waiting
    web.rss("/broken.xml", [])
    engine.run_scan(s, trigger="manual", llm=RadarLLM(), log=lambda m: None)  # "Scan now" retries everything
    broken = store.get_source(broken["id"])
    assert broken["consecutive_failures"] == 0 and broken["last_error"] is None


def test_the_ai_being_down_keeps_discoveries_for_later(radar: dict) -> None:
    s, store = radar["settings"], radar["store"]

    class Down(RadarLLM):
        def complete(self, system: str, prompt: str, *, max_tokens: int = 8000) -> str:
            from gta_factory.app.core.errors import ProviderError

            raise ProviderError("Ollama is not running")

    res = engine.run_scan(s, trigger="cli", llm=Down(), log=lambda m: None)
    assert res["status"] == "partial" and store.counts()["pending_analysis"] == 4
    res = engine.run_scan(s, trigger="manual", llm=RadarLLM(), log=lambda m: None)
    assert res["analyses"] == 3 and store.counts()["pending_analysis"] == 0


def test_one_scan_at_a_time(radar: dict) -> None:
    store = radar["store"]
    assert store.acquire_lock("host:1")
    assert not store.acquire_lock("other-host:2")
    assert engine.run_scan(radar["settings"], trigger="cli", llm=RadarLLM(), log=lambda m: None)["status"] == "busy"
    store.release_lock("host:1")
    assert store.acquire_lock("other-host:2")
    # an expired lease is taken over (the scanning process died)
    store.set_state("lock", {"owner": "other-host:2", "expires_at": iso(utcnow() - timedelta(minutes=1))})
    assert store.acquire_lock("host:3")
    # a lease held by a process of this computer that no longer exists too
    import socket

    store.set_state("lock", {"owner": f"{socket.gethostname()}:999999", "expires_at": iso(utcnow() + timedelta(minutes=20))})
    assert store.acquire_lock(store.owner_id())


def test_schedule_and_tick(radar: dict) -> None:
    s, store = radar["settings"], radar["store"]
    s.data["radar"]["analysis_enabled"] = False
    info = engine.schedule(s, store)
    assert info["due"] and info["status"] == "no_scheduler"
    assert engine.tick(s, via="test", log=lambda m: None) is not None  # due: scans
    info = engine.schedule(s, store)
    assert not info["due"] and info["status"] == "active"
    nxt = datetime.fromisoformat(info["next_scan_at"])
    last = datetime.fromisoformat(info["last_scan"]["finished_at"])
    assert abs((nxt - last) - timedelta(hours=2)) < timedelta(seconds=2)
    assert engine.tick(s, via="test", log=lambda m: None) is None  # not due: nothing
    # a failed scan is retried sooner than the interval
    store.finish_scan(store.list_scans(1)[0]["id"], status="failed")
    nxt = datetime.fromisoformat(engine.schedule(s, store)["next_scan_at"])
    assert nxt - datetime.fromisoformat(store.list_scans(1)[0]["finished_at"]) == timedelta(minutes=20)
    # interval changed in the dashboard, then paused
    from gta_factory.modules.radar.api import update_settings

    assert update_settings(s, {"interval_hours": 6})["interval_hours"] == 6
    assert update_settings(s, {"enabled": False})["enabled"] is False
    assert engine.schedule(s, store)["status"] == "paused" and engine.tick(s, via="test", log=lambda m: None) is None
    with pytest.raises(FactoryError):
        update_settings(s, {"interval_hours": 100})


def test_guards_and_score() -> None:
    assert an.cap("official", "community") == "community" and an.cap("rumor", "official") == "rumor"
    assert an.ceiling([{"trust": "community"}, {"trust": "press"}]) == "credible"
    assert an.ceiling([{"trust": "official"}]) == "official"
    a = {"scores": {k: 10 for k in ("audience_interest", "novelty", "timeliness", "visual_potential", "angle_strength")}, "verification": "official", "coverage": "medium"}
    fresh = an.score(a, published_at=iso(utcnow() - timedelta(hours=1)), outlets=1)
    old = an.score(a, published_at=iso(utcnow() - timedelta(days=10)), outlets=1)
    rumor = an.score({**a, "verification": "rumor"}, published_at=iso(utcnow()), outlets=1)
    ours = an.score({**a, "overlaps_our_videos": True}, published_at=iso(utcnow()), outlets=1)
    assert fresh["score"] == 100 and fresh["priority"] == "high"
    assert old["score"] < fresh["score"] and rumor["score"] < fresh["score"] and ours["score"] < 75
    assert an.urgency({"verification": "official", "urgency": "low"}, iso(utcnow())) == "high"


def test_sources_typed_by_the_operator() -> None:
    assert normalize_source({"kind": "reddit", "target": "https://www.reddit.com/r/GTA6/comments/x"})["target"] == "GTA6"
    yt = normalize_source({"kind": "youtube", "target": "https://www.youtube.com/channel/UCJvUEELQilCGdh-2riqqukQ"})
    assert yt["target"] == "UCJvUEELQilCGdh-2riqqukQ" and yt["trust"] == "creator"
    assert normalize_source({"kind": "youtube", "target": "https://www.youtube.com/@GTAbites"})["target"] == "@GTAbites"
    with pytest.raises(FactoryError):
        normalize_source({"kind": "rss", "target": "gtaforums.com"})
    with pytest.raises(FactoryError):
        normalize_source({"kind": "rss", "target": "https://x.com/feed", "trust": "very"})


def test_alerts_are_throttled_into_a_digest(radar: dict) -> None:
    from gta_factory.modules.radar.notify import notify_scan

    s, store = radar["settings"], radar["store"]
    s.data["radar"]["notifications"]["max_per_hour"] = 2
    opps = []
    for i in range(5):
        oid = store.create_opportunity(title=f"GTA 6 thing {i}", fingerprint=[f"t{i}"], published_at=None, categories=["secrets"])
        store.update_opportunity(oid, score=90 - i, priority="high", analysis={"verification": "credible", "alert": {"kind": "none"}}, analysis_status="done")
        opps.append(store.get_opportunity(oid))
    assert notify_scan(store, s, opps) == 3  # 2 alerts + 1 digest
    kinds = [n["kind"] for n in store.list_notifications()]
    assert kinds.count("high_opportunity") == 2 and kinds.count("digest") == 1
    assert notify_scan(store, s, [store.get_opportunity(o["id"]) for o in opps]) == 0  # never twice


def test_external_alerts_use_env_credentials(radar: dict, monkeypatch) -> None:
    from gta_factory.modules.radar import notify

    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(notify.httpx, "post", lambda url, json, timeout: sent.append({"url": url, **json}) or httpx.Response(200))
    s, store = radar["settings"], radar["store"]
    oid = store.create_opportunity(title="Rockstar confirms GTA 6 heists", fingerprint=["x"], published_at=None, categories=["heists"])
    store.update_opportunity(oid, score=88, priority="high", analysis={"verification": "official", "alert": {"kind": "official_announcement"}}, analysis_status="done")
    assert notify.notify_scan(store, s, [store.get_opportunity(oid)]) == 1
    assert sent and sent[0]["chat_id"] == "42" and "Official GTA VI news" in sent[0]["text"]
    assert store.list_notifications()[0]["delivered"] == {"telegram": "ok"}


def test_dashboard_radar_api(radar: dict, monkeypatch) -> None:
    from gta_factory.app.dashboard import server
    from gta_factory.app.dashboard.server import make_server
    from gta_factory.modules.radar import worker

    monkeypatch.setattr(server, "spawn_runner", lambda *a, **k: None)  # no real video job in a test

    s, store = radar["settings"], radar["store"]
    engine.run_scan(s, trigger="cli", llm=RadarLLM(), log=lambda m: None)
    httpd = make_server(s, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    h = {"X-Factory": "1"}
    try:
        o = httpx.get(base + "/api/radar").json()
        assert o["counts"]["discoveries"] == 4 and o["sources"]["total"] == 4 and o["schedule"]["last_success_at"]
        feed = httpx.get(base + "/api/radar/opportunities").json()
        assert feed["total"] == 3 and all(c["source"]["url"].startswith("http") for c in feed["items"])  # the weak one is hidden
        assert httpx.get(base + "/api/radar/opportunities?include_low=1").json()["total"] == 4
        assert httpx.get(base + "/api/radar/opportunities?category=heists").json()["total"] == 1
        heist = next(c for c in feed["items"] if "heists" in c["categories"])
        detail = httpx.get(base + f"/api/radar/opportunities/{heist['id']}").json()
        assert len(detail["sources"]) == 2 and detail["ideas"]["titles"] and detail["analysis"]["what_is_new"]
        assert httpx.put(base + f"/api/radar/opportunities/{heist['id']}", json={"status": "approved"}).status_code == 403  # no X-Factory header
        assert httpx.put(base + f"/api/radar/opportunities/{heist['id']}", json={"status": "approved"}, headers=h).json()["status"] == "approved"
        assert httpx.get(base + "/api/radar/opportunities?status=approved").json()["total"] == 1
        assert httpx.put(base + f"/api/radar/opportunities/{heist['id']}", json={"status": "maybe"}, headers=h).status_code == 400
        added = httpx.post(base + "/api/radar/sources", json={"kind": "reddit", "target": "r/GTAVI"}, headers=h).json()["source"]
        assert added["target"] == "GTAVI" and added["origin"] == "dashboard" and added["topic_only"] == 1
        assert httpx.post(base + "/api/radar/sources", json={"kind": "reddit", "target": "GTAVI"}, headers=h).status_code == 400  # duplicate
        cfg_src = next(x for x in store.list_sources() if x["origin"] == "config")
        assert httpx.delete(base + f"/api/radar/sources/{cfg_src['id']}", headers=h).status_code == 400  # pause it instead
        assert httpx.put(base + f"/api/radar/sources/{cfg_src['id']}", json={"enabled": False}, headers=h).json()["source"]["enabled"] == 0
        assert httpx.delete(base + f"/api/radar/sources/{added['id']}", headers=h).json()["ok"]
        assert httpx.put(base + "/api/radar/settings", json={"interval_hours": 3}, headers=h).json()["interval_hours"] == 3
        n = httpx.get(base + "/api/radar/notifications").json()
        assert n["unread"] >= 1
        assert httpx.post(base + "/api/radar/notifications/read", json={}, headers=h).json()["unread"] == 0
        monkeypatch.setattr(worker, "spawn_scan", lambda settings: 4242)
        assert httpx.post(base + "/api/radar/scan", json={}, headers=h).json()["pid"] == 4242
        assert httpx.get(base + "/api/radar/scans").json()["scans"][0]["trigger"] == "cli"
        # an approved idea goes to the factory as a news job made from its press articles
        r = httpx.post(base + f"/api/radar/opportunities/{heist['id']}/video", json={"format": "long"}, headers=h)
        assert r.status_code == 201, r.text
        from gta_factory.app.database.db import get_db

        job = get_db(s.db_path).get_job(r.json()["id"])
        assert job and set(job["urls"]) == {radar["web"].base + "/a/heist", radar["web"].base + "/b/heist"}
        foot = by_title(store, "bigfoot")  # a community find has no article to read
        assert httpx.post(base + f"/api/radar/opportunities/{foot['id']}/video", json={}, headers=h).status_code == 400
    finally:
        httpd.shutdown()


def test_the_full_article_is_read_once_and_cached(radar: dict) -> None:
    s, store, web = radar["settings"], radar["store"], radar["web"]
    s.data["radar"]["read_articles"] = True
    para = "Rockstar's new trailer shows Jason drilling a bank vault while Lucia watches the police scanner. " * 8
    web.pages["/a/heist"] = (200, "text/html", f"<html><head><title>{HEIST}</title></head><body><article><h1>{HEIST}</h1><p>{para}</p><p>{para}</p></article></body></html>")
    llm = RadarLLM()
    engine.run_scan(s, trigger="cli", llm=llm, log=lambda m: None)
    heist = by_title(store, "heist")
    assert any("Lucia watches the police scanner" in c for c in llm.analysis_calls)  # the AI read the article, not just the teaser
    lead = next(i for i in store.items_of(heist["id"]) if i["url"].endswith("/a/heist"))
    assert "police scanner" in lead["meta"]["text"]
    hits = web.hits["/a/heist"]
    store.update_opportunity(heist["id"], needs_reanalysis=1)
    engine.run_scan(s, trigger="manual", llm=llm, log=lambda m: None)
    assert web.hits["/a/heist"] == hits  # cached: read once


def test_cli_radar_commands(radar: dict, capsys) -> None:
    from gta_factory.app.cli.main import main
    from gta_factory.app.config import settings as settings_mod

    s = radar["settings"]
    engine.run_scan(s, trigger="cli", llm=RadarLLM(), log=lambda m: None)
    orig = settings_mod.get_settings
    import gta_factory.app.cli.main as cli

    cli.get_settings = lambda *a, **k: s  # the CLI reads the test project's settings
    try:
        assert main(["radar", "status"]) == 0
        out = capsys.readouterr().out
        assert "4 discoveries" in out and "4 sources" in out
        assert main(["radar", "list", "--all"]) == 0
        assert "(4 of 4)" in capsys.readouterr().out
        heist = by_title(radar["store"], "heist")
        assert main(["radar", "set", str(heist["id"]), "published"]) == 0
        assert radar["store"].get_opportunity(heist["id"])["status"] == "published"
        assert main(["radar", "sources", "add", "GTA", "--kind", "reddit"]) == 0
        assert main(["radar", "sources"]) == 0
        assert "r/GTA" in capsys.readouterr().out
    finally:
        cli.get_settings = orig


def test_the_background_worker_scans_when_due(radar: dict) -> None:
    from gta_factory.modules.radar.worker import RadarWorker

    s, store = radar["settings"], radar["store"]
    s.data["radar"]["analysis_enabled"] = False
    w = RadarWorker(s, via="test", poll_seconds=0.2, log=lambda m: None)
    w.start()
    try:
        for _ in range(100):
            if store.last_scan():
                break
            threading.Event().wait(0.1)
    finally:
        w.stop()
        w.join(5)
    assert store.last_scan()["trigger"] == "schedule" and not w.is_alive()
    assert (store.get_state("heartbeat") or {}).get("kind") == "test"


def test_system_schedule_cron_and_launchd(radar: dict, tmp_path, monkeypatch) -> None:
    import os
    import plistlib
    import subprocess

    from gta_factory.modules.radar import worker

    s = radar["settings"]
    # cron: a stand-in crontab binary that keeps the table in a file
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    table = tmp_path / "crontab.txt"
    (bin_dir / "crontab").write_text(f'#!/bin/sh\nif [ "$1" = "-l" ]; then [ -f "{table}" ] && cat "{table}" || exit 1; else cat > "{table}"; fi\n')
    (bin_dir / "crontab").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(worker.platform, "system", lambda: "Linux")
    table.write_text("0 3 * * * echo backup\n")
    assert "installed" in worker.install_schedule(s)
    assert worker.schedule_status(s)["installed"]
    assert "already installed" in worker.ensure_schedule(s)
    lines = table.read_text().splitlines()
    assert lines[0] == "0 3 * * * echo backup" and "radar tick --via cron" in lines[1] and len(lines) == 2  # the user's own lines are kept
    worker.install_schedule(s)
    assert len(table.read_text().splitlines()) == 2  # never twice
    assert worker.uninstall_schedule(s) == "cron entry removed"
    assert table.read_text().splitlines() == ["0 3 * * * echo backup"]
    # launchd: the agent file, loaded with launchctl
    monkeypatch.setattr(worker.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(worker.Path, "home", lambda: tmp_path / "home")
    calls: list[list[str]] = []
    monkeypatch.setattr(worker.subprocess, "run", lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    msg = worker.install_schedule(s)
    plist = plistlib.loads(worker.launchd_plist(s).read_bytes())
    assert plist["ProgramArguments"][-4:] == ["radar", "tick", "--via", "launchd"] and plist["StartInterval"] == 900 and plist["RunAtLoad"]
    assert plist["WorkingDirectory"] == str(s.root) and "launchd agent installed" in msg
    assert ["launchctl", "load", "-w", str(worker.launchd_plist(s))] in calls
    assert worker.uninstall_schedule(s) == "launchd agent removed" and not worker.launchd_plist(s).exists()
