"""SQLite publishing / rights / analytics database."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .common import DIRS

DB_PATH = DIRS["database"] / "engine.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    url TEXT,
    local_path TEXT,
    title TEXT,
    streamer TEXT,
    profile TEXT DEFAULT 'gta6',            -- content profile used to process it
    game TEXT,
    duration REAL,
    rights_status TEXT DEFAULT 'unknown',   -- unknown | owned | permission_granted | denied
    commercial_use INTEGER DEFAULT 0,       -- 1 = explicit commercial-use permission
    authorized_on TEXT,
    authorization_ref TEXT,                 -- where the permission lives (email, contract, DM link)
    restrictions TEXT,
    layout_json TEXT,                       -- facecam detection result
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT REFERENCES sources(id),
    start REAL, end REAL, peak REAL,
    signal_score REAL,
    virality REAL,
    category TEXT,
    summary TEXT,
    why TEXT,
    hook TEXT,
    status TEXT DEFAULT 'candidate',        -- candidate | rejected | selected | rendered
    reject_reason TEXT,
    review_json TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    source_id TEXT REFERENCES sources(id),
    candidate_id INTEGER REFERENCES candidates(id),
    path TEXT,
    sha256 TEXT,
    duration REAL,
    layout TEXT,
    category TEXT,
    virality REAL,
    source_start REAL, source_end REAL,
    metadata_json TEXT,
    commentary_json TEXT,
    narrated INTEGER DEFAULT 0,
    preview INTEGER DEFAULT 0,              -- forced preview render: never scheduled
    review_status TEXT DEFAULT 'pending',   -- human approval: pending | approved | rejected
    updated_at TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id TEXT REFERENCES clips(id),
    platform TEXT,                          -- youtube | instagram
    status TEXT,                            -- scheduled | uploading | published | failed | blocked | test
    scheduled_for TEXT,                     -- UTC ISO
    uploaded_at TEXT,
    published_at TEXT,
    remote_id TEXT,
    url TEXT,
    attempts INTEGER DEFAULT 0,
    last_error TEXT,
    UNIQUE(clip_id, platform)
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id INTEGER REFERENCES publications(id),
    fetched_at TEXT,
    views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER,
    avg_view_duration REAL, avg_view_pct REAL,
    raw_json TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,                              -- process | rerender | render_candidate | analytics | publish
    label TEXT,
    args_json TEXT,
    status TEXT DEFAULT 'queued',           -- queued | running | paused | done | failed | cancelled | interrupted
    progress REAL DEFAULT 0,
    stage TEXT,
    pid INTEGER,
    log_path TEXT,
    source_id TEXT,
    result_json TEXT,
    created_at TEXT, started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS learned_weights (
    key TEXT PRIMARY KEY,
    value REAL,
    samples INTEGER,
    updated_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


MIGRATIONS = ["ALTER TABLE clips ADD COLUMN preview INTEGER DEFAULT 0",
              "ALTER TABLE clips ADD COLUMN review_status TEXT DEFAULT 'pending'",  # pending|approved|rejected
              "ALTER TABLE clips ADD COLUMN updated_at TEXT",
              "ALTER TABLE sources ADD COLUMN profile TEXT DEFAULT 'gta6'"]


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        for m in MIGRATIONS:  # idempotent column additions for older databases
            try:
                con.execute(m)
            except sqlite3.OperationalError:
                pass


def rows(sql: str, args=()) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def one(sql: str, args=()) -> dict | None:
    r = rows(sql, args)
    return r[0] if r else None


def execute(sql: str, args=()) -> int:
    with connect() as con:
        cur = con.execute(sql, args)
        return cur.lastrowid


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False)


init()
