"""Persistence of the Content Radar (tables ``radar_*``, see ``app/database/migrations.py``)."""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any

from ...app.core.utils import now_iso
from ...app.database.db import Database, get_db

STATUSES = ("new", "to_review", "approved", "rejected", "published")
CATEGORIES = ("secrets", "map", "missions", "heists")
KINDS = ("rss", "reddit", "youtube", "news_search")
TRUSTS = ("official", "press", "community", "creator")
_JSON = {"options", "hints", "meta", "fingerprint", "categories", "triage", "analysis", "ideas", "errors", "delivered", "value"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for k in _JSON & set(out):
        if isinstance(out[k], str):
            try:
                out[k] = json.loads(out[k])
            except json.JSONDecodeError:
                pass
    return out


def _dump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


class RadarStore:
    def __init__(self, db: Database):
        self.db = db

    @classmethod
    def of(cls, settings: Any) -> "RadarStore":
        return cls(get_db(settings.db_path))

    # ------------------------------------------------------------------ state (settings, lock)
    def get_state(self, key: str, default: Any = None) -> Any:
        with self.db.connect() as con:
            r = con.execute("SELECT value FROM radar_state WHERE key=?", (key,)).fetchone()
        if r is None:
            return default
        try:
            return json.loads(r["value"])
        except json.JSONDecodeError:
            return default

    def set_state(self, key: str, value: Any) -> None:
        with self.db.connect() as con:
            con.execute("INSERT INTO radar_state (key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, _dump(value), now_iso()))

    @staticmethod
    def owner_id() -> str:
        return f"{socket.gethostname()}:{os.getpid()}"

    def acquire_lock(self, owner: str, lease_seconds: int = 1800) -> bool:
        """One scan at a time across the dashboard, the worker and cron. A lease that expired, or whose
        process on this computer is gone, is taken over."""
        now = utcnow()
        with self.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            r = con.execute("SELECT value FROM radar_state WHERE key='lock'").fetchone()
            cur = json.loads(r["value"]) if r else None
            if cur and cur.get("owner") and cur["owner"] != owner:
                expires = parse_iso(cur.get("expires_at"))
                alive = expires is not None and expires > now
                host, _, pid = str(cur["owner"]).rpartition(":")
                if alive and host == socket.gethostname() and pid.isdigit() and not _pid_alive(int(pid)):
                    alive = False  # the scanning process crashed on this computer
                if alive:
                    return False
            value = {"owner": owner, "since": iso(now), "expires_at": iso(now + timedelta(seconds=lease_seconds))}
            con.execute("INSERT INTO radar_state (key, value, updated_at) VALUES ('lock',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (_dump(value), iso(now)))
            return True

    def renew_lock(self, owner: str, lease_seconds: int = 1800) -> None:
        cur = self.get_state("lock") or {}
        if cur.get("owner") == owner:
            cur["expires_at"] = iso(utcnow() + timedelta(seconds=lease_seconds))
            self.set_state("lock", cur)

    def release_lock(self, owner: str) -> None:
        with self.db.connect() as con:
            r = con.execute("SELECT value FROM radar_state WHERE key='lock'").fetchone()
            if r and json.loads(r["value"]).get("owner") == owner:
                con.execute("DELETE FROM radar_state WHERE key='lock'")

    def lock_info(self) -> dict[str, Any] | None:
        cur = self.get_state("lock")
        if not cur:
            return None
        exp = parse_iso(cur.get("expires_at"))
        return cur if exp and exp > utcnow() else None

    # ------------------------------------------------------------------ sources
    def seed_sources(self, sources: list[dict[str, Any]]) -> int:
        """Insert the configured sources that are not in the database yet (matched by ``key``).
        Existing rows keep their enabled flag and health, so a source disabled in the dashboard stays off."""
        added = 0
        with self.db.connect() as con:
            for s in sources:
                key = str(s.get("key") or "").strip()
                if not key or con.execute("SELECT 1 FROM radar_sources WHERE key=?", (key,)).fetchone():
                    continue
                con.execute(
                    "INSERT INTO radar_sources (key, name, kind, target, trust, topic_only, options, enabled, origin, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (key, s["name"], s["kind"], s["target"], s.get("trust", "press"), int(bool(s.get("topic_only"))), _dump(s.get("options") or {}), int(s.get("enabled", True)), "config", now_iso()),
                )
                added += 1
        return added

    def add_source(self, *, name: str, kind: str, target: str, trust: str, topic_only: bool, options: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.db.connect() as con:
            dup = con.execute("SELECT id FROM radar_sources WHERE kind=? AND target=?", (kind, target)).fetchone()
            if dup:
                raise ValueError("This source is already monitored.")
            cur = con.execute(
                "INSERT INTO radar_sources (key, name, kind, target, trust, topic_only, options, enabled, origin, created_at) VALUES (NULL,?,?,?,?,?,?,1,'dashboard',?)",
                (name, kind, target, trust, int(topic_only), _dump(options or {}), now_iso()),
            )
            sid = int(cur.lastrowid or 0)
        return self.get_source(sid)  # type: ignore[return-value]

    def get_source(self, sid: int) -> dict[str, Any] | None:
        with self.db.connect() as con:
            return _row(con.execute("SELECT * FROM radar_sources WHERE id=?", (sid,)).fetchone())

    def list_sources(self) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM radar_sources ORDER BY CASE trust WHEN 'official' THEN 0 WHEN 'press' THEN 1 WHEN 'creator' THEN 2 ELSE 3 END, name").fetchall()
        return [_row(r) for r in rows]  # type: ignore[misc]

    def update_source(self, sid: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [_dump(v) if k in _JSON and not isinstance(v, str) else v for k, v in fields.items()]
        with self.db.connect() as con:
            con.execute(f"UPDATE radar_sources SET {cols} WHERE id=?", (*vals, sid))

    def delete_source(self, sid: int) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM radar_sources WHERE id=?", (sid,))

    def due_sources(self, now: datetime) -> list[dict[str, Any]]:
        return [s for s in self.list_sources() if s["enabled"] and (not s.get("next_attempt_at") or (parse_iso(s["next_attempt_at"]) or now) <= now)]

    # ------------------------------------------------------------------ items
    def get_item_by_uid(self, uid: str) -> dict[str, Any] | None:
        with self.db.connect() as con:
            return _row(con.execute("SELECT * FROM radar_items WHERE uid=?", (uid,)).fetchone())

    def item_with_url(self, canonical: str) -> dict[str, Any] | None:
        with self.db.connect() as con:
            return _row(con.execute("SELECT * FROM radar_items WHERE canonical_url=? ORDER BY id LIMIT 1", (canonical,)).fetchone())

    def insert_item(self, item: dict[str, Any]) -> int:
        with self.db.connect() as con:
            cur = con.execute(
                "INSERT INTO radar_items (source_id, uid, url, canonical_url, title, summary, author, published_at, discovered_at, content_hash, relevant, hints, trust, opportunity_id, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item["source_id"], item["uid"], item["url"], item["canonical_url"], item["title"], item.get("summary"), item.get("author"), item.get("published_at"), item.get("discovered_at") or now_iso(), item["content_hash"], int(item.get("relevant", 0)), _dump(item.get("hints") or []), item.get("trust"), item.get("opportunity_id"), _dump(item.get("meta") or {})),
            )
            return int(cur.lastrowid or 0)

    def update_item(self, iid: int, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [_dump(v) if k in _JSON and not isinstance(v, str) else v for k, v in fields.items()]
        with self.db.connect() as con:
            con.execute(f"UPDATE radar_items SET {cols} WHERE id=?", (*vals, iid))

    def items_of(self, opportunity_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT i.*, s.name AS source_name, s.kind AS source_kind FROM radar_items i JOIN radar_sources s ON s.id=i.source_id WHERE i.opportunity_id=? ORDER BY CASE i.trust WHEN 'official' THEN 0 WHEN 'press' THEN 1 WHEN 'creator' THEN 2 WHEN 'community' THEN 3 ELSE 4 END, COALESCE(i.published_at, i.discovered_at)",
                (opportunity_id,),
            ).fetchall()
        return [_row(r) for r in rows]  # type: ignore[misc]

    def count_items(self, *, relevant_only: bool = True) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM radar_items" + (" WHERE relevant=1" if relevant_only else "")).fetchone()[0])

    # ------------------------------------------------------------------ opportunities
    def create_opportunity(self, *, title: str, fingerprint: list[str], published_at: str | None, categories: list[str]) -> int:
        now = now_iso()
        with self.db.connect() as con:
            cur = con.execute(
                "INSERT INTO radar_opportunities (created_at, updated_at, title, fingerprint, published_at, categories, status, status_changed_at) VALUES (?,?,?,?,?,?, 'new', ?)",
                (now, now, title, _dump(fingerprint), published_at, _dump(categories), now),
            )
            return int(cur.lastrowid or 0)

    def update_opportunity(self, oid: int, **fields: Any) -> None:
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [_dump(v) if k in _JSON and not isinstance(v, str) else v for k, v in fields.items()]
        with self.db.connect() as con:
            con.execute(f"UPDATE radar_opportunities SET {cols} WHERE id=?", (*vals, oid))

    def get_opportunity(self, oid: int) -> dict[str, Any] | None:
        with self.db.connect() as con:
            return _row(con.execute("SELECT * FROM radar_opportunities WHERE id=?", (oid,)).fetchone())

    def recent_opportunities(self, since: datetime) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM radar_opportunities WHERE created_at>=? ORDER BY id", (iso(since),)).fetchall()
        return [_row(r) for r in rows]  # type: ignore[misc]

    def pending_triage(self, limit: int) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM radar_opportunities WHERE analysis_status='pending' AND triage IS NULL ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]  # type: ignore[misc]

    def analysis_queue(self, limit: int, max_attempts: int) -> list[dict[str, Any]]:
        """Triaged opportunities waiting for a full analysis (or re-analysis after a new source), best first."""
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT * FROM radar_opportunities WHERE ((analysis_status='pending' AND triage IS NOT NULL) OR (analysis_status='done' AND needs_reanalysis=1)) AND analysis_attempts<? ORDER BY needs_reanalysis DESC, id DESC",
                (max_attempts,),
            ).fetchall()
        out = [_row(r) for r in rows]
        out.sort(key=lambda o: (-int(o.get("needs_reanalysis") or 0), -int(((o.get("triage") or {}).get("interest") or 0))))  # type: ignore[union-attr]
        return out[:limit]  # type: ignore[return-value]

    def list_opportunities(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        priority: str | None = None,
        fmt: str | None = None,
        since: str | None = None,
        include_low: bool = False,
        query: str | None = None,
        limit: int = 60,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        where, args = [], []
        if status in STATUSES:
            where.append("status=?")
            args.append(status)
        if category in CATEGORIES:
            where.append("categories LIKE ?")
            args.append(f'%"{category}"%')
        if priority in ("high", "medium", "low"):
            where.append("priority=?")
            args.append(priority)
        if fmt in ("short", "long"):
            where.append("format=?")
            args.append(fmt)
        if since:
            where.append("COALESCE(published_at, created_at)>=?")
            args.append(since)
        if query:
            where.append("(title LIKE ? OR summary LIKE ?)")
            args += [f"%{query}%", f"%{query}%"]
        if not include_low and priority != "low" and status not in ("rejected",):
            where.append("analysis_status IN ('done','failed','pending')")
        sql_where = (" WHERE " + " AND ".join(where)) if where else ""
        with self.db.connect() as con:
            total = int(con.execute("SELECT COUNT(*) FROM radar_opportunities" + sql_where, args).fetchone()[0])
            rows = con.execute(
                "SELECT * FROM radar_opportunities" + sql_where + " ORDER BY CASE analysis_status WHEN 'done' THEN 0 WHEN 'pending' THEN 1 WHEN 'failed' THEN 2 ELSE 3 END, COALESCE(score, -1) DESC, created_at DESC LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        return [_row(r) for r in rows], total  # type: ignore[return-value]

    def set_status(self, oid: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status '{status}'")
        self.update_opportunity(oid, status=status, status_changed_at=now_iso())

    def counts(self) -> dict[str, Any]:
        with self.db.connect() as con:
            total = int(con.execute("SELECT COUNT(*) FROM radar_opportunities").fetchone()[0])
            by_status = {r[0]: r[1] for r in con.execute("SELECT status, COUNT(*) FROM radar_opportunities WHERE analysis_status IN ('done','pending','failed') GROUP BY status")}
            high = int(con.execute("SELECT COUNT(*) FROM radar_opportunities WHERE priority='high' AND status IN ('new','to_review')").fetchone()[0])
            pending = int(con.execute("SELECT COUNT(*) FROM radar_opportunities WHERE analysis_status='pending'").fetchone()[0])
            low = int(con.execute("SELECT COUNT(*) FROM radar_opportunities WHERE analysis_status='skipped'").fetchone()[0])
        return {"discoveries": total, "new": by_status.get("new", 0), "to_review": by_status.get("to_review", 0), "approved": by_status.get("approved", 0), "high_priority": high, "pending_analysis": pending, "low_value": low}

    # ------------------------------------------------------------------ scans
    def start_scan(self, trigger: str, owner: str) -> int:
        with self.db.connect() as con:
            # a scan left "running" by a process that died: close it so the history stays truthful
            con.execute("UPDATE radar_scans SET status='interrupted', finished_at=? WHERE status='running'", (now_iso(),))
            cur = con.execute("INSERT INTO radar_scans (started_at, trigger, status, owner) VALUES (?,?, 'running', ?)", (now_iso(), trigger, owner))
            return int(cur.lastrowid or 0)

    def finish_scan(self, sid: int, **fields: Any) -> None:
        fields.setdefault("finished_at", now_iso())
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [_dump(v) if k in _JSON and not isinstance(v, str) else v for k, v in fields.items()]
        with self.db.connect() as con:
            con.execute(f"UPDATE radar_scans SET {cols} WHERE id=?", (*vals, sid))

    def list_scans(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM radar_scans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]  # type: ignore[misc]

    def last_scan(self, *, statuses: tuple[str, ...] | None = None) -> dict[str, Any] | None:
        q = "SELECT * FROM radar_scans WHERE status!='running'"
        args: list[Any] = []
        if statuses:
            q += " AND status IN (" + ",".join("?" * len(statuses)) + ")"
            args += list(statuses)
        with self.db.connect() as con:
            return _row(con.execute(q + " ORDER BY id DESC LIMIT 1", args).fetchone())

    def running_scan(self) -> dict[str, Any] | None:
        with self.db.connect() as con:
            return _row(con.execute("SELECT * FROM radar_scans WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone())

    # ------------------------------------------------------------------ notifications
    def add_notification(self, *, opportunity_id: int | None, kind: str, title: str, body: str, dedupe_key: str) -> int | None:
        """Insert unless an alert with the same key exists. Returns the new id, or None for a duplicate."""
        with self.db.connect() as con:
            if con.execute("SELECT 1 FROM radar_notifications WHERE dedupe_key=?", (dedupe_key,)).fetchone():
                return None
            cur = con.execute("INSERT INTO radar_notifications (created_at, opportunity_id, kind, title, body, dedupe_key) VALUES (?,?,?,?,?,?)", (now_iso(), opportunity_id, kind, title, body, dedupe_key))
            return int(cur.lastrowid or 0)

    def set_delivered(self, nid: int, delivered: dict[str, str]) -> None:
        with self.db.connect() as con:
            con.execute("UPDATE radar_notifications SET delivered=? WHERE id=?", (_dump(delivered), nid))

    def notifications_since(self, since: datetime) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM radar_notifications WHERE created_at>=?", (iso(since),)).fetchone()[0])

    def list_notifications(self, *, unread_only: bool = False, limit: int = 30) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM radar_notifications" + (" WHERE read_at IS NULL" if unread_only else "") + " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]  # type: ignore[misc]

    def unread_count(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM radar_notifications WHERE read_at IS NULL").fetchone()[0])

    def mark_read(self, ids: list[int] | None = None) -> None:
        with self.db.connect() as con:
            if ids is None:
                con.execute("UPDATE radar_notifications SET read_at=? WHERE read_at IS NULL", (now_iso(),))
            else:
                con.executemany("UPDATE radar_notifications SET read_at=? WHERE id=? AND read_at IS NULL", [(now_iso(), i) for i in ids])


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
