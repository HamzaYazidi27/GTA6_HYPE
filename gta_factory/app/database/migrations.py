"""Versioned schema migrations for the factory database.

The original tables (jobs, steps, stories, articles, assets) are created by
``db.SCHEMA`` with ``CREATE TABLE IF NOT EXISTS``. Every later change is a
numbered migration here, applied once, in order, and recorded in
``schema_migrations`` so an existing database is upgraded in place when the
new code starts (no data is lost, nothing is applied twice).

To change the schema: append a ``(name, sql)`` pair. Never edit or reorder a
migration that has shipped.
"""
from __future__ import annotations

import sqlite3

from ..core.utils import now_iso

MIGRATIONS: list[tuple[str, str]] = [
    (
        "0001_content_radar",
        """
        CREATE TABLE IF NOT EXISTS radar_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,                    -- stable id of a source from config.yaml; NULL for sources added in the dashboard
            name TEXT NOT NULL,
            kind TEXT NOT NULL,                 -- rss | reddit | youtube | news_search
            target TEXT NOT NULL,               -- feed URL, subreddit, YouTube channel id / @handle, or search query
            trust TEXT NOT NULL,                -- official | press | community | creator
            topic_only INTEGER NOT NULL DEFAULT 0,  -- 1: every entry is about GTA VI (r/GTA6, a GTA channel)
            options TEXT NOT NULL DEFAULT '{}', -- JSON (reddit listing, resolved YouTube channel id ...)
            enabled INTEGER NOT NULL DEFAULT 1,
            origin TEXT NOT NULL DEFAULT 'config',   -- config | dashboard
            created_at TEXT NOT NULL,
            etag TEXT,
            last_modified TEXT,
            last_checked_at TEXT,
            last_success_at TEXT,
            last_error TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT,
            items_total INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS radar_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            uid TEXT NOT NULL UNIQUE,           -- hash of source + entry id: the same entry is stored once
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            author TEXT,
            published_at TEXT,                  -- original publication date given by the source
            discovered_at TEXT NOT NULL,        -- when the radar first saw it
            updated_at TEXT,                    -- when the radar saw its content change
            content_hash TEXT NOT NULL,
            relevant INTEGER NOT NULL DEFAULT 0,
            hints TEXT NOT NULL DEFAULT '[]',   -- JSON: categories suggested by keywords
            trust TEXT,                         -- official | press | community | creator | web (per item)
            opportunity_id INTEGER,
            meta TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_radar_items_canon ON radar_items(canonical_url);
        CREATE INDEX IF NOT EXISTS idx_radar_items_opp ON radar_items(opportunity_id);
        CREATE INDEX IF NOT EXISTS idx_radar_items_disc ON radar_items(discovered_at);
        CREATE TABLE IF NOT EXISTS radar_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            title TEXT NOT NULL,
            fingerprint TEXT NOT NULL,          -- JSON list of title tokens (same-story matching)
            published_at TEXT,                  -- earliest original publication among its sources
            categories TEXT NOT NULL DEFAULT '[]',
            verification TEXT,                  -- official | credible | community | rumor
            score INTEGER,
            priority TEXT,                      -- high | medium | low
            format TEXT,                        -- short | long
            urgency TEXT,                       -- high | medium | low
            summary TEXT,
            triage TEXT,                        -- JSON: first-pass reading of the headline
            analysis TEXT,                      -- JSON: full analysis
            ideas TEXT,                         -- JSON: titles, hooks, scripts
            analysis_status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | skipped | failed
            analysis_attempts INTEGER NOT NULL DEFAULT 0,
            analysis_error TEXT,
            analyzed_at TEXT,
            needs_reanalysis INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new', -- new | to_review | approved | rejected | published
            status_changed_at TEXT,
            source_count INTEGER NOT NULL DEFAULT 1,
            job_id TEXT,
            notified_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_radar_opp_status ON radar_opportunities(status);
        CREATE INDEX IF NOT EXISTS idx_radar_opp_created ON radar_opportunities(created_at);
        CREATE INDEX IF NOT EXISTS idx_radar_opp_analysis ON radar_opportunities(analysis_status);
        CREATE TABLE IF NOT EXISTS radar_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            trigger TEXT NOT NULL,              -- schedule | manual | cli
            status TEXT NOT NULL,               -- running | success | partial | failed | interrupted
            owner TEXT,
            sources_checked INTEGER NOT NULL DEFAULT 0,
            sources_failed INTEGER NOT NULL DEFAULT 0,
            items_new INTEGER NOT NULL DEFAULT 0,
            items_updated INTEGER NOT NULL DEFAULT 0,
            opportunities_new INTEGER NOT NULL DEFAULT 0,
            analyses INTEGER NOT NULL DEFAULT 0,
            notifications INTEGER NOT NULL DEFAULT 0,
            errors TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_radar_scans_started ON radar_scans(started_at);
        CREATE TABLE IF NOT EXISTS radar_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            opportunity_id INTEGER,
            kind TEXT NOT NULL,                 -- official | new_mechanic | hidden_detail | high_opportunity | digest
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            dedupe_key TEXT NOT NULL UNIQUE,    -- one alert per opportunity: never twice
            read_at TEXT,
            delivered TEXT NOT NULL DEFAULT '{}'    -- JSON: {"telegram": "ok"|"error ...", "email": ...}
        );
        CREATE TABLE IF NOT EXISTS radar_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,                -- JSON
            updated_at TEXT NOT NULL
        );
        """,
    ),
]


def apply_migrations(con: sqlite3.Connection) -> list[str]:
    """Apply the migrations this database has not seen yet. Returns their names."""
    con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {r[0] for r in con.execute("SELECT name FROM schema_migrations")}
    applied = []
    for name, sql in MIGRATIONS:
        if name in done:
            continue
        con.executescript(sql)
        con.execute("INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)", (name, now_iso()))
        applied.append(name)
    return applied
