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
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import db as dbm
from . import notify as notifier
from .config import Config, Target, tarmac_home
from .model import BLOCKED, WORKING, Session, parse_agents_json

NEXT_STEPS_REMOTE = "~/.tarmac/next-steps.jsonl"

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
    """The exact collection command per transport (SPEC §4.2)."""
    binary = (resolve_local_bin(target.claude_bin)
              if target.transport == "local" else target.claude_bin)
    inner = f"{binary} agents --json --all"
    inner = f"{target.config_dir_prefix}{inner}"
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


def collect_target(target: Target) -> TargetResult:
    """Never raises.

    The pool re-raises into the caller, so one unforeseen exception here used
    to take down the whole cycle — every other target included.
    """
    try:
        return _collect_target(target)
    except Exception as e:
        return TargetResult(target, None, error=f"{type(e).__name__}: {e}"[:500],
                            error_kind="error")


def _collect_target(target: Target) -> TargetResult:
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
    try:
        sessions = parse_agents_json(proc.stdout)
    except ValueError as e:
        return TargetResult(target, None, error=f"JSON inválido: {e}", error_kind="error")
    return TargetResult(target, sessions)


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
) -> int:
    """Returns the new row's id — the identity a notification is claimed against."""
    cur = conn.execute(
        "INSERT INTO transitions (target_id, session_id, from_state, to_state, at, uncertain) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (target_id, session_id, from_state, to_state, at, 1 if uncertain else 0),
    )
    return int(cur.lastrowid)


def record_failure(conn: sqlite3.Connection, t: Target,
                   error: str | None, kind: str | None) -> None:
    """Mark one target as failing, with backoff. Rows stay — stale, not gone.

    Reached both from a target that answered badly and from one whose mirroring
    raised: as far as the panel is concerned those are the same event, a target
    it could not read this cycle, and both must leave a trace. A failure that
    writes nothing is what let a broken collect read as a healthy one.
    """
    now = dbm.now_ms()
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
        (t.id, error, kind, fails, now + backoff),
    )


def _guarded(conn: sqlite3.Connection, name: str, fn):
    """Run one step of the cycle in its own savepoint; returns (result, error).

    The cycle writes many independent things under a single transaction. Any
    one of them raising used to discard all of it and escape the process — and
    a panel that cannot finish a collect keeps drawing its last good frame,
    which is the one failure a status panel must never have. Now a failing step
    is rolled back alone and the rest of the cycle — above all `last_collect_at`
    and the other targets — still lands.

    `name` is a SQL identifier, so it is always a literal at the call site.
    """
    conn.execute(f"SAVEPOINT {name}")
    try:
        out = fn()
    except Exception as e:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        return None, e
    conn.execute(f"RELEASE {name}")
    return out, None


def _step(conn: sqlite3.Connection, name: str, fn) -> None:
    """A guarded step whose failure has no target to be attributed to.

    Nothing in the session list depends on these (mute expiry, next_step
    enrichment, pruning), so a failure must not stop the cycle — but it does
    not get to vanish either: silence is what this whole change is about. The
    error lands in kv, where `tarmac stats` and a DB read can find it.
    """
    _, err = _guarded(conn, name, fn)
    if err is not None:
        dbm.kv_set(conn, f"last_error:{name}",
                   f"{dbm.now_ms()} {type(err).__name__}: {err}"[:500])


def apply_result(conn: sqlite3.Connection, result: TargetResult,
                 notify: bool = False) -> list[notifier.BlockedEvent]:
    """Mirror one target's collection into the DB, inside one transaction.

    Returns the sessions that just entered `blocked` and whose notification
    this cycle has claimed (SPEC §7.3) — empty unless `notify`. Sending happens
    after the commit: a claim that is never sent is a lost alert, a send that is
    never claimed is a duplicate, and duplicates are the worse failure.
    """
    now = dbm.now_ms()
    t = result.target
    events: list[notifier.BlockedEvent] = []

    if result.sessions is None:
        # isolated failure: keep old data (marked stale by last_ok_at), back off
        record_failure(conn, t, result.error, result.error_kind)
        return events

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

    # A target seen for the first time (fresh DB, target just added) can hold
    # several already-blocked sessions. Those are not news, they are the
    # backlog: notifying would greet a new install with a burst of alerts.
    cold_start = notify and conn.execute(
        "SELECT 1 FROM sessions WHERE target_id = ? LIMIT 1", (t.id,)
    ).fetchone() is None

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
        entered_blocked = eff == BLOCKED and (old is None or old["eff_state"] != BLOCKED)
        transition_id = None
        if old is None:
            # first sighting: record the entry state so blocked_since exists
            transition_id = _record_transition(conn, t.id, s.session_id, None, eff, now)
        elif old["eff_state"] != eff:
            if eff == WORKING:
                # working again -> whatever the hook summarised at the last end
                # is history (SPEC §10)
                dbm.clear_auto_next_step(conn, t.id, s.session_id)
            # If it changed while we were blind, we do NOT know when: stamp the
            # start of the blind window and mark uncertain (SPEC §4.4 — show >=,
            # never a falsely precise number).
            transition_id = _record_transition(
                conn, t.id, s.session_id, old["eff_state"], eff,
                blind_start if was_blind else now,
                uncertain=was_blind,
            )
        # `service` sessions are automation nobody conducts, and `mine: false`
        # is someone else's box: neither is ever waiting on this keyboard.
        if (entered_blocked and notify and not cold_start
                and t.mine and klass != "service" and transition_id is not None):
            if dbm.claim_notification(conn, transition_id, t.id, s.session_id):
                events.append(notifier.BlockedEvent(
                    target_id=t.id,
                    target_label=t.label,
                    session_id=s.session_id,
                    name=s.name or s.session_id[:8],
                    waiting_for=s.waiting_for,
                    cwd=s.cwd,
                ))
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
    return events


def fetch_next_steps(target: Target) -> list[dict]:
    """Drain the machine-local queue written by the SessionEnd hook (SPEC §10).

    Read-then-truncate; on any failure returns [] and leaves the file alone
    (the next cycle retries). Lines are JSON: {session_id, next_step, at}.
    """
    try:
        if target.transport == "local":
            path = tarmac_home() / "next-steps.jsonl"
            if not path.exists():
                return []
            text = path.read_text()
            path.write_text("")
        else:
            host = f"{target.ssh_user}@{target.ssh_host}" if target.ssh_user else target.ssh_host
            proc = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host,
                 f"cat {NEXT_STEPS_REMOTE} 2>/dev/null && : > {NEXT_STEPS_REMOTE}"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return []
            text = proc.stdout
    except Exception:
        return []
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


def machine_key(target: Target) -> tuple:
    """Targets that share a machine share `~/.tarmac` — and thus one queue."""
    if target.transport == "local":
        return ("local",)
    return ("ssh", target.ssh_user, target.ssh_host)


def entry_target(conn: sqlite3.Connection, targets: list[Target], entry: dict) -> Target:
    """Which target on this machine does one queue entry belong to?

    The hook records the CLAUDE_CONFIG_DIR it ran under, which is the only
    reliable discriminator: a session that ended before the first collect has
    no row to look up, and guessing would file it under the wrong account.
    Entries written by an older hook have no config_dir — those fall back to
    the target that actually has the session, then to the first target.
    """
    if len(targets) == 1:
        return targets[0]
    cfg = str(entry.get("config_dir") or "")
    if cfg:
        for t in targets:
            if t.matches_config_dir(cfg):
                return t
    uuid = str(entry.get("session_id") or "")
    if uuid:
        for t in targets:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE target_id = ? AND (uuid = ? OR session_id = ?)",
                (t.id, uuid, uuid),
            ).fetchone()
            if row:
                return t
    return targets[0]


def route_next_steps(
    conn: sqlite3.Connection, targets: list[Target], entries: list[dict]
) -> None:
    """Split one machine's drained queue across that machine's targets."""
    per_target: dict[str, list[dict]] = {}
    by_id = {t.id: t for t in targets}
    for entry in entries:
        per_target.setdefault(entry_target(conn, targets, entry).id, []).append(entry)
    for target_id, group in per_target.items():
        apply_next_steps(conn, by_id[target_id], group)


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


def collect(config: Config, conn: sqlite3.Connection, force: bool = False,
            local_only: bool = False) -> list[TargetResult]:
    """Collect every enabled target in parallel and mirror into the DB.

    `local_only` is what a Notification hook fires (`tarmac poke`, SPEC §7.3):
    the event says a session on THIS machine started waiting, and reading it
    must not drag every ssh target — with its 15s timeout — along for the ride.
    """
    now = dbm.now_ms()
    targets = []
    sources = [t for t in config.enabled_targets()
               if not local_only or t.transport == "local"]
    for t in sources:
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

    # Decided once, before the transaction: a mute that expires mid-cycle must
    # not make half the sessions notify and the other half not.
    notify_on = config.settings.notify and not notifier.is_muted(conn)
    events: list[notifier.BlockedEvent] = []
    with conn:
        _step(conn, "expire", lambda: notifier.collect_expired(conn))
        for r in results:
            ev, err = _guarded(
                conn, "apply", lambda r=r: apply_result(conn, r, notify=notify_on)
            )
            if err is None:
                events.extend(ev or [])
                continue
            # An internal failure IS the target's failure from where the panel
            # sits: record it so the rows go stale and loud instead of quietly
            # keeping the state they had before the bug.
            _guarded(conn, "apply_failed", lambda r=r, err=err: record_failure(
                conn, r.target, f"{type(err).__name__}: {err}"[:500], "error"))
        # The next_step queue is per MACHINE, not per target: two config dirs on
        # one box write to the same ~/.tarmac/next-steps.jsonl. Draining it once
        # per target would let the first one swallow the other's entries — so
        # drain once per machine and route each entry (SPEC §10).
        per_machine: dict[tuple, list[Target]] = {}
        for r in results:
            if r.sessions is not None:  # only drain machines we can reach
                per_machine.setdefault(machine_key(r.target), []).append(r.target)
        for group in per_machine.values():
            _step(conn, "next_steps", lambda group=group: route_next_steps(
                conn, group, fetch_next_steps(group[0])))
        _step(conn, "prune", lambda: (dbm.prune_service_sessions(conn),
                                      dbm.prune_notifications(conn)))
        dbm.kv_set(conn, "last_collect_at", str(now))
    # after the commit: osascript is slow and can hang, and holding a write
    # transaction open across it would block the other renderer's collect
    if events:
        notifier.dispatch(events, config.settings.locale,
                          config.settings.notify_sound)
    return results


def collect_if_stale(config: Config, conn: sqlite3.Connection) -> bool:
    """Collect only if the last cycle is older than stale_after_s (SPEC §8.0)."""
    last = dbm.kv_get(conn, "last_collect_at")
    if last and dbm.now_ms() - int(last) < config.settings.stale_after_s * 1000:
        return False
    collect(config, conn)
    return True
