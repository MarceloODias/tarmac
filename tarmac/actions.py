"""Actions: open-or-focus terminal tabs, logs/stop/rm, copy resume (SPEC §9).

Terminal adapter policy (SPEC §15.2): if iTerm2 is unavailable or AppleScript
fails, degrade to printing the command and copying it to the clipboard — never
fail silently.
"""

from __future__ import annotations

import shlex
import sqlite3
import subprocess

from . import db as dbm
from .config import Target
from .derive import Row

# FINDINGS F1: address iTerm2 by bundle id — the name "iTerm2" doesn't even
# compile right after install; the bundle id is robust from second zero.
ITERM_ID = "com.googlecode.iterm2"


# ---------- command building (SPEC §9.1) ----------

def attach_command(target: Target, row: Row) -> str:
    """The shell command that lands inside the session."""
    if row.short_id:
        inner = f"{target.claude_bin} attach {shlex.quote(row.short_id)}"
    elif row.uuid:
        # fallback: works from any directory since v2.1.223
        inner = f"{target.claude_bin} --resume {shlex.quote(row.uuid)}"
    else:
        raise ValueError("sessão sem short_id e sem uuid — nada para anexar")
    if target.needs_config_dir_export:
        inner = f"CLAUDE_CONFIG_DIR={shlex.quote(target.config_dir)} {inner}"
    if target.transport == "ssh":
        host = f"{target.ssh_user}@{target.ssh_host}" if target.ssh_user else target.ssh_host
        return f"ssh -t {shlex.quote(host)} {shlex.quote(inner)}"  # -t is mandatory
    return inner


def resume_command(target: Target, row: Row) -> str:
    if not row.uuid:
        raise ValueError("sessão sem uuid — não há comando de resume")
    inner = f"claude --resume {row.uuid}"
    if target.needs_config_dir_export:
        inner = f"CLAUDE_CONFIG_DIR={shlex.quote(target.config_dir)} {inner}"
    if target.transport == "ssh":
        host = f"{target.ssh_user}@{target.ssh_host}" if target.ssh_user else target.ssh_host
        return f"ssh -t {shlex.quote(host)} {shlex.quote(inner)}"
    return inner


def remote_claude(target: Target, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a claude subcommand on the target (logs/stop/rm — SPEC §9.2)."""
    inner = f"{target.claude_bin} {' '.join(shlex.quote(a) for a in args)}"
    if target.needs_config_dir_export:
        inner = f"CLAUDE_CONFIG_DIR={shlex.quote(target.config_dir)} {inner}"
    if target.transport == "ssh":
        host = f"{target.ssh_user}@{target.ssh_host}" if target.ssh_user else target.ssh_host
        cmd = ["ssh", "-o", "BatchMode=yes", host, inner]
    else:
        cmd = ["sh", "-c", inner]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---------- clipboard ----------

def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["pbcopy"], input=text, text=True, timeout=5, check=True)
        return True
    except Exception:
        return False


# ---------- iTerm2 via AppleScript (SPEC §9.0) ----------

def _osascript(script: str, timeout: int = 30) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:
        return False, str(e)
    if proc.returncode != 0:
        return False, (proc.stderr or "").strip()
    return True, proc.stdout.strip()


def iterm_available() -> bool:
    ok, out = _osascript(f'exists application id "{ITERM_ID}"', timeout=10)
    return ok and out == "true"


def focus_tab(handle: str) -> str:
    """'found' | 'missing' | error text (SPEC §9.0)."""
    script = f'''
    with timeout of 20 seconds
    tell application id "{ITERM_ID}"
      repeat with w in windows
        repeat with t in tabs of w
          repeat with sess in sessions of t
            if id of sess is "{handle}" then
              select w
              select t
              select sess
              activate
              return "found"
            end if
          end repeat
        end repeat
      end repeat
    end tell
    return "missing"
    end timeout
    '''
    ok, out = _osascript(script)
    return out if ok else f"error: {out}"


def create_tab(command: str) -> tuple[str | None, str]:
    """Create a window running `command`; return (handle, message)."""
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    with timeout of 20 seconds
    tell application id "{ITERM_ID}"
      create window with default profile
      tell current session of current window
        write text "{escaped}"
        set h to id
      end tell
      activate
      return h
    end tell
    end timeout
    '''
    ok, out = _osascript(script)
    if ok and out:
        return out, "ok"
    return None, out


def open_or_focus(
    conn: sqlite3.Connection, target: Target, row: Row,
    command: str | None = None,
) -> str:
    """Click action: focus the registered tab if it still exists, else create
    one and register the handle (SPEC §9.0). Returns a human message.

    `command` overrides what runs in a fresh tab (e.g. a resume instead of an
    attach); the handle key is the same either way — one tab per session."""
    command = command or attach_command(target, row)

    handle_row = conn.execute(
        "SELECT handle FROM terminal_handles WHERE target_id = ? AND session_id = ?",
        (target.id, row.session_id),
    ).fetchone()
    if handle_row and handle_row["handle"]:
        result = focus_tab(handle_row["handle"])
        if result == "found":
            return "focado"
        # stale or errored handle: forget it and open fresh
        conn.execute(
            "DELETE FROM terminal_handles WHERE target_id = ? AND session_id = ?",
            (target.id, row.session_id),
        )
        conn.commit()

    handle, msg = create_tab(command)
    if handle:
        conn.execute(
            "INSERT INTO terminal_handles (target_id, session_id, handle, opened_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(target_id, session_id) DO UPDATE SET "
            "handle = excluded.handle, opened_at = excluded.opened_at",
            (target.id, row.session_id, handle, dbm.now_ms()),
        )
        conn.commit()
        return "aberto"

    # degrade: print + clipboard, never fail silently (SPEC §15.2)
    copied = copy_to_clipboard(command)
    return (
        f"iTerm2 indisponível ({msg}). Comando "
        + ("copiado para o clipboard: " if copied else "para colar manualmente: ")
        + command
    )


def task_command(target: Target, cwd: str, text: str) -> str:
    """Start a fresh Claude Code in the task's folder, with the task text as
    the opening prompt."""
    inner = f"cd {shlex.quote(cwd)} && {target.claude_bin} {shlex.quote(text)}"
    if target.needs_config_dir_export:
        inner = f"CLAUDE_CONFIG_DIR={shlex.quote(target.config_dir)} {inner}"
    if target.transport == "ssh":
        host = f"{target.ssh_user}@{target.ssh_host}" if target.ssh_user else target.ssh_host
        return f"ssh -t {shlex.quote(host)} {shlex.quote(inner)}"
    return inner


def open_task(
    conn: sqlite3.Connection, target: Target, task_id: int, cwd: str, text: str,
) -> str:
    """Open-or-focus a terminal tab running Claude Code for a standalone task."""
    key = f"task:{task_id}"
    command = task_command(target, cwd, text)
    handle_row = conn.execute(
        "SELECT handle FROM terminal_handles WHERE target_id = ? AND session_id = ?",
        (target.id, key),
    ).fetchone()
    if handle_row and handle_row["handle"]:
        if focus_tab(handle_row["handle"]) == "found":
            return "focado"
        conn.execute(
            "DELETE FROM terminal_handles WHERE target_id = ? AND session_id = ?",
            (target.id, key),
        )
        conn.commit()
    handle, msg = create_tab(command)
    if handle:
        conn.execute(
            "INSERT INTO terminal_handles (target_id, session_id, handle, opened_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(target_id, session_id) DO UPDATE SET "
            "handle = excluded.handle, opened_at = excluded.opened_at",
            (target.id, key, handle, dbm.now_ms()),
        )
        conn.commit()
        return "aberto"
    copied = copy_to_clipboard(command)
    return (
        f"iTerm2 indisponível ({msg}). Comando "
        + ("copiado para o clipboard: " if copied else "para colar manualmente: ")
        + command
    )


def close_resolved_tabs(
    conn: sqlite3.Connection, idle_min: int = 30,
) -> list[str]:
    """`Fechar abas resolvidas` (SPEC §9.0.1): close tabs whose session is no
    longer blocked and whose handle is older than idle_min. Explicit action —
    never automatic."""
    cutoff = dbm.now_ms() - idle_min * 60_000
    rows = conn.execute(
        "SELECT h.target_id, h.session_id, h.handle FROM terminal_handles h "
        "LEFT JOIN sessions s ON s.target_id = h.target_id AND s.session_id = h.session_id "
        "WHERE h.opened_at < ? AND (s.eff_state IS NULL OR s.eff_state != 'blocked')",
        (cutoff,),
    ).fetchall()
    closed = []
    for r in rows:
        script = f'''
        with timeout of 20 seconds
        tell application id "{ITERM_ID}"
          repeat with w in windows
            repeat with t in tabs of w
              repeat with sess in sessions of t
                if id of sess is "{r["handle"]}" then
                  close t
                  return "closed"
                end if
              end repeat
            end repeat
          end repeat
        end tell
        return "missing"
        end timeout
        '''
        ok, out = _osascript(script)
        if ok and out in ("closed", "missing"):
            conn.execute(
                "DELETE FROM terminal_handles WHERE target_id = ? AND session_id = ?",
                (r["target_id"], r["session_id"]),
            )
            if out == "closed":
                closed.append(r["session_id"])
    conn.commit()
    return closed
