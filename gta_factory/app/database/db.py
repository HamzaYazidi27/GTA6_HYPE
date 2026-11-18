"""SQLite persistence: jobs, pipeline steps, the story memory and the asset ledger.

The database is the single source of truth for job state (the dashboard and the
CLI both read it) and for *story memory*: every produced story stores its
claims so that a later article about the same events can be recognised.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..core.utils import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,              -- queued | running | completed | failed | stopped | needs_review
    format TEXT NOT NULL,
    language TEXT NOT NULL,
    title TEXT,
    urls TEXT NOT NULL,                -- JSON list
    options TEXT NOT NULL,             -- JSON dict
    current_step TEXT,
    progress REAL DEFAULT 0,
    error TEXT,
    output_dir TEXT,
    summary TEXT                       -- JSON dict (duration, novelty score, qc verdict ...)
);
CREATE TABLE IF NOT EXISTS steps (
    job_id TEXT NOT NULL,
    name TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL,              -- pending | running | done | failed | skipped
    started_at TEXT,
    finished_at TEXT,
    seconds REAL,
    detail TEXT,                       -- JSON dict (step summary)
    error TEXT,
    PRIMARY KEY (job_id, name)
);
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    headline TEXT NOT NULL,
    angle TEXT,
    format TEXT,
    fingerprint TEXT NOT NULL,         -- JSON: tokens, entities, dates
    claims TEXT NOT NULL,              -- JSON list of normalised claim strings
    source_urls TEXT NOT NULL,         -- JSON list
    published INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS articles (
    url TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    job_id TEXT,
    publisher TEXT,
    title TEXT,
    published_at TEXT,
    content_hash TEXT
);
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    job_id TEXT,
    kind TEXT,
    file_path TEXT,
    origin_url TEXT,
    license TEXT,
    commercial_use INTEGER,
    attribution_required INTEGER,
    review_status TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_steps_job ON steps(job_id, position);
CREATE INDEX IF NOT EXISTS idx_stories_job ON stories(job_id);
"""

_J_FIELDS = {"urls", "options", "summary", "detail", "fingerprint", "claims", "source_urls", "meta"}


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for k in list(out):
        if k in _J_FIELDS and isinstance(out[k], str):
            try:
                out[k] = json.loads(out[k])
            except json.JSONDecodeError:
                pass
    return out


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            con = sqlite3.connect(self.path, timeout=30)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            try:
                yield con
                con.commit()
            finally:
                con.close()

    # ------------------------------------------------------------------ jobs
    def create_job(self, job_id: str, *, urls: list[str], fmt: str, language: str, options: dict[str, Any], steps: list[str], output_dir: str) -> None:
        now = now_iso()
        with self.connect() as con:
            con.execute(
                "INSERT INTO jobs (id, created_at, updated_at, status, format, language, urls, options, output_dir, summary) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_id, now, now, "queued", fmt, language, json.dumps(urls), json.dumps(options), output_dir, "{}"),
            )
            con.executemany(
                "INSERT INTO steps (job_id, name, position, status) VALUES (?,?,?,?)",
                [(job_id, name, i, "pending") for i, name in enumerate(steps)],
            )

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [json.dumps(v) if k in _J_FIELDS and not isinstance(v, str) else v for k, v in fields.items()]
        with self.connect() as con:
            con.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*vals, job_id))

    def merge_summary(self, job_id: str, **items: Any) -> None:
        job = self.get_job(job_id)
        if job is None:
            return
        summary = job.get("summary") or {}
        summary.update(items)
        self.update_job(job_id, summary=summary)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            return _row(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]  # type: ignore[misc]

    def job_counts(self) -> dict[str, int]:
        with self.connect() as con:
            rows = con.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        counts = {r["status"]: r["n"] for r in rows}
        counts["total"] = sum(counts.values())
        return counts

    def delete_job(self, job_id: str) -> None:
        with self.connect() as con:
            for table in ("steps", "stories", "assets"):
                con.execute(f"DELETE FROM {table} WHERE job_id=?", (job_id,))
            con.execute("UPDATE articles SET job_id=NULL WHERE job_id=?", (job_id,))
            con.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    # ----------------------------------------------------------------- steps
    def get_steps(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM steps WHERE job_id=? ORDER BY position", (job_id,)).fetchall()
        return [_row(r) for r in rows]  # type: ignore[misc]

    def set_step(self, job_id: str, name: str, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [json.dumps(v) if k in _J_FIELDS and not isinstance(v, str) else v for k, v in fields.items()]
        with self.connect() as con:
            con.execute(f"UPDATE steps SET {cols} WHERE job_id=? AND name=?", (*vals, job_id, name))

    def reset_steps_from(self, job_id: str, name: str) -> list[str]:
        """Mark ``name`` and every later step pending again. Returns the reset step names."""
        steps = self.get_steps(job_id)
        pos = next((s["position"] for s in steps if s["name"] == name), None)
        if pos is None:
            raise KeyError(f"unknown step '{name}'")
        with self.connect() as con:
            con.execute(
                "UPDATE steps SET status='pending', started_at=NULL, finished_at=NULL, seconds=NULL, error=NULL, detail=NULL WHERE job_id=? AND position>=?",
                (job_id, pos),
            )
        return [s["name"] for s in steps if s["position"] >= pos]

    # --------------------------------------------------------------- stories
    def add_story(self, *, job_id: str, headline: str, angle: str, fmt: str, fingerprint: dict[str, Any], claims: list[str], source_urls: list[str]) -> int:
        with self.connect() as con:
            con.execute("DELETE FROM stories WHERE job_id=?", (job_id,))
            cur = con.execute(
                "INSERT INTO stories (job_id, created_at, headline, angle, format, fingerprint, claims, source_urls) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, now_iso(), headline, angle, fmt, json.dumps(fingerprint), json.dumps(claims), json.dumps(source_urls)),
            )
            return int(cur.lastrowid or 0)

    def list_stories(self, exclude_job: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM stories ORDER BY created_at DESC").fetchall()
        out = [_row(r) for r in rows]
        return [s for s in out if s and s["job_id"] != exclude_job]

    def mark_story_published(self, job_id: str, video_id: str) -> None:
        with self.connect() as con:
            con.execute("UPDATE stories SET published=1 WHERE job_id=?", (job_id,))

    # -------------------------------------------------------------- articles
    def remember_article(self, *, url: str, job_id: str, publisher: str, title: str, published_at: str | None, content_hash: str) -> dict[str, Any] | None:
        """Record an article. Returns the earlier record when the URL was already processed by another job."""
        with self.connect() as con:
            prev = _row(con.execute("SELECT * FROM articles WHERE url=?", (url,)).fetchone())
            if prev is None:
                con.execute(
                    "INSERT INTO articles (url, first_seen_at, job_id, publisher, title, published_at, content_hash) VALUES (?,?,?,?,?,?,?)",
                    (url, now_iso(), job_id, publisher, title, published_at, content_hash),
                )
                return None
        return prev if prev.get("job_id") not in (None, job_id) else None

    # ---------------------------------------------------------------- assets
    def upsert_asset(self, job_id: str, asset: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO assets (asset_id, job_id, kind, file_path, origin_url, license, commercial_use, attribution_required, review_status, meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    asset["asset_id"],
                    job_id,
                    asset.get("kind"),
                    asset.get("file_path"),
                    asset.get("origin_url"),
                    asset.get("license"),
                    int(bool(asset.get("commercial_use"))),
                    int(bool(asset.get("attribution_required"))),
                    asset.get("review_status"),
                    json.dumps({k: v for k, v in asset.items() if k not in {"asset_id", "kind", "file_path", "origin_url", "license", "commercial_use", "attribution_required", "review_status"}}),
                ),
            )


_DB_CACHE: dict[str, Database] = {}


def get_db(path: Path) -> Database:
    key = str(Path(path).resolve())
    if key not in _DB_CACHE:
        _DB_CACHE[key] = Database(Path(path))
    return _DB_CACHE[key]
