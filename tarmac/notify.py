"""macOS notification when a session starts needing you (SPEC §7.3).

SPEC §7 closed the door on this: the panel is always open, and the badge *is*
the alert. Marcelo reopened it — a badge only alerts while you are looking at
the screen, and a blocked session is precisely the case where you are not.

What §7 bought was idempotency, and that is not given back. One notification
belongs to ONE transition into `blocked`, identified by (target, session, the
timestamp of that transition) and claimed in the `notifications` table inside
the same transaction that records the transition. A session that stays blocked
for an hour, a re-run of `collect`, and two renderers collecting at the same
second all produce exactly one — the claim is what decides, not the send.

Muting is two levels, on purpose:

  settings.notify: false   in targets.yaml — hard off, nothing to toggle.
  a mute stored in the DB  — the toggle: `tarmac notify off`, `N` in the TUI,
                             or the menu-bar item. `tarmac notify mute 1h`
                             expires by itself, which is the one that fits a
                             meeting: you cannot forget to switch it back on.

Both renderers show a 🔕 in the badge while muted — a silent panel must never
be indistinguishable from a quiet one.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from . import db as dbm
from .strings import tr

# Absolute path on purpose: the panel is launched by the terminal emulator with
# launchd's minimal environment, and a bare name already cost this project every
# row going (stale) once when `claude` fell off PATH. osascript is a system
# binary — /usr/bin is not a guess.
OSASCRIPT = "/usr/bin/osascript"

MUTE_KEY = "notify_mute_until"
FOREVER = "forever"

# macOS caps how many notifications are useful at once; past this it is one
# summary instead of a stack the user has to dismiss one by one.
GROUP_ABOVE = 3


@dataclass
class BlockedEvent:
    """A session that just entered `blocked` — one notification's worth."""
    target_id: str
    target_label: str
    session_id: str
    name: str
    waiting_for: str | None = None
    cwd: str | None = None


# ---------- mute state (SPEC §7.3) ----------

def mute_until(conn) -> str | None:
    """Raw kv value: None, 'forever', or an epoch-ms string."""
    return dbm.kv_get(conn, MUTE_KEY) or None


def is_muted(conn, now: int | None = None) -> bool:
    """Pure read — a timed mute simply stops being true once it is past.

    Deliberately does not clean up the expired key: `build_view` calls this on
    every render, and a renderer that writes (and commits) mid-build would be
    committing whatever else the caller had open on that connection. Tidying is
    `collect_expired`, which runs where a write is expected.
    """
    value = mute_until(conn)
    if not value:
        return False
    if value == FOREVER:
        return True
    try:
        deadline = int(value)
    except ValueError:  # corrupt value: fail unmuted, never silently silent
        return False
    return deadline > (now or dbm.now_ms())


def collect_expired(conn, now: int | None = None) -> bool:
    """Drop a mute that has run out. Caller owns the transaction."""
    value = mute_until(conn)
    if not value or value == FOREVER or is_muted(conn, now):
        return False
    conn.execute("DELETE FROM kv WHERE key = ?", (MUTE_KEY,))
    return True


def mute(conn, until_ms: int | None = None) -> None:
    """`until_ms=None` mutes indefinitely; otherwise until that instant."""
    dbm.kv_set(conn, MUTE_KEY, FOREVER if until_ms is None else str(int(until_ms)))
    conn.commit()


def unmute(conn) -> None:
    conn.execute("DELETE FROM kv WHERE key = ?", (MUTE_KEY,))
    conn.commit()


def toggle(conn) -> bool:
    """Flip and return the new muted state — the one-keystroke path."""
    if is_muted(conn):
        unmute(conn)
        return False
    mute(conn)
    return True


def status_line(enabled: bool, muted: bool, until_ms: int | None,
                locale: str = "en") -> str:
    """One line describing where notifications stand.

    Pure on purpose: the CLI has a connection in hand, the renderers only have
    the View, and neither should have to open the DB again to draw a menu item.
    """
    from datetime import datetime
    if not enabled:
        return f"{tr(locale, 'notify_state_off')} (settings.notify: false)"
    if not available():
        return f"{tr(locale, 'notify_state_off')} — {tr(locale, 'notify_unsupported')}"
    if not muted:
        return tr(locale, "notify_state_on")
    if until_ms is None:
        return tr(locale, "notify_state_off")
    when = datetime.fromtimestamp(until_ms / 1000).strftime("%H:%M")
    return tr(locale, "notify_state_until", when=when)


# ---------- delivery ----------

def available() -> bool:
    return sys.platform == "darwin"


def _escape(text: str) -> str:
    """AppleScript string literal: backslash and double quote only."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def send(title: str, message: str, subtitle: str = "", sound: str = "") -> bool:
    """Post one macOS notification. Never raises — a failed alert must not
    take down a collect cycle, and macOS Focus already silences these for us."""
    if not available():
        return False
    script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
    if subtitle:
        script += f' subtitle "{_escape(subtitle)}"'
    if sound:
        script += f' sound name "{_escape(sound)}"'
    try:
        proc = subprocess.run(
            [OSASCRIPT, "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _line(event: BlockedEvent) -> str:
    where = f" · {event.target_label}" if event.target_label else ""
    return f"{event.name}{where}"


def dispatch(events: list[BlockedEvent], locale: str = "en", sound: str = "") -> int:
    """Turn claimed events into notifications. Returns how many were posted."""
    if not events:
        return 0
    if len(events) > GROUP_ABOVE:
        ok = send(
            tr(locale, "notify_title_many", n=len(events)),
            ", ".join(_line(e) for e in events),
            sound=sound,
        )
        return 1 if ok else 0
    sent = 0
    for event in events:
        body = event.waiting_for or tr(locale, "notify_body_default")
        if event.cwd:
            body = f"{body} — {event.cwd}"
        if send(tr(locale, "notify_title"), body, subtitle=_line(event), sound=sound):
            sent += 1
    return sent
