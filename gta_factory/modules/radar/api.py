"""What the dashboard and the CLI show and do with the radar (JSON-ready dictionaries)."""
from __future__ import annotations

from typing import Any

from ...app.core.errors import FactoryError
from .analyze import CATEGORY_LABEL
from .engine import config, schedule
from .notify import channels_ready
from .store import CATEGORIES, STATUSES, RadarStore


def _int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _card(o: dict[str, Any], lead: dict[str, Any] | None) -> dict[str, Any]:
    a = o.get("analysis") or {}
    return {
        "id": o["id"],
        "title": o["title"],
        "categories": o.get("categories") or [],
        "discovered_at": o["created_at"],
        "published_at": o.get("published_at"),
        "source": {"name": ((lead or {}).get("meta") or {}).get("publisher") or (lead or {}).get("source_name"), "url": (lead or {}).get("url"), "trust": (lead or {}).get("trust")} if lead else None,
        "source_count": o.get("source_count") or 1,
        "summary": o.get("summary") or "",
        "verification": o.get("verification"),
        "score": o.get("score"),
        "priority": o.get("priority"),
        "format": o.get("format"),
        "urgency": o.get("urgency"),
        "status": o["status"],
        "analysis_status": o["analysis_status"],
        "analysis_error": o.get("analysis_error"),
        "title_idea": ((o.get("ideas") or {}).get("titles") or [None])[0],
        "angle": a.get("unique_angle"),
        "job_id": o.get("job_id"),
    }


def overview(settings: Any) -> dict[str, Any]:
    from .worker import schedule_status

    store = RadarStore.of(settings)
    sources = store.list_sources()
    return {
        "counts": store.counts(),
        "sources": {"total": len(sources), "enabled": sum(1 for s in sources if s["enabled"]), "failing": sum(1 for s in sources if s["enabled"] and s.get("consecutive_failures"))},
        "schedule": schedule(settings, store),
        "system_schedule": schedule_status(settings),
        "unread": store.unread_count(),
        "channels": channels_ready(),
        "categories": CATEGORY_LABEL,
        "statuses": list(STATUSES),
    }


def list_payload(settings: Any, q: dict[str, str]) -> dict[str, Any]:
    store = RadarStore.of(settings)
    rows, total = store.list_opportunities(
        status=q.get("status") or None,
        category=q.get("category") or None,
        priority=q.get("priority") or None,
        fmt=q.get("format") or None,
        since=q.get("since") or None,
        include_low=q.get("include_low") in ("1", "true"),
        query=(q.get("q") or "").strip()[:100] or None,
        limit=max(1, min(200, _int(q.get("limit"), 60))),
        offset=max(0, _int(q.get("offset"), 0)),
    )
    cards = []
    for o in rows:
        items = store.items_of(int(o["id"]))
        cards.append(_card(o, items[0] if items else None))
    return {"items": cards, "total": total}


def opportunity_payload(settings: Any, oid: int) -> dict[str, Any]:
    store = RadarStore.of(settings)
    o = store.get_opportunity(oid)
    if o is None:
        raise FactoryError(f"Unknown discovery #{oid}.")
    items = store.items_of(oid)
    return {
        **_card(o, items[0] if items else None),
        "analysis": {k: v for k, v in (o.get("analysis") or {}).items()},
        "ideas": o.get("ideas") or {},
        "triage": o.get("triage") or {},
        "analyzed_at": o.get("analyzed_at"),
        "status_changed_at": o.get("status_changed_at"),
        "sources": [
            {
                "name": (i.get("meta") or {}).get("publisher") or i.get("source_name"),
                "kind": i.get("source_kind"),
                "trust": i.get("trust"),
                "url": i["url"],
                "title": i["title"],
                "published_at": i.get("published_at"),
                "discovered_at": i.get("discovered_at"),
                "updated_at": i.get("updated_at"),
            }
            for i in items
        ],
    }


def set_status(settings: Any, oid: int, status: str) -> dict[str, Any]:
    store = RadarStore.of(settings)
    if status not in STATUSES:
        raise FactoryError(f"Unknown status '{status}'.", hint=", ".join(STATUSES))
    if store.get_opportunity(oid) is None:
        raise FactoryError(f"Unknown discovery #{oid}.")
    store.set_status(oid, status)
    return opportunity_payload(settings, oid)


def reanalyze(settings: Any, oid: int) -> None:
    store = RadarStore.of(settings)
    o = store.get_opportunity(oid)
    if o is None:
        raise FactoryError(f"Unknown discovery #{oid}.")
    if o["analysis_status"] == "done":
        store.update_opportunity(oid, needs_reanalysis=1, analysis_attempts=0)
    else:  # failed or skipped: back to the queue, past the first pass
        triage = o.get("triage") or {"interest": 10, "keep": True, "categories": o.get("categories") or [], "reason": "asked by the operator"}
        store.update_opportunity(oid, analysis_status="pending", triage={**triage, "interest": max(int(triage.get("interest") or 0), 6), "keep": True}, analysis_attempts=0, analysis_error=None)


def start_video(settings: Any, oid: int, fmt: str | None = None) -> str:
    """Hand an approved idea to the video factory: a news job from its official / press articles."""
    from ...app.core.pipeline import create_job

    store = RadarStore.of(settings)
    o = store.get_opportunity(oid)
    if o is None:
        raise FactoryError(f"Unknown discovery #{oid}.")
    urls = [i["url"] for i in store.items_of(oid) if i.get("trust") in ("official", "press") and i.get("source_kind") not in ("youtube", "reddit")][:3]
    if not urls:
        raise FactoryError(
            "This discovery has no official or press article the factory can read.",
            hint="Community finds and videos are not articles: use the idea with « Comparaison ou top liste », or wait until an outlet covers it.",
        )
    fmt = fmt or ("short" if o.get("format") == "short" else "long")
    if fmt not in (settings.get("formats") or {}):
        raise FactoryError(f"Unknown format '{fmt}'.")
    job_id = create_job(settings, urls, fmt=fmt, options={})
    store.set_status(oid, "approved")
    store.update_opportunity(oid, job_id=job_id)
    return job_id


def update_settings(settings: Any, body: dict[str, Any]) -> dict[str, Any]:
    store = RadarStore.of(settings)
    cur = store.get_state("settings", {}) or {}
    if "interval_hours" in body:
        try:
            hours = float(body["interval_hours"])
        except (TypeError, ValueError) as exc:
            raise FactoryError("The interval must be a number of hours.") from exc
        if not 0.25 <= hours <= 48:
            raise FactoryError("The interval must be between 0.25 and 48 hours.")
        cur["interval_hours"] = hours
    if "enabled" in body:
        cur["enabled"] = bool(body["enabled"])
    store.set_state("settings", cur)
    return {"interval_hours": config(settings, store)["interval_hours"], "enabled": config(settings, store)["enabled"]}


def category_ok(c: str) -> bool:
    return c in CATEGORIES
