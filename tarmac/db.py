"""SQLite storage — two deliberately separate layers (SPEC §5).

Derived layer (`sessions`, `transitions`, `target_status`): a disposable mirror
of the --json; can be rebuilt at any time. Intent layer (`session_meta`,
`session_checklist`, `terminal_handles`): mine, preserved, survives sessions
disappearing from the JSON.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .config import tarmac_home

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  target_id     TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  short_id      TEXT,
  uuid          TEXT,
  name          TEXT,
  kind          TEXT,
  state         TEXT,
  status        TEXT,
  waiting_for   TEXT,
  cwd           TEXT,
  pid           INTEGER,
  started_at    INTEGER,
  first_seen_at INTEGER,
  last_seen_at  INTEGER,
  gone          INTEGER DEFAULT 0,
  class         TEXT DEFAULT 'owned',
  eff_state     TEXT,
  raw_json      TEXT,
  PRIMARY KEY (target_id, session_id)
);

CREATE TABLE IF NOT EXISTS session_meta (
  target_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  alias           TEXT,
  next_step       TEXT,
  next_step_origin TEXT,
  due_at          INTEGER,
  due_label       TEXT,
  hide_until_due  INTEGER DEFAULT 0,
  tags            TEXT,
  pinned          INTEGER DEFAULT 0,
  notes           TEXT,
  resolved_at     INTEGER,
  updated_at      INTEGER,
  PRIMARY KEY (target_id, session_id)
);

CREATE TABLE IF NOT EXISTS session_checklist (
  id         INTEGER PRIMARY KEY,
  target_id  TEXT NOT NULL,
  session_id TEXT NOT NULL,
  position   INTEGER NOT NULL,
  text       TEXT NOT NULL,
  done       INTEGER DEFAULT 0,
  created_at INTEGER,
  done_at    INTEGER
);

CREATE TABLE IF NOT EXISTS transitions (
  id INTEGER PRIMARY KEY,
  target_id TEXT, session_id TEXT,
  from_state TEXT, to_state TEXT, at INTEGER,
  -- 1 = the transition happened while the target was unobservable (VPN down);
  -- `at` is then the start of the blind window, so waits render as ">=".
  uncertain INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS terminal_handles (
  target_id  TEXT NOT NULL,
  session_id TEXT NOT NULL,
  handle     TEXT,
  opened_at  INTEGER,
  PRIMARY KEY (target_id, session_id)
);

CREATE TABLE IF NOT EXISTS target_status (
  target_id     TEXT PRIMARY KEY,
  last_ok_at    INTEGER,
  last_error    TEXT,
  error_kind    TEXT,          -- 'offline' | 'error' | NULL
  fail_count    INTEGER DEFAULT 0,
  next_retry_at INTEGER,
  claude_version TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  text        TEXT NOT NULL,
  target_id   TEXT,           -- resolved on first open
  cwd         TEXT,           -- resolved folder to start Claude Code in
  due_at      INTEGER,
  due_label   TEXT,
  created_at  INTEGER,
  opened_at   INTEGER,
  resolved_at INTEGER
);

CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE INDEX IF NOT EXISTS idx_transitions_session
  ON transitions (target_id, session_id, at);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def db_path() -> Path:
    return tarmac_home() / "tarmac.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def kv_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, target_id: str, session_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM session_meta WHERE target_id = ? AND session_id = ?",
        (target_id, session_id),
    ).fetchone()


def upsert_meta(conn: sqlite3.Connection, target_id: str, session_id: str, **fields) -> None:
    existing = get_meta(conn, target_id, session_id)
    fields["updated_at"] = now_ms()
    if existing is None:
        cols = ["target_id", "session_id", *fields.keys()]
        conn.execute(
            f"INSERT INTO session_meta ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            (target_id, session_id, *fields.values()),
        )
    else:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE session_meta SET {sets} WHERE target_id = ? AND session_id = ?",
            (*fields.values(), target_id, session_id),
        )


def blocked_since(conn: sqlite3.Connection, target_id: str, session_id: str) -> tuple[int, bool] | None:
    """(at, uncertain) of the last still-standing `* -> blocked` transition (SPEC §5.3)."""
    row = conn.execute(
        "SELECT to_state, at, uncertain FROM transitions "
        "WHERE target_id = ? AND session_id = ? ORDER BY at DESC, id DESC LIMIT 1",
        (target_id, session_id),
    ).fetchone()
    if row and row["to_state"] == "blocked":
        return row["at"], bool(row["uncertain"])
    return None


def wasted_by_day(conn: sqlite3.Connection, days: int = 14) -> list[tuple[str, int]]:
    """Aggregate seconds spent blocked per local day, owned sessions only (SPEC §5.3)."""
    since = now_ms() - days * 86_400_000
    rows = conn.execute(
        "SELECT t.target_id, t.session_id, t.from_state, t.to_state, t.at "
        "FROM transitions t JOIN sessions s "
        "  ON s.target_id = t.target_id AND s.session_id = t.session_id "
        "WHERE s.class = 'owned' AND t.at >= ? "
        "ORDER BY t.target_id, t.session_id, t.at, t.id",
        (since,),
    ).fetchall()
    intervals: list[tuple[int, int]] = []
    open_at: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (r["target_id"], r["session_id"])
        if r["to_state"] == "blocked":
            open_at.setdefault(key, r["at"])
        elif key in open_at:
            intervals.append((open_at.pop(key), r["at"]))
    now = now_ms()
    intervals.extend((start, now) for start in open_at.values())

    per_day: dict[str, int] = {}
    for start, end in intervals:
        # split the interval across local-midnight boundaries
        cur = start
        while cur < end:
            day = time.strftime("%Y-%m-%d", time.localtime(cur / 1000))
            next_midnight = int(
                (time.mktime(time.strptime(day, "%Y-%m-%d")) + 86400) * 1000
            )
            chunk_end = min(end, next_midnight)
            per_day[day] = per_day.get(day, 0) + (chunk_end - cur) // 1000
            cur = chunk_end
    return sorted(per_day.items())


def prune_service_sessions(conn: sqlite3.Connection, retention_h: int = 24) -> int:
    """Drop service sessions that finished (or vanished) > retention_h ago (SPEC §3.3)."""
    cutoff = now_ms() - retention_h * 3_600_000
    rows = conn.execute(
        "SELECT target_id, session_id FROM sessions "
        "WHERE class = 'service' AND last_seen_at < ? "
        "AND (gone = 1 OR eff_state IN ('done', 'failed', 'stopped'))",
        (cutoff,),
    ).fetchall()
    for r in rows:
        conn.execute(
            "DELETE FROM transitions WHERE target_id = ? AND session_id = ?",
            (r["target_id"], r["session_id"]),
        )
        conn.execute(
            "DELETE FROM sessions WHERE target_id = ? AND session_id = ?",
            (r["target_id"], r["session_id"]),
        )
    return len(rows)
