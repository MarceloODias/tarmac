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
from ..derive import Row, View, badge, build_view, format_duration
from ..strings import tr
from .rows import layout_for, row_renderable

STATE_STYLE = {"alarm": "bold red", "warn": "yellow", "info": "cyan", "ok": "green"}


def render_view(config: Config, view: View, width: int = 100) -> Group:
    """`render --once`, laid out by the SAME row renderer as the live panel.

    It used to have a second, similar-but-different table here: a layout fixed
    in one place stayed broken in the other, and the one on screen was the one
    I was not looking at.
    """
    locale = config.settings.locale
    text, severity = badge(view)
    parts: list = [Text(text, style=STATE_STYLE.get(severity, ""))]

    listed = (view.overdue + view.blocked + view.working + view.scheduled
              + view.other)
    # -4: the panel border and its padding eat into the row's width
    layout = layout_for(listed, max(40, width - 4))

    def add(title_key: str, rows: list[Row]) -> None:
        if not rows:
            return
        body = Group(*[row_renderable(r, locale, layout) for r in rows])
        parts.append(Panel(body, title=tr(locale, title_key), title_align="left",
                           width=width))

    add("for_today", view.overdue)
    add("needs_you", view.blocked)
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
        elif tl.state == "stale":
            # no error to report and the data is old anyway: say so where the
            # eye already goes for target health, not only in the dim timestamp
            footer.append(
                f"⏳ {tl.label} · {tr(locale, 'stale_for', ago=format_duration(tl.age_s or 0))}   ",
                style="yellow",
            )
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
            live.update(render_view(config, view, console.width), refresh=True)
            try:
                time.sleep(interval_s)
            except KeyboardInterrupt:
                break
