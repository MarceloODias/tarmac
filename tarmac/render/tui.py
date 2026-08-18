"""TUI renderer — the PRIMARY surface (SPEC §8.0): a dedicated window on the
second monitor, refreshing every 60s, showing what the menu hides (wait time,
next_step, checklist progress and cwd all at once).

v1 is display-only, built on rich.Live; actions go through the CLI or the
SwiftBar menu (see DECISIONS.md #2).
"""

from __future__ import annotations

import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..collect import collect_if_stale
from ..config import Config
from ..db import connect
from ..derive import (
    ESCALATION_ALARM_S,
    ESCALATION_WARN_S,
    Row,
    View,
    badge,
    build_view,
    format_duration,
)
from ..strings import tr

STATE_STYLE = {"alarm": "bold red", "warn": "yellow", "info": "cyan", "ok": "green"}


def _wait_text(row: Row) -> Text:
    if row.wait_s is None:
        return Text("")
    style = "green"
    if row.wait_s >= ESCALATION_ALARM_S:
        style = "bold red blink"
    elif row.wait_s >= ESCALATION_WARN_S:
        style = "yellow"
    prefix = "≥" if row.wait_uncertain else ""
    marker = " ▲" if row.wait_s >= ESCALATION_ALARM_S else ""
    return Text(f"{prefix}{format_duration(row.wait_s)}{marker}", style=style)


def _rows_table(rows: list[Row], locale: str, show_wait: bool) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(width=2)
    table.add_column(width=30, no_wrap=True)   # name
    table.add_column(width=8, no_wrap=True)    # target
    table.add_column(width=7, no_wrap=True)    # wait
    table.add_column(overflow="ellipsis")      # detail flexes/truncates last
    for row in rows:
        icon = {"blocked": "⏸", "working": "▶", "idle": "·"}.get(row.eff_state, "⏱" if row.overdue or row.due_at else "·")
        name = Text(row.display_name)
        if row.never_named:
            name.append(" ✎", style="dim")
        if row.pinned:
            name.append(" ★", style="yellow")
        detail_parts = []
        if row.overdue and row.due_at:
            ago = format_duration(max(0, int(time.time() - row.due_at / 1000)))
            detail_parts.append(tr(locale, "overdue_ago", ago=ago))
        elif row.due_at and not row.overdue:
            detail_parts.append(row.due_label or "")
        if row.eff_state == "blocked":
            if row.permission_prompt:
                detail_parts.append("⚠ permission prompt")  # anomalous under auto mode
            else:
                detail_parts.append(row.waiting_for or "blocked")
        if row.next_step:
            detail_parts.append(f"→ {row.next_step}")
        if row.checklist:
            detail_parts.append(f"[{row.checklist[0]}/{row.checklist[1]}]")
        if row.stale:
            detail_parts.append("(stale)")
        detail = Text("  ".join(p for p in detail_parts if p))
        table.add_row(
            icon, name, Text(row.target_label, style="dim"),
            _wait_text(row) if show_wait else Text(""), detail,
        )
        if row.cwd:
            table.add_row("", Text(row.cwd, style="dim"), "", "", "")
    return table


def render_view(config: Config, view: View) -> Group:
    locale = config.settings.locale
    text, severity = badge(view)
    parts: list = [Text(text, style=STATE_STYLE.get(severity, ""))]

    def add(title_key: str, rows: list[Row], show_wait: bool = False) -> None:
        if rows:
            parts.append(Panel(_rows_table(rows, locale, show_wait),
                               title=tr(locale, title_key), title_align="left"))

    add("for_today", view.overdue)
    add("needs_you", view.blocked, show_wait=True)
    add("working", view.working)
    add("scheduled", view.scheduled)

    if view.services:
        table = Table.grid(padding=(0, 2))
        for s in view.services:
            line = f"⚙ {s.label}  {s.target_label}  {s.active} {tr(locale, 'active')}"
            if s.stuck:
                line += f" · {s.stuck} {tr(locale, 'stuck')} {format_duration(s.stuck_oldest_s)} ⚠"
            table.add_row(Text(line, style="yellow" if s.stuck else "dim"))
        parts.append(Panel(table, title=tr(locale, "services"), title_align="left"))

    if view.other:
        add("idle", view.other)
    # no DONE section here either (see tui_app._render)

    footer = Text()
    for tl in view.targets:
        if tl.state == "offline":
            footer.append(
                f"{tl.label} · {tr(locale, 'offline_for', ago=format_duration(tl.age_s or 0))}   ",
                style="dim",
            )
        elif tl.state == "error":
            footer.append(f"⚠ {tl.label}: {(tl.last_error or '')[:60]}   ", style="red")
    if view.last_collect_ms:
        ago = format_duration(int(time.time() - view.last_collect_ms / 1000))
        footer.append(tr(locale, "updated_ago", ago=ago), style="dim")
    parts.append(footer)
    return Group(*parts)


def run_tui(config: Config, interval_s: int = 60) -> None:
    console = Console()
    conn = connect()
    with Live(console=console, auto_refresh=False, screen=True) as live:
        while True:
            try:
                collect_if_stale(config, conn)
            except Exception as e:  # collection must never kill the panel
                console.log(f"coleta falhou: {e}")
            view = build_view(config, conn)
            live.update(render_view(config, view), refresh=True)
            try:
                time.sleep(interval_s)
            except KeyboardInterrupt:
                break
