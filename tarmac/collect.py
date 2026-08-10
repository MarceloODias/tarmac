"""Collector: run `claude agents --json --all` per target, mirror into SQLite.

- Targets collected in parallel; one slow/broken target never blocks others
  (SPEC §4.1).
- SSH failures are classified offline vs error by stderr (SPEC §4.4): offline
  is calm and expected behind a VPN; error is loud.
- Backoff 60s -> 2min -> 5min for offline targets, reset on first success.
- Never reads the CLI's internal JSONL/state files on disk (SPEC §2).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import db as dbm
from .config import Config, Target, tarmac_home
from .model import Session, parse_agents_json

NEXT_STEPS_REMOTE = "~/.tarmac/next-steps.jsonl"
QUEUE_SENTINEL = "===TARMAC-NEXT-STEPS==="

TARGET_TIMEOUT_S = 15
BACKOFF_MS = [60_000, 120_000, 300_000]  # SPEC §4.4

# stderr signatures that mean "can't see it" rather than "it's broken"
OFFLINE_PATTERNS = (
    "timed out",
    "timeout",
    "no route to host",
    "network is unreachable",
    "could not resolve hostname",
    "connection refused",
    "connection reset",
    "broken pipe",
)
# signatures that deserve a loud ⚠
ERROR_PATTERNS = (
    "permission denied",
    "host key verification failed",
    "too many authentication failures",
    "command not found",
    "no such file or directory",
)


def classify_failure(stderr: str) -> str:
    low = stderr.lower()
    for pat in ERROR_PATTERNS:
        if pat in low:
            return "error"
    for pat in OFFLINE_PATTERNS:
        if pat in low:
            return "offline"
    return "error"


# Where `claude` usually lives. The panel is launched by the terminal emulator,
# not by an interactive shell, so PATH can be launchd's minimal one and a bare
# `claude` fails with "command not found" — which marked every row (stale).
LOCAL_CLAUDE_CANDIDATES = (
    "~/.local/bin/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    "~/.claude/local/claude",
)


def resolve_local_bin(claude_bin: str) -> str:
    """Absolute path wins; otherwise trust PATH, then look in the usual places."""
    if "/" in claude_bin:
        return claude_bin
    if shutil.which(claude_bin):
        return claude_bin
    for candidate in LOCAL_CLAUDE_CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return claude_bin  # let it fail loudly, with a clear stderr


def build_command(target: Target) -> list[str]:
    """The exact collection command per transport (SPEC §4.2), plus the
    next_step queue in the SAME round trip.

    Draining the queue used to be a second SSH connection per cycle — 1440
    connections a day, all of them empty while the hook is uninstalled. One
    command, one connection, and no network I/O inside the DB transaction.
    """
    binary = (resolve_local_bin(target.claude_bin)
              if target.transport == "local" else target.claude_bin)
    # `: > file` is a special builtin: a failed redirection aborts the whole
    # shell, so a missing queue file used to break the entire collection.
    inner = (
        f"{binary} agents --json --all || exit $?; "
        f"echo '{QUEUE_SENTINEL}'; "
        f"if [ -f {NEXT_STEPS_REMOTE} ]; then "
        f"cat {NEXT_STEPS_REMOTE} 2>/dev/null; "
        f"cp /dev/null {NEXT_STEPS_REMOTE} 2>/dev/null; "
        f"fi; true"
    )
    if target.needs_config_dir_export:
        inner = f"CLAUDE_CONFIG_DIR={shlex.quote(target.config_dir)} {inner}"
    if target.transport == "local":
        return ["sh", "-c", inner]
    host = f"{target.ssh_user}@{target.ssh_host}" if target.ssh_user else target.ssh_host
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        host,
        inner,
    ]


@dataclass
class TargetResult:
    target: Target
    sessions: list[Session] | None  # None = collection failed
    error: str | None = None
    error_kind: str | None = None   # 'offline' | 'error'
    next_steps: list[dict] = field(default_factory=list)  # drained in the same trip


def collect_target(target: Target) -> TargetResult:
    cmd = build_command(target)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TARGET_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        _reset_dead_control_socket(target)
        return TargetResult(target, None, error="timeout", error_kind="offline")
    except FileNotFoundError as e:
        return TargetResult(target, None, error=str(e), error_kind="error")

    if proc.returncode != 0:
        kind = classify_failure(proc.stderr or proc.stdout or "")
        if kind == "offline":
            _reset_dead_control_socket(target)
        return TargetResult(
            target, None,
            error=(proc.stderr or proc.stdout or "").strip()[:500] or f"exit {proc.returncode}",
            error_kind=kind,
        )
    payload, _, queue = proc.stdout.partition(QUEUE_SENTINEL)
    try:
        sessions = parse_agents_json(payload)
    except ValueError as e:
        return TargetResult(target, None, error=f"JSON inválido: {e}", error_kind="error")
    return TargetResult(target, sessions, next_steps=parse_queue(queue))


def parse_queue(text: str) -> list[dict]:
    """JSONL written by the SessionEnd hook; junk lines are skipped, never fatal."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("session_id") and entry.get("next_step"):
            out.append(entry)
    return out


def _reset_dead_control_socket(target: Target) -> None:
    """A ControlMaster socket goes stale when the VPN drops (SPEC §4.4).

    `ssh -O check` probes it; `ssh -O exit` tears it down so the next attempt
    starts clean. Best-effort, never raises.
    """
    if target.transport != "ssh":
        return
    host = f"{target.ssh_user}@{target.ssh_host}" if target.ssh_user else target.ssh_host
    try:
        check = subprocess.run(
            ["ssh", "-O", "check", host], capture_output=True, text=True, timeout=5,
        )
        if check.returncode == 0:
            # master alive but connection failing -> socket points at a dead path
            subprocess.run(
                ["ssh", "-O", "exit", host], capture_output=True, text=True, timeout=5,
            )
    except Exception:
        pass


def _record_transition(
    conn: sqlite3.Connection,
    target_id: str,
    session_id: str,
    from_state: str | None,
    to_state: str,
    at: int,
    uncertain: bool = False,
) -> None:
    conn.execute(
        "INSERT INTO transitions (target_id, session_id, from_state, to_state, at, uncertain) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (target_id, session_id, from_state, to_state, at, 1 if uncertain else 0),
    )


def apply_result(conn: sqlite3.Connection, result: TargetResult) -> None:
    """Mirror one target's collection into the DB, inside one transaction."""
    now = dbm.now_ms()
    t = result.target

    if result.sessions is None:
        # isolated failure: keep old data (marked stale by last_ok_at), back off
        row = conn.execute(
            "SELECT fail_count FROM target_status WHERE target_id = ?", (t.id,)
        ).fetchone()
        fails = (row["fail_count"] if row else 0) + 1
        backoff = BACKOFF_MS[min(fails - 1, len(BACKOFF_MS) - 1)]
        conn.execute(
            "INSERT INTO target_status (target_id, last_error, error_kind, fail_count, next_retry_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(target_id) DO UPDATE SET "
            "last_error = excluded.last_error, error_kind = excluded.error_kind, "
            "fail_count = excluded.fail_count, next_retry_at = excluded.next_retry_at",
            (t.id, result.error, result.error_kind, fails, now + backoff),
        )
        return

    # success: was the target unobservable before this read? (affects blocked_since)
    prev = conn.execute(
        "SELECT last_ok_at, fail_count FROM target_status WHERE target_id = ?", (t.id,)
    ).fetchone()
    was_blind = bool(prev and prev["fail_count"] and prev["fail_count"] >= t.offline_after)
    blind_start = prev["last_ok_at"] if (was_blind and prev["last_ok_at"]) else now

    conn.execute(
        "INSERT INTO target_status (target_id, last_ok_at, last_error, error_kind, fail_count, next_retry_at) "
        "VALUES (?, ?, NULL, NULL, 0, NULL) "
        "ON CONFLICT(target_id) DO UPDATE SET "
        "last_ok_at = excluded.last_ok_at, last_error = NULL, error_kind = NULL, "
        "fail_count = 0, next_retry_at = NULL",
        (t.id, now),
    )

    seen_ids = set()
    for s in result.sessions:
        seen_ids.add(s.session_id)
        klass = t.classify(s.cwd, s.name)
        eff = s.effective_state
        old = conn.execute(
            "SELECT eff_state, first_seen_at, gone FROM sessions "
            "WHERE target_id = ? AND session_id = ?",
            (t.id, s.session_id),
        ).fetchone()
        first_seen = old["first_seen_at"] if old else now
        if old is None:
            # first sighting: record the entry state so blocked_since exists
            _record_transition(conn, t.id, s.session_id, None, eff, now)
        elif old["eff_state"] != eff:
            # If it changed while we were blind, we do NOT know when: stamp the
            # start of the blind window and mark uncertain (SPEC §4.4 — show >=,
            # never a falsely precise number).
            _record_transition(
                conn, t.id, s.session_id, old["eff_state"], eff,
                blind_start if was_blind else now,
                uncertain=was_blind,
            )
        conn.execute(
            "INSERT INTO sessions (target_id, session_id, short_id, uuid, name, kind, state, "
            "  status, waiting_for, cwd, pid, started_at, first_seen_at, last_seen_at, gone, "
            "  class, eff_state, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?) "
            "ON CONFLICT(target_id, session_id) DO UPDATE SET "
            "short_id = excluded.short_id, uuid = excluded.uuid, name = excluded.name, "
            "kind = excluded.kind, state = excluded.state, status = excluded.status, "
            "waiting_for = excluded.waiting_for, cwd = excluded.cwd, pid = excluded.pid, "
            "started_at = excluded.started_at, last_seen_at = excluded.last_seen_at, "
            "gone = 0, class = excluded.class, eff_state = excluded.eff_state, "
            "raw_json = excluded.raw_json",
            (
                t.id, s.session_id, s.short_id, s.uuid, s.name, s.kind, s.state,
                s.status, s.waiting_for, s.cwd, s.pid, s.started_at, first_seen,
                now, klass, eff, s.raw_json,
            ),
        )

    # gone = 1, never delete (SPEC §5.2) — meta and transcripts outlive the listing
    if seen_ids:
        placeholders = ",".join("?" * len(seen_ids))
        conn.execute(
            f"UPDATE sessions SET gone = 1 WHERE target_id = ? "
            f"AND session_id NOT IN ({placeholders}) AND gone = 0",
            (t.id, *seen_ids),
        )
    else:
        conn.execute(
            "UPDATE sessions SET gone = 1 WHERE target_id = ? AND gone = 0", (t.id,)
        )


def apply_next_steps(conn: sqlite3.Connection, target: Target, entries: list[dict]) -> None:
    for entry in entries:
        uuid = str(entry["session_id"])
        row = conn.execute(
            "SELECT session_id FROM sessions WHERE target_id = ? "
            "AND (uuid = ? OR session_id = ?)",
            (target.id, uuid, uuid),
        ).fetchone()
        session_id = row["session_id"] if row else uuid
        meta = dbm.get_meta(conn, target.id, session_id)
        # auto never overwrites what I typed by hand (SPEC §10)
        if meta and meta["next_step"] and meta["next_step_origin"] == "manual":
            continue
        dbm.upsert_meta(
            conn, target.id, session_id,
            next_step=str(entry["next_step"]).strip()[:200],
            next_step_origin="auto",
        )


def collect(config: Config, conn: sqlite3.Connection, force: bool = False) -> list[TargetResult]:
    """Collect every enabled target in parallel and mirror into the DB."""
    now = dbm.now_ms()
    targets = []
    for t in config.enabled_targets():
        if not force:
            row = conn.execute(
                "SELECT next_retry_at FROM target_status WHERE target_id = ?", (t.id,)
            ).fetchone()
            if row and row["next_retry_at"] and row["next_retry_at"] > now:
                continue  # backing off
        targets.append(t)

    results: list[TargetResult] = []
    if targets:
        with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
            results = list(pool.map(collect_target, targets))
    with conn:
        for r in results:
            apply_result(conn, r)
            if r.next_steps:  # already drained in the collection round trip
                apply_next_steps(conn, r.target, r.next_steps)
        dbm.prune_service_sessions(conn)
        dbm.kv_set(conn, "last_collect_at", str(now))
    return results


def collect_if_stale(config: Config, conn: sqlite3.Connection) -> bool:
    """Collect only if the last cycle is older than stale_after_s (SPEC §8.0)."""
    last = dbm.kv_get(conn, "last_collect_at")
    if last and dbm.now_ms() - int(last) < config.settings.stale_after_s * 1000:
        return False
    collect(config, conn)
    return True
