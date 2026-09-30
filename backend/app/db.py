"""SQLite storage. Plain sqlite3 so FTS5 is easy; one connection per thread."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS streamers (
    id INTEGER PRIMARY KEY,
    twitch_id TEXT UNIQUE NOT NULL,
    login TEXT NOT NULL,
    display_name TEXT NOT NULL,
    profile_image_url TEXT,
    synced_until TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,               -- Twitch clip slug
    streamer_id INTEGER NOT NULL REFERENCES streamers(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    creator_name TEXT,
    game_id TEXT,
    game_name TEXT,
    view_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    duration REAL DEFAULT 0,
    thumbnail_url TEXT,
    vod_id TEXT,
    vod_offset INTEGER,
    language TEXT,

    status TEXT NOT NULL DEFAULT 'new', -- new|queued|downloading|transcribing|analyzing|done|error
    error TEXT,
    file_path TEXT,
    starred INTEGER NOT NULL DEFAULT 0,

    category TEXT,
    summary TEXT,
    tags TEXT,                          -- JSON list
    mood TEXT,
    energy INTEGER,
    confidence REAL,
    needs_review INTEGER NOT NULL DEFAULT 0,
    corrected INTEGER NOT NULL DEFAULT 0,
    analysis TEXT,                      -- JSON ClipAnalysis
    verification TEXT,                  -- JSON corroboration result
    transcript TEXT,                    -- JSON list of segments
    transcript_text TEXT,
    frames TEXT,                        -- JSON list of {path,t}
    provider TEXT,
    analyzed_at REAL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS clips_streamer ON clips(streamer_id, created_at);
CREATE INDEX IF NOT EXISTS clips_status ON clips(status);

CREATE VIRTUAL TABLE IF NOT EXISTS clips_fts USING fts5(
    clip_id UNINDEXED, title, summary, tags, transcript, game,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS qa (
    id INTEGER PRIMARY KEY,
    clip_id TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                 -- user|verify|crosscheck
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    supported INTEGER,                  -- for verify: 1 yes / 0 no / NULL unsure
    provider TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                 -- sync|analyze|verify
    clip_id TEXT,
    streamer_id INTEGER,
    params TEXT,
    status TEXT NOT NULL DEFAULT 'queued', -- queued|running|done|error|cancelled
    progress REAL NOT NULL DEFAULT 0,
    message TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, kind);

CREATE TABLE IF NOT EXISTS resolve_queue (
    id INTEGER PRIMARY KEY,
    clip_id TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    append_to_timeline INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    sent_at REAL
);
"""

JSON_COLUMNS = ("tags", "analysis", "verification", "transcript", "frames", "params")

_local = threading.local()
_init_lock = threading.Lock()
_initialized: set[str] = set()


def db_path() -> Path:
    return config.DATA_DIR / "clips.db"


def connect() -> sqlite3.Connection:
    path = str(db_path())
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    with _init_lock:
        if path not in _initialized:
            conn.executescript(SCHEMA)
            _initialized.add(path)
    _local.conn = conn
    _local.path = path
    return conn


def reset_for_tests() -> None:
    _initialized.clear()
    _local.__dict__.clear()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for col in JSON_COLUMNS:
        if col in d and isinstance(d[col], str):
            try:
                d[col] = json.loads(d[col])
            except json.JSONDecodeError:
                pass
    d.pop("embedding", None)
    return d


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    return [row_to_dict(r) for r in connect().execute(sql, tuple(params)).fetchall()]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    return row_to_dict(connect().execute(sql, tuple(params)).fetchone())


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    return connect().execute(sql, tuple(params))


def update(table: str, key: str, key_value: Any, **fields: Any) -> None:
    if not fields:
        return
    cols, values = [], []
    for k, v in fields.items():
        if k in JSON_COLUMNS and v is not None and not isinstance(v, (str, bytes)):
            v = json.dumps(v)
        cols.append(f"{k} = ?")
        values.append(v)
    values.append(key_value)
    execute(f"UPDATE {table} SET {', '.join(cols)} WHERE {key} = ?", values)


def now() -> float:
    return time.time()


def reindex_clip(clip_id: str) -> None:
    """Refresh the keyword index for one clip."""
    c = query_one("SELECT * FROM clips WHERE id = ?", [clip_id])
    if not c:
        return
    tags = c.get("tags") or []
    conn = connect()
    conn.execute("DELETE FROM clips_fts WHERE clip_id = ?", [clip_id])
    conn.execute(
        "INSERT INTO clips_fts (clip_id, title, summary, tags, transcript, game) VALUES (?,?,?,?,?,?)",
        [
            clip_id,
            c["title"] or "",
            " ".join(filter(None, [c.get("summary"), c.get("category"), c.get("mood")])),
            " ".join(tags) if isinstance(tags, list) else str(tags),
            c.get("transcript_text") or "",
            c.get("game_name") or "",
        ],
    )
