"""One radar scan, and when the next one is due.

``run_scan`` is safe to call from anywhere (dashboard thread, CLI, launchd, cron):
a lease in the database lets only one scan run at a time, a scan that crashed is
closed as ``interrupted`` by the next one, and everything a scan learns is
written as it goes, so a restart loses nothing:

* a failing source is retried later with exponential back-off, the others go on;
* entries are stored before any AI call: when the AI is down, the discoveries wait
  as *pending* and the next scan analyses them;
* an opportunity whose analysis keeps failing is marked *failed* after a few tries
  (visible in the dashboard, re-runnable) instead of blocking the queue.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ...app.core.errors import FactoryError, ProviderError
from ...app.core.logging import job_logger
from .feeds import Entry, canonical, fetch_source, item_trust
from .relevance import category_hints, find_cluster, fingerprint, is_relevant
from .store import RadarStore, iso, parse_iso, utcnow

LEVEL_RANK = {"rumor": 0, "community": 1, "credible": 2, "official": 3}
TRUST_LEVEL = {"official": "official", "press": "credible", "creator": "community", "community": "community", "web": "community"}


# ----------------------------------------------------------------------------- configuration

def config(settings: Any, store: RadarStore | None = None) -> dict[str, Any]:
    """The ``radar`` section of config.yaml, with the values changed in the dashboard on top."""
    cfg = dict(settings.get("radar") or {})
    over = (store or RadarStore.of(settings)).get_state("settings", {}) or {}
    interval = float(over.get("interval_hours", cfg.get("interval_hours", 2)) or 2)
    cfg["interval_hours"] = max(0.25, min(48.0, interval))
    cfg["enabled"] = bool(over.get("enabled", cfg.get("enabled", True)))
    return cfg


def configured_sources(settings: Any) -> list[dict[str, Any]]:
    return [s for s in (settings.get("radar.sources") or []) if isinstance(s, dict) and s.get("key") and s.get("kind") and s.get("target")]


def log_path(settings: Any) -> Path:
    return settings.jobs_dir / ".radar" / "radar.log"


def radar_logger(settings: Any) -> Callable[[str], None]:
    logger = job_logger("radar", log_path(settings))
    return logger.info


# ----------------------------------------------------------------------------- schedule

def schedule(settings: Any, store: RadarStore | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Last scans, next scheduled scan and who is keeping the schedule."""
    store = store or RadarStore.of(settings)
    now = now or utcnow()
    cfg = config(settings, store)
    last = store.last_scan()
    last_ok = store.last_scan(statuses=("success", "partial"))
    running = store.running_scan()
    lock = store.lock_info()
    interval = timedelta(hours=cfg["interval_hours"])
    nxt: datetime | None = None
    if cfg["enabled"]:
        if not last:
            nxt = now
        else:
            done = parse_iso(last.get("finished_at")) or parse_iso(last.get("started_at")) or now
            retry = timedelta(minutes=float(cfg.get("retry_minutes", 20)))
            nxt = done + (min(interval, retry) if last["status"] in ("failed", "interrupted") else interval)
    beat = store.get_state("heartbeat") or {}
    beat_at = parse_iso(beat.get("at"))
    scheduler_alive = bool(beat_at and (now - beat_at) < timedelta(minutes=float(cfg.get("heartbeat_stale_minutes", 40))))
    if not cfg["enabled"]:
        status = "paused"
    elif running and lock:
        status = "scanning"
    elif not scheduler_alive:
        status = "no_scheduler"  # nothing will start the next scan: dashboard closed and no service installed
    else:
        status = "active"
    return {
        "enabled": cfg["enabled"],
        "interval_hours": cfg["interval_hours"],
        "status": status,
        "last_scan": last,
        "last_success_at": (last_ok or {}).get("finished_at"),
        "next_scan_at": iso(nxt) if nxt else None,
        "due": bool(nxt and nxt <= now),
        "running": running if lock else None,
        "scheduler": {"kind": beat.get("kind"), "at": beat.get("at"), "alive": scheduler_alive},
    }


def heartbeat(settings: Any, kind: str, store: RadarStore | None = None) -> None:
    (store or RadarStore.of(settings)).set_state("heartbeat", {"kind": kind, "at": iso(utcnow())})


def tick(settings: Any, *, via: str = "worker", log: Callable[[str], None] | None = None) -> dict[str, Any] | None:
    """Scan only when one is due (what the scheduler calls every few minutes). Returns the scan summary or None."""
    store = RadarStore.of(settings)
    store.seed_sources(configured_sources(settings))
    heartbeat(settings, via, store)
    info = schedule(settings, store)
    if not info["due"] or info["running"]:
        return None
    return run_scan(settings, trigger="schedule", log=log)


# ----------------------------------------------------------------------------- ingestion

def _outlets(items: list[dict[str, Any]]) -> int:
    return len({((i.get("meta") or {}).get("publisher") or i.get("source_name") or i.get("source_id")) for i in items}) or 1


def _ingest(store: RadarStore, settings: Any, cfg: dict[str, Any], source: dict[str, Any], e: Entry, clusters: list[dict[str, Any]], stats: dict[str, int], now: datetime) -> None:
    prev = store.get_item_by_uid(e.uid)
    if prev:
        if prev["content_hash"] == e.content_hash:
            return
        # the same entry, edited by its publisher: keep the new text, and re-read the story if it matters
        store.update_item(int(prev["id"]), title=e.title, summary=e.summary, content_hash=e.content_hash, updated_at=iso(now))
        stats["items_updated"] += 1
        if prev.get("opportunity_id"):
            opp = store.get_opportunity(int(prev["opportunity_id"]))
            if opp and opp["analysis_status"] == "done" and (now - (parse_iso(opp["created_at"]) or now)) < timedelta(days=7):
                store.update_opportunity(int(opp["id"]), needs_reanalysis=1)
        return
    pub = parse_iso(e.published_at)
    max_age = timedelta(days=float(cfg.get("max_entry_age_days", 7)))
    too_old = bool(pub and now - pub > max_age)
    relevant = is_relevant(e.title, e.summary, topic_only=bool(source.get("topic_only"))) and not too_old
    canon = canonical(e.url)
    same_url = store.item_with_url(canon)
    trust = item_trust(source, e.url, settings.source_tier)
    item = {
        "source_id": source["id"], "uid": e.uid, "url": e.url, "canonical_url": canon, "title": e.title, "summary": e.summary,
        "author": e.author, "published_at": e.published_at, "discovered_at": iso(now), "content_hash": e.content_hash,
        "relevant": relevant, "hints": category_hints(e.title, e.summary) if relevant else [], "trust": trust,
        "meta": {**e.meta, **({"too_old": True} if too_old else {})},
    }
    iid = store.insert_item(item)
    stats["items_new"] += 1
    if not relevant:
        return
    stats["items_relevant"] += 1
    target = None
    if same_url and same_url.get("opportunity_id"):
        target = store.get_opportunity(int(same_url["opportunity_id"]))
    fp = fingerprint(e.title)
    if target is None:
        target = find_cluster(fp, clusters, float(cfg.get("same_story_threshold", 0.5)))
    if target is None:
        oid = store.create_opportunity(title=e.title, fingerprint=fp, published_at=e.published_at, categories=item["hints"])
        store.update_item(iid, opportunity_id=oid)
        store.update_opportunity(oid, summary=e.summary[:400])
        new = store.get_opportunity(oid)
        clusters.append(new)  # type: ignore[arg-type]
        stats["opportunities_new"] += 1
        return
    # one more outlet telling the same story: a supporting source, never a second card
    oid = int(target["id"])
    store.update_item(iid, opportunity_id=oid)
    items = store.items_of(oid)
    fields: dict[str, Any] = {"source_count": _outlets(items)}
    first = min((i["published_at"] for i in items if i.get("published_at")), default=target.get("published_at"))
    if first:
        fields["published_at"] = first
    if target["analysis_status"] == "done":
        # a stronger source than the analysis knew (e.g. Rockstar confirms a community find): read it again
        known = LEVEL_RANK.get(target.get("verification") or "rumor", 0)
        if LEVEL_RANK[TRUST_LEVEL.get(trust, "community")] > known:
            fields["needs_reanalysis"] = 1
    elif target["analysis_status"] == "skipped" and fields["source_count"] >= int(cfg.get("rejudge_at_outlets", 3)):
        fields.update(analysis_status="pending", triage=None, priority=None)  # more and more outlets cover it: judge it again
    store.update_opportunity(oid, **fields)
    stats["items_clustered"] += 1


def _fetch_all(store: RadarStore, settings: Any, cfg: dict[str, Any], owner: str, stats: dict[str, int], errors: list[dict[str, str]], log: Callable[[str], None], *, force: bool) -> None:
    now = utcnow()
    sources = [s for s in store.list_sources() if s["enabled"]] if force else store.due_sources(now)
    candidates = store.recent_opportunities(now - timedelta(days=float(cfg.get("same_story_days", 7))))
    timeout = float(cfg.get("request_timeout_seconds", 25))
    per_source = int(cfg.get("max_entries_per_source", 60))
    base = float(cfg.get("backoff_minutes", 15))
    for src in sources:
        stats["sources_checked"] += 1
        t = utcnow()
        try:
            res = fetch_source(src, timeout=timeout)
        except (ProviderError, FactoryError) as exc:
            n = int(src.get("consecutive_failures") or 0) + 1
            wait = min(base * (2 ** (n - 1)), float(cfg.get("max_backoff_hours", 12)) * 60)
            store.update_source(int(src["id"]), last_checked_at=iso(t), last_error=exc.message[:300], consecutive_failures=n, next_attempt_at=iso(t + timedelta(minutes=wait)))
            stats["sources_failed"] += 1
            errors.append({"source": src["name"], "error": exc.message[:300]})
            log(f"radar: {src['name']} failed ({exc.message[:120]}); next try in {wait:.0f} min")
            continue
        before = stats["items_new"]
        for e in res.entries[:per_source]:
            try:
                _ingest(store, settings, cfg, src, e, candidates, stats, t)
            except Exception as exc:  # one malformed entry must not lose the rest of the feed
                errors.append({"source": src["name"], "error": f"entry skipped: {type(exc).__name__}: {str(exc)[:160]}"})
        store.update_source(
            int(src["id"]),
            last_checked_at=iso(t), last_success_at=iso(t), last_error=None, consecutive_failures=0, next_attempt_at=None,
            etag=res.etag, last_modified=res.last_modified, options=res.options or src.get("options") or {},
            items_total=int(src.get("items_total") or 0) + (stats["items_new"] - before),
        )
        added = stats["items_new"] - before
        log(f"radar: {src['name']}: " + ("unchanged" if res.status == 304 else f"{len(res.entries)} entries, {added} new"))
        store.renew_lock(owner)


# ----------------------------------------------------------------------------- analysis

def _article_texts(settings: Any, store: RadarStore, items: list[dict[str, Any]], limit: int, log: Callable[[str], None]) -> dict[int, str]:
    """Full text of the best web articles of a story (cached on the entry), so the AI reads more than a teaser."""
    from ..article_extraction.extractor import extract_article

    out: dict[int, str] = {}
    fetched = 0
    for it in items:
        meta = it.get("meta") or {}
        if meta.get("text"):
            out[int(it["id"])] = meta["text"]
            continue
        if fetched >= limit or it.get("trust") not in ("official", "press") or it.get("source_kind") in ("youtube", "reddit"):
            continue
        fetched += 1
        try:
            art = extract_article(it["url"], tier_of=settings.source_tier, allow_render=False)
        except Exception as exc:  # paywall, blocked, JavaScript-only: the feed summary is used instead
            log(f"radar: article text unavailable for {it['url'][:80]} ({type(exc).__name__})")
            continue
        text = " ".join(art.text.split()[:900])
        out[int(it["id"])] = text
        store.update_item(int(it["id"]), meta={**meta, "text": text})
    return out


def _ours(settings: Any, store: RadarStore, exclude: int) -> list[str]:
    """Headlines of our produced videos and of the radar ideas already approved or published."""
    from ...app.database.db import get_db

    titles = [s["headline"] for s in get_db(settings.db_path).list_stories()[:40]]
    rows, _ = store.list_opportunities(status="approved", limit=30)
    rows2, _ = store.list_opportunities(status="published", limit=30)
    titles += [r["title"] for r in rows + rows2 if int(r["id"]) != exclude]
    return titles


def _analyse_queue(store: RadarStore, settings: Any, cfg: dict[str, Any], owner: str, stats: dict[str, int], errors: list[dict[str, str]], log: Callable[[str], None], llm: Any, today: str) -> list[dict[str, Any]]:
    from .analyze import analyze, score, source_list, triage, urgency

    analysed: list[dict[str, Any]] = []
    # 1. first pass on every new headline, in batches
    batch = int(cfg.get("triage_batch", 30))
    min_interest = int(cfg.get("triage_min_interest", 5))
    for _ in range(int(cfg.get("triage_batches_per_scan", 3))):
        todo = store.pending_triage(batch)
        if not todo:
            break
        try:
            rated = triage(llm, todo, store.items_of, today)
        except FactoryError as exc:
            errors.append({"source": "AI", "error": f"first pass failed: {exc.message[:200]}"})
            log(f"radar: first pass failed ({exc.message[:120]}); the headlines wait for the next scan")
            break
        for o in todo:
            r = rated.get(int(o["id"]))
            if r is None:
                continue
            cats = r["categories"] or o.get("categories") or []
            if r["keep"] and r["interest"] >= min_interest:
                store.update_opportunity(int(o["id"]), triage=r, categories=cats)
            else:
                store.update_opportunity(int(o["id"]), triage=r, categories=cats, analysis_status="skipped", score=r["interest"] * 5, priority="low", summary=r["reason"] or o.get("summary"))
        store.renew_lock(owner)
        if len(todo) < batch:
            break
    # 2. full analysis of the best ones
    for o in store.analysis_queue(int(cfg.get("max_analyses_per_scan", 8)), int(cfg.get("max_analysis_attempts", 3))):
        oid = int(o["id"])
        items = store.items_of(oid)
        if not items:
            store.update_opportunity(oid, analysis_status="failed", analysis_error="no source left")
            continue
        texts = _article_texts(settings, store, items, int(cfg.get("article_fetches_per_story", 3)), log) if cfg.get("read_articles", True) else {}
        sources = source_list(items, texts)
        attempts = int(o.get("analysis_attempts") or 0) + 1
        try:
            a = analyze(llm, o, sources, _ours(settings, store, oid), today)
        except FactoryError as exc:
            failed = attempts >= int(cfg.get("max_analysis_attempts", 3))
            store.update_opportunity(oid, analysis_attempts=attempts, analysis_error=exc.message[:400], **({"analysis_status": "failed", "needs_reanalysis": 0} if failed else {}))
            errors.append({"source": "AI", "error": f"#{oid}: {exc.message[:200]}"})
            log(f"radar: analysis of #{oid} failed ({exc.message[:120]})")
            continue
        sc = score(a, published_at=o.get("published_at"), outlets=_outlets(items), high=int(cfg.get("high_priority_score", 75)), medium=int(cfg.get("medium_priority_score", 55)))
        a["sources"] = [{k: v for k, v in s.items() if k != "text"} for s in sources]
        a["score_breakdown"] = sc["breakdown"]
        ideas = a.pop("ideas")
        store.update_opportunity(
            oid,
            title=(a.get("headline") or o["title"])[:200],
            summary=a.get("what_is_new") or o.get("summary"),
            categories=a["categories"],
            verification=a["verification"],
            score=sc["score"],
            priority=sc["priority"],
            format=a["format"],
            urgency=urgency(a, o.get("published_at")),
            analysis=a,
            ideas=ideas,
            analysis_status="done",
            analysis_attempts=attempts,
            analysis_error=None,
            analyzed_at=iso(utcnow()),
            needs_reanalysis=0,
            source_count=_outlets(items),
        )
        stats["analyses"] += 1
        done = store.get_opportunity(oid)
        if done:
            analysed.append(done)
        log(f"radar: #{oid} analysed: score {sc['score']} ({sc['priority']}), {a['verification']}, {a['format']} - {done['title'][:70] if done else ''}")
        store.renew_lock(owner)
    return analysed


# ----------------------------------------------------------------------------- one scan

def run_scan(settings: Any, *, trigger: str = "manual", log: Callable[[str], None] | None = None, llm: Any = None, force_sources: bool = False) -> dict[str, Any]:
    """Fetch, store, group, analyse and alert. Returns the scan summary (``status`` is "busy" when another scan runs)."""
    from datetime import date

    from ...providers.llm import get_llm
    from .notify import notify_scan

    log = log or radar_logger(settings)
    store = RadarStore.of(settings)
    cfg = config(settings, store)
    owner = store.owner_id()
    if not store.acquire_lock(owner, int(cfg.get("lock_minutes", 30)) * 60):
        log("radar: another scan is running; this one is skipped")
        return {"status": "busy"}
    scan_id = store.start_scan(trigger, owner)
    stats = {k: 0 for k in ("sources_checked", "sources_failed", "items_new", "items_updated", "items_relevant", "items_clustered", "opportunities_new", "analyses", "notifications")}
    errors: list[dict[str, str]] = []
    status = "failed"
    try:
        store.seed_sources(configured_sources(settings))
        log(f"radar: scan #{scan_id} ({trigger}) started")
        _fetch_all(store, settings, cfg, owner, stats, errors, log, force=force_sources or trigger == "manual")
        analysed: list[dict[str, Any]] = []
        if cfg.get("analysis_enabled", True):
            try:
                llm = llm or get_llm(settings)
                llm.check()
            except FactoryError as exc:
                llm = None
                errors.append({"source": "AI", "error": f"AI provider unavailable: {exc.message[:200]}"})
                log(f"radar: AI unavailable ({exc.message[:120]}); discoveries are kept and analysed at the next scan")
            if llm is not None:
                analysed = _analyse_queue(store, settings, cfg, owner, stats, errors, log, llm, date.today().isoformat())
        port = int(settings.get("dashboard.port", 8600))
        stats["notifications"] = notify_scan(store, settings, analysed, dashboard_url=f"http://127.0.0.1:{port}/#radar", log=log)
        all_sources_failed = stats["sources_checked"] > 0 and stats["sources_failed"] == stats["sources_checked"]
        status = "failed" if all_sources_failed else "partial" if errors else "success"
    except Exception as exc:  # unexpected: recorded on the scan, the lock is released, the next scan retries
        errors.append({"source": "radar", "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        log(f"radar: scan #{scan_id} crashed: {type(exc).__name__}: {exc}")
        status = "failed"
    finally:
        store.finish_scan(scan_id, status=status, errors=errors[:50], **{k: v for k, v in stats.items() if k in ("sources_checked", "sources_failed", "items_new", "items_updated", "opportunities_new", "analyses", "notifications")})
        store.release_lock(owner)
    log(f"radar: scan #{scan_id} {status}: {stats['sources_checked']} source(s) ({stats['sources_failed']} failed), {stats['items_new']} new entries ({stats['items_relevant']} about GTA VI), {stats['opportunities_new']} new topic(s), {stats['analyses']} analysed, {stats['notifications']} alert(s)")
    return {"id": scan_id, "status": status, **stats, "errors": errors}
