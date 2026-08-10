"""Standalone tasks — intent without a session (yet).

"no benji-dp, preciso adicionar uma forma de dividir os rampids em ssps"
lands in AGENDADO; opening it starts Claude Code in the right folder. The
folder is inferred from the task text against the history of cwds where
Claude Code actually runs (the sessions mirror — no filesystem scanning);
when inference fails the UI asks, offering the most-used folders.
"""

from __future__ import annotations

import os.path
import re
import sqlite3
from dataclasses import dataclass

from . import db as dbm


@dataclass
class Candidate:
    target_id: str
    cwd: str
    count: int  # how many sessions ever ran there


def add_task(
    conn: sqlite3.Connection,
    text: str,
    due_at: int | None = None,
    due_label: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO tasks (text, due_at, due_label, created_at) VALUES (?, ?, ?, ?)",
        (text.strip(), due_at, due_label, dbm.now_ms()),
    )
    conn.commit()
    return cur.lastrowid


def resolve_task(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute("UPDATE tasks SET resolved_at = ? WHERE id = ?",
                 (dbm.now_ms(), task_id))
    conn.commit()


def set_task_folder(conn: sqlite3.Connection, task_id: int, target_id: str, cwd: str) -> None:
    conn.execute("UPDATE tasks SET target_id = ?, cwd = ? WHERE id = ?",
                 (target_id, cwd, task_id))
    conn.commit()


def mark_opened(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute("UPDATE tasks SET opened_at = ? WHERE id = ?",
                 (dbm.now_ms(), task_id))
    conn.commit()


def open_tasks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM tasks WHERE resolved_at IS NULL ORDER BY created_at"
    ).fetchall()


def cwd_candidates(conn: sqlite3.Connection, limit: int = 15) -> list[Candidate]:
    """Folders where Claude Code actually runs, most used first.

    Owned sessions only (a chatops folder is never where I start work), and
    worktree paths are folded into their repository root so the list offers
    real projects, not throwaway checkouts.
    """
    rows = conn.execute(
        "SELECT target_id, cwd, COUNT(*) AS n FROM sessions "
        "WHERE cwd IS NOT NULL AND class = 'owned' "
        "GROUP BY target_id, cwd",
    ).fetchall()
    agg: dict[tuple[str, str], int] = {}
    for r in rows:
        cwd = re.sub(r"/\.claude/worktrees/.*$", "", r["cwd"])
        key = (r["target_id"], cwd)
        agg[key] = agg.get(key, 0) + r["n"]
    out = [Candidate(t, c, n) for (t, c), n in agg.items()]
    out.sort(key=lambda c: -c.count)
    return out[:limit]


def infer_folder(text: str, candidates: list[Candidate]) -> Candidate | None:
    """Deterministic inference: the folder's basename appearing as a word in
    the task text ("no benji-dp, preciso…" -> …/benji-dp). Returns a single
    confident match or None (then the UI asks)."""
    low = text.lower()
    matches: list[Candidate] = []
    for cand in candidates:
        base = os.path.basename(cand.cwd.rstrip("/")).lower()
        if len(base) < 3:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(base)}(?![a-z0-9])", low):
            matches.append(cand)
    distinct_cwds = {(m.target_id, m.cwd) for m in matches}
    if len(distinct_cwds) == 1:
        return matches[0]
    if len(distinct_cwds) > 1:
        # same project name on two targets (or nested repos): most-used wins
        # only when it clearly dominates; otherwise ask.
        matches.sort(key=lambda c: -c.count)
        if matches[0].count >= 3 * max(1, matches[1].count):
            return matches[0]
    return None
