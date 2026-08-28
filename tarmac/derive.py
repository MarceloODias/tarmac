"""Turn DB rows into the view model both renderers share (SPEC §7, §8).

Everything user-visible — badge, sections, ordering, escalation — is computed
here once, so SwiftBar and the TUI are thin layers over the same decisions.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from . import db as dbm
from . import notify
from .config import Config
from .model import BLOCKED, IDLE, TERMINAL_STATES, WORKING

ESCALATION_WARN_S = 5 * 60      # 5-30 min: highlight color (SPEC §7.1)
ESCALATION_ALARM_S = 30 * 60    # > 30 min: alarm color + ▲


@dataclass
class Row:
    target_id: str
    target_label: str
    session_id: str
    display_name: str
    eff_state: str
    kind: str
    cwd: str | None
    short_id: str | None
    uuid: str | None
    waiting_for: str | None
    gone: bool
    stale: bool                  # data from an offline target's last snapshot
    pid: int | None = None       # absent = no live worker behind this session
    wait_s: int | None = None    # seconds blocked, None if not blocked
    wait_uncertain: bool = False  # blocked during an offline window -> ">="
    due_at: int | None = None
    due_label: str | None = None
    overdue: bool = False
    hidden_until_due: bool = False
    pinned: bool = False
    next_step: str | None = None
    never_named: bool = False
    checklist: tuple[int, int] | None = None  # (done, total)
    permission_prompt: bool = False
    target_account: str = ""     # blank for the default account


@dataclass
class ServiceLine:
    target_id: str
    target_label: str
    label: str
    active: int
    stuck: int                  # blocked beyond threshold
    stuck_oldest_s: int = 0


@dataclass
class TargetLine:
    target_id: str
    label: str
    # 'stale' is not a kind of failure — it is data that got old without one,
    # which is the case a panel is most likely to render as healthy.
    state: str                  # 'ok' | 'stale' | 'offline' | 'error'
    last_error: str | None
    age_s: int | None           # seconds since last successful read


@dataclass
class View:
    overdue: list[Row] = field(default_factory=list)       # PRA HOJE
    blocked: list[Row] = field(default_factory=list)       # PRECISA DE VOCÊ
    working: list[Row] = field(default_factory=list)       # TRABALHANDO
    scheduled: list[Row] = field(default_factory=list)     # AGENDADO
    services: list[ServiceLine] = field(default_factory=list)
    done: list[Row] = field(default_factory=list)          # CONCLUÍDO
    other: list[Row] = field(default_factory=list)         # idle etc.
    targets: list[TargetLine] = field(default_factory=list)
    last_collect_ms: int | None = None
    notify_muted: bool = False   # SPEC §7.3 — a silenced panel says so
    notify_mute_until: int | None = None  # epoch ms; None = until switched back on

    @property
    def has_error(self) -> bool:
        return any(t.state == "error" for t in self.targets)

    @property
    def has_stale(self) -> bool:
        return any(t.state == "stale" for t in self.targets)


def _wait_of(conn: sqlite3.Connection, r: sqlite3.Row, now: int) -> tuple[int | None, bool]:
    bs = dbm.blocked_since(conn, r["target_id"], r["session_id"])
    if bs is None:
        # blocked but no transition recorded (fresh DB): fall back to last_seen
        return max(0, (now - (r["last_seen_at"] or now)) // 1000), False
    at, uncertain = bs
    return max(0, (now - at) // 1000), uncertain


def account_width(rows: list[Row], cap: int = 10) -> int:
    """Width of the account column, 0 when every session is on the default one.

    A single-account setup pays nothing for it; with two accounts the column is
    as wide as the longest name, and the default account renders as blanks —
    the label column stays short instead of being truncated to "Mac (per".
    """
    return min(cap, max((len(r.target_account) for r in rows), default=0))


def build_view(
    config: Config,
    conn: sqlite3.Connection,
    mine_only: bool = True,
    now: int | None = None,
) -> View:
    now = now or dbm.now_ms()
    view = View()

    last = dbm.kv_get(conn, "last_collect_at")
    view.last_collect_ms = int(last) if last else None
    view.notify_muted = config.settings.notify and notify.is_muted(conn, now)
    if view.notify_muted:
        raw = notify.mute_until(conn)
        view.notify_mute_until = None if raw == notify.FOREVER else int(raw)

    status_by_target = {
        r["target_id"]: r for r in conn.execute("SELECT * FROM target_status")
    }
    wanted: dict[str, object] = {}
    # Targets whose rows may no longer reflect reality — a failing read, or a
    # last successful one too old to trust.
    stale_targets: set[str] = set()
    for t in config.enabled_targets():
        if mine_only and not t.mine:
            continue
        wanted[t.id] = t
        st = status_by_target.get(t.id)
        if st is None:
            # never read: no data yet, so no old data either
            view.targets.append(TargetLine(t.id, t.label, "ok", None, None))
            continue
        age = (now - st["last_ok_at"]) // 1000 if st["last_ok_at"] else None
        too_old = age is not None and age >= config.settings.stale_data_after_s
        if st["error_kind"] or too_old:
            stale_targets.add(t.id)
        if st["error_kind"] is None or (st["fail_count"] or 0) < t.offline_after:
            state = "ok" if st["error_kind"] is None else (
                # failing but under the offline_after threshold: keep calm only
                # if failures look like connectivity on an intermittent target
                "ok" if (t.expect_intermittent and st["error_kind"] == "offline") else st["error_kind"]
            )
        else:
            state = st["error_kind"]
        if state == "offline" and not t.expect_intermittent:
            state = "error"  # unexpected unreachability is loud (SPEC §4.4)
        # Data has an age of its own, independent of whether the last attempt
        # reported anything. A collect that dies before it can record a failure
        # leaves error_kind NULL forever, and the panel then draws a frozen
        # frame as a healthy one — which is how a nine-hour-old list once read
        # as current. Age is the check that does not depend on the failure
        # path working.
        if state == "ok" and too_old:
            state = "stale"
        view.targets.append(TargetLine(t.id, t.label, state, st["last_error"], age))

    service_agg: dict[tuple[str, str], ServiceLine] = {}

    rows = conn.execute("SELECT * FROM sessions ORDER BY target_id, started_at").fetchall()
    for r in rows:
        t = wanted.get(r["target_id"])
        if t is None:
            continue
        stale = r["target_id"] in stale_targets
        meta = dbm.get_meta(conn, r["target_id"], r["session_id"])
        eff = r["eff_state"] or "unknown"

        if r["class"] == "service":
            if r["gone"] or eff in TERMINAL_STATES:
                continue
            label = next(
                (rule.label or "service" for rule in t.session_classes
                 if rule.class_ == "service" and rule.matches(r["cwd"], r["name"])),
                "service",
            )
            key = (t.id, label)
            line = service_agg.setdefault(
                key, ServiceLine(t.id, t.label, label, 0, 0)
            )
            line.active += 1
            if eff == BLOCKED:
                wait_s, _ = _wait_of(conn, r, now)
                if wait_s >= config.settings.service_stuck_min * 60:
                    line.stuck += 1
                    line.stuck_oldest_s = max(line.stuck_oldest_s, wait_s)
            continue

        checklist = conn.execute(
            "SELECT COUNT(*) AS total, SUM(done) AS done FROM session_checklist "
            "WHERE target_id = ? AND session_id = ?",
            (r["target_id"], r["session_id"]),
        ).fetchone()

        from .model import parse_session  # local import to avoid cycle at module load
        import json
        never_named = False
        try:
            s = parse_session(json.loads(r["raw_json"])) if r["raw_json"] else None
            never_named = s.never_named if s else False
        except (ValueError, TypeError):
            pass

        row = Row(
            target_id=r["target_id"],
            target_label=t.label,
            target_account=t.account,
            session_id=r["session_id"],
            display_name=(meta["alias"] if meta and meta["alias"] else None)
            or r["name"] or r["session_id"][:8],
            eff_state=eff,
            kind=r["kind"] or "unknown",
            cwd=r["cwd"],
            short_id=r["short_id"],
            uuid=r["uuid"],
            waiting_for=r["waiting_for"],
            gone=bool(r["gone"]),
            stale=stale,
            pid=r["pid"],
            due_at=meta["due_at"] if meta else None,
            due_label=meta["due_label"] if meta else None,
            hidden_until_due=bool(meta["hide_until_due"]) if meta else False,
            pinned=bool(meta["pinned"]) if meta else False,
            next_step=meta["next_step"] if meta else None,
            never_named=never_named,
            checklist=(checklist["done"] or 0, checklist["total"])
            if checklist and checklist["total"] else None,
            permission_prompt=(r["waiting_for"] == "permission prompt"),
        )

        resolved = bool(meta and meta["resolved_at"] and meta["due_at"]
                        and meta["resolved_at"] >= meta["due_at"])
        if row.due_at and row.due_at <= now and not resolved:
            # overdue: rises to PRA HOJE and stays until acted on (SPEC §6.1)
            row.overdue = True
            view.overdue.append(row)
            continue
        if row.due_at and row.due_at > now and row.hidden_until_due:
            view.scheduled.append(row)
            continue

        # A session that vanished from the --json is NOT live any more: its last
        # known state is a memory, not a fact. Rendering it as working/blocked
        # kept dead rows in the list and — worse — inflated the badge forever.
        # Intent (a reminder, a pin) is mine and does outlive the listing.
        if row.gone and not row.pinned:
            continue

        if eff == BLOCKED:
            row.wait_s, row.wait_uncertain = _wait_of(conn, r, now)
            view.blocked.append(row)
        elif row.due_at and row.due_at > now:
            view.scheduled.append(row)
        elif eff == WORKING:
            view.working.append(row)
        elif eff in TERMINAL_STATES:
            view.done.append(row)
        elif eff == IDLE:
            view.other.append(row)

    # standalone tasks (intent without a session): live in AGENDADO, rise to
    # PRA HOJE when overdue, open by starting Claude Code in their folder
    from .tasks import open_tasks
    labels = {t.id: t.label for t in config.enabled_targets()}
    accounts = {t.id: t.account for t in config.enabled_targets()}
    for tk in open_tasks(conn):
        row = Row(
            target_id=tk["target_id"] or "",
            target_label=labels.get(tk["target_id"], "?") if tk["target_id"] else "?",
            target_account=accounts.get(tk["target_id"], "") if tk["target_id"] else "",
            session_id=f"task:{tk['id']}",
            display_name=tk["text"],
            eff_state="task",
            kind="task",
            cwd=tk["cwd"],
            short_id=None,
            uuid=None,
            waiting_for=None,
            gone=False,
            stale=False,
            due_at=tk["due_at"],
            due_label=tk["due_label"],
        )
        if row.due_at and row.due_at <= now:
            row.overdue = True
            view.overdue.append(row)
        else:
            view.scheduled.append(row)

    # every section groups by folder (see _by_folder); the key below is what
    # ranks sessions inside a folder, and breaks ties between folders
    view.overdue = _by_folder(view.overdue, lambda r: r.due_at or 0)
    view.blocked = _by_folder(view.blocked, lambda r: -(r.wait_s or 0))
    view.scheduled = _by_folder(view.scheduled,
                                lambda r: (r.due_at is None, r.due_at or 0))
    view.working = _by_folder(view.working, _pin_then_folder)
    view.other = _by_folder(view.other, _pin_then_folder)
    view.done = _by_folder(view.done, _pin_then_folder)
    view.services = sorted(service_agg.values(), key=lambda s: (s.target_label, s.label))
    return view


def _pin_then_folder(row: Row) -> tuple[bool, str, str]:
    """Rank for sections with nothing urgent to escalate (working, idle, done).

    A pinned session leads, and the folder path comes before the name so that
    folders of the same size land in path order — which keeps sibling projects
    of one tree (…/inpowered/*) reading as a block instead of being split up by
    session names.
    """
    return (not row.pinned, row.cwd or "", row.display_name)


def _by_folder(rows: list[Row], key) -> list[Row]:
    """Sessions of the same folder side by side (SPEC §8.0: the panel is read
    top-down, and neighbouring subjects should read as one block).

    Folder order: most sessions first (that is where the attention goes), then
    the folder's most urgent session, then the path so it is stable. Inside a
    folder the section's own key still applies.

    Note this means a section's first row is NOT necessarily its most urgent
    one — `badge` therefore takes the worst wait explicitly instead of reading
    view.blocked[0].
    """
    per_folder: dict[str, list] = {}
    for row in rows:
        per_folder.setdefault(row.cwd or "", []).append(key(row))
    rank = {folder: (-len(keys), min(keys), folder)
            for folder, keys in per_folder.items()}
    return sorted(rows, key=lambda r: (rank[r.cwd or ""], key(r)))


def badge(view: View) -> tuple[str, str]:
    """(text, severity) for the menu bar title (SPEC §8.1).

    severity: 'ok' | 'info' | 'warn' | 'alarm' — renderers map it to color.
    """
    parts: list[str] = []
    severity = "ok"
    if view.blocked:
        # the worst wait, not the first row: the list is grouped by folder now,
        # so position no longer implies urgency (see _by_folder)
        top = max(view.blocked, key=lambda r: r.wait_s or 0)
        wait = format_duration(top.wait_s or 0)
        prefix = "≥" if top.wait_uncertain else ""
        parts.append(f"⏸ {len(view.blocked)} · {prefix}{wait}")
        if (top.wait_s or 0) >= ESCALATION_ALARM_S:
            severity = "alarm"
        elif (top.wait_s or 0) >= ESCALATION_WARN_S:
            severity = "warn"
        else:
            severity = "info"
    if view.overdue:
        parts.append(f"⏱ {len(view.overdue)}")
        if severity == "ok":
            severity = "warn"
    if not parts:
        if view.working:
            parts.append(f"▶ {len(view.working)}")
            severity = "info"
        else:
            parts.append("✓")
    if view.has_error:
        parts.append("⚠")
        if severity in ("ok", "info"):
            severity = "warn"
    if view.has_stale:
        # Without this the badge reports on data it has no reason to believe —
        # a "✓" that means "nothing was waiting on you the last time I could
        # look", rendered identically to "nothing is waiting on you".
        parts.append("⏳")
        if severity in ("ok", "info"):
            severity = "warn"
    if any(s.stuck for s in view.services):
        parts.append("⚙⚠")
        if severity in ("ok", "info"):
            severity = "warn"
    if view.notify_muted:
        # a muted panel must not read as a quiet one (SPEC §7.3)
        parts.append("🔕")
    return "  ".join(parts), severity


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        rem = minutes % 60
        return f"{hours}h{rem:02d}" if rem and hours < 10 else f"{hours}h"
    return f"{hours // 24}d"
