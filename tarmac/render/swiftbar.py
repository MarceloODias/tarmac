"""SwiftBar plugin output (SPEC §8.1, §8.2) — laptop-mode fallback renderer.

Prints the menu-bar title, then the dropdown. Each session line opens the
session on click; submenus expose the per-line actions by shelling back into
the `tarmac` CLI.
"""

from __future__ import annotations

import sys

from ..config import Config
from ..derive import (
    ESCALATION_ALARM_S,
    ESCALATION_WARN_S,
    Row,
    View,
    badge,
    format_duration,
)
from ..strings import tr

COLORS = {
    "ok": "",
    "info": "#4a90d9",
    "warn": "#e5a50a",
    "alarm": "#e01b24",
    "gray": "#77767b",
}


def _tarmac_cmd(*args: str) -> str:
    exe = sys.argv[0]
    params = " ".join(
        f"param{i}={a}" for i, a in enumerate(args, start=1)
    )
    return f"bash={exe} {params} terminal=false refresh=true"


def _wait_str(row: Row) -> str:
    if row.wait_s is None:
        return ""
    prefix = "≥" if row.wait_uncertain else ""
    marker = " ▲" if row.wait_s >= ESCALATION_ALARM_S else ""
    return f"{prefix}{format_duration(row.wait_s)}{marker}"


def _line_color(row: Row) -> str:
    if row.wait_s is not None:
        if row.wait_s >= ESCALATION_ALARM_S:
            return COLORS["alarm"]
        if row.wait_s >= ESCALATION_WARN_S:
            return COLORS["warn"]
    return ""


def _session_line(row: Row, icon: str, extra: str, locale: str) -> list[str]:
    checklist = f" [{row.checklist[0]}/{row.checklist[1]}]" if row.checklist else ""
    pencil = " ✎" if row.never_named else ""
    perm = " ⚠" if row.permission_prompt else ""
    stale = " (stale)" if row.stale else ""
    label = f"{icon} {row.display_name}{pencil}  {row.target_label}  {extra}{perm}{checklist}{stale}"
    color = _line_color(row)
    attrs = f" color={color}" if color else ""
    lines = [f"{label} | {_tarmac_cmd('open', row.target_id, row.session_id)}{attrs}"]
    sub = [
        (tr(locale, "open"), ("open", row.target_id, row.session_id)),
        (tr(locale, "logs"), ("logs", row.target_id, row.session_id)),
        (tr(locale, "copy_resume"), ("copy-resume", row.target_id, row.session_id)),
        (tr(locale, "pin") if not row.pinned else tr(locale, "unpin"),
         ("pin", row.target_id, row.session_id)),
        (tr(locale, "stop"), ("stop", row.target_id, row.session_id)),
    ]
    if row.overdue:
        sub.insert(0, (tr(locale, "resolve"), ("resolve", row.target_id, row.session_id)))
    for shortcut in ("2h", "amanhã", "segunda"):
        sub.append((f"{tr(locale, 'remind_in')} {shortcut}",
                    ("remember", row.target_id, row.session_id, shortcut)))
    for title, args in sub:
        lines.append(f"-- {title} | {_tarmac_cmd(*args)}")
    if row.next_step:
        lines.append(f"-- → {row.next_step} | disabled=true")
    if row.cwd:
        lines.append(f"-- {row.cwd} | disabled=true")
    return lines


def render_swiftbar(config: Config, view: View) -> str:
    locale = config.settings.locale
    out: list[str] = []

    text, severity = badge(view)
    color = COLORS.get(severity, "")
    out.append(f"{text} | {f'color={color} ' if color else ''}font=Menlo")
    out.append("---")

    def section(title: str, rows: list[str]) -> None:
        if not rows:
            return
        out.append(f"{title} | disabled=true")
        out.extend(rows)
        out.append("---")

    section(tr(locale, "for_today"), [
        line
        for row in view.overdue
        for line in _session_line(row, "⏱", _overdue_extra(row, locale), locale)
    ])
    section(tr(locale, "needs_you"), [
        line
        for row in view.blocked
        for line in _session_line(row, "⏸", f"{row.waiting_for or 'blocked'}   {_wait_str(row)}", locale)
    ])
    section(tr(locale, "working"), [
        line
        for row in view.working
        for line in _session_line(row, "▶", "", locale)
    ])
    section(tr(locale, "scheduled"), [
        line
        for row in view.scheduled
        for line in _session_line(row, "⏱", row.due_label or "", locale)
    ])

    if view.services:
        out.append(f"{tr(locale, 'services')} | disabled=true")
        for s in view.services:
            stuck = ""
            if s.stuck:
                stuck = f" · {s.stuck} {tr(locale, 'stuck')} {format_duration(s.stuck_oldest_s)} ⚠"
            out.append(f"⚙ {s.label}  {s.target_label}  {s.active} {tr(locale, 'active')}{stuck} | disabled=true")
        out.append("---")

    if view.done:
        out.append(f"{tr(locale, 'done')} ({len(view.done)})")
        for row in view.done:
            out.append(f"-- ✓ {row.display_name}  {row.target_label}  {row.eff_state} | "
                       + _tarmac_cmd("copy-resume", row.target_id, row.session_id))
        out.append("---")

    for tl in view.targets:
        if tl.state == "offline":
            ago = format_duration(tl.age_s or 0)
            out.append(
                f"{tl.label} · {tr(locale, 'offline_for', ago=ago)} | "
                f"color={COLORS['gray']} disabled=true"
            )
        elif tl.state == "error":
            out.append(f"⚠ {tl.label}: {(tl.last_error or '')[:80]} | color={COLORS['alarm']} disabled=true")

    ago = "?"
    if view.last_collect_ms:
        import time
        ago = format_duration(int(time.time() - view.last_collect_ms / 1000))
    out.append(f"{tr(locale, 'updated_ago', ago=ago)} · {tr(locale, 'refresh')} | {_tarmac_cmd('collect', '--force')}")
    return "\n".join(out) + "\n"


def _overdue_extra(row: Row, locale: str) -> str:
    import time

    from ..derive import format_duration as fd
    if not row.due_at:
        return ""
    ago = fd(max(0, int(time.time() - row.due_at / 1000)))
    return tr(locale, "overdue_ago", ago=ago)
