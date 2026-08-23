"""One session row, laid out once for every surface (SPEC §8.0).

This module exists because the layout used to be written twice — once for the
interactive panel and once for `render --once` — so a layout checked in one
place could still be wrong in the window actually on screen.

Columns, left to right: state icon, name (carries ✎★), spacer, wait, account,
target, spacer, pending. The pending column is anchored to the right edge and never
gets more than PENDING_MAX_SHARE of the width: the name and the path are what
the panel is read for.

Why wait and account come BEFORE the target: they are the columns a given row
often does not use, and every column is a fixed width. Sitting between the
target and the pending, their empty cells opened a blank band in the middle of
the row; sitting before it, their slack merges with the name column's.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..derive import (
    ESCALATION_ALARM_S,
    ESCALATION_WARN_S,
    Row,
    account_width,
    format_duration,
)
from ..strings import tr

CWD_STYLE = "#8c8c8c"

def name_column_width(rows: list[Row], floor: int = 18, cap: int = 44) -> int:
    """As wide as the longest name on screen, not as wide as the window.

    A fixed 80 pushed target/account/pending far to the right on a big monitor.
    Feed this EVERY row that will be rendered: sized from the top sections only,
    it fell back to the floor and truncated every idle name below.
    """
    longest = max((len(r.display_name) for r in rows), default=0)
    return max(floor, min(cap, longest))


# The pending text is context, not the subject: the session name and its path
# are what I look for first, so pending never gets more than this share.
PENDING_MAX_SHARE = 0.4


def pending_column_width(total_width: int) -> int:
    return max(24, int((total_width or 100) * PENDING_MAX_SHARE))


WAIT_WIDTH = 7


@dataclass(frozen=True)
class Layout:
    """Every column width for one render, derived from the rows and the window.

    One object instead of five arguments: the widths have to agree with each
    other, and they used to be recomputed (differently) per surface.
    """

    name: int
    account: int
    target: int
    pending: int
    show_wait: bool

    left_gap: int = 0
    right_gap: int = 0

    def fixed_columns(self) -> list[int]:
        """Every column except the two gaps, left to right."""
        cols = [1, self.name]                      # state icon, name
        if self.show_wait:
            cols.append(WAIT_WIDTH)
        if self.account:
            cols.append(self.account)
        cols += [self.target, self.pending]
        return cols


# Nudge for the target/account block: dead centre of the gap read a touch too
# far left, so Marcelo asked for five columns to the right. Not derived from
# anything — it is a taste correction, and it belongs in one named place.
META_SHIFT = 5


def layout_for(rows: list[Row], total_width: int) -> Layout:
    """The layout for a window of `total_width` columns showing `rows`.

    The two gaps are explicit widths rather than `ratio=1` spacers: a ratio
    splits the slack evenly and cannot be biased by a fixed number of columns.
    They are computed from the exact column list, because being one column off
    stops the pending from sitting flush against the right edge.
    """
    width = total_width or 100
    account = account_width(rows)
    target = min(8, max((len(r.target_label) for r in rows), default=3)) or 3
    pending = pending_column_width(width)
    show_wait = any(r.wait_s is not None for r in rows)

    def slack_for(name: int) -> tuple[list[int], int]:
        cols = Layout(name, account, target, pending, show_wait).fixed_columns()
        # with both gaps present there is one padding per gap between columns
        return cols, width - sum(cols) - (len(cols) + 1)

    name = max(12, min(name_column_width(rows), name_column_width(rows)
                       + slack_for(name_column_width(rows))[1]))
    slack = max(0, slack_for(name)[1])
    left = min(slack, slack // 2 + META_SHIFT)
    return Layout(
        name=name, account=account, target=target, pending=pending,
        show_wait=show_wait, left_gap=left, right_gap=slack - left,
    )


def pending_text(row: Row, locale: str) -> Text:
    """Everything that is waiting on me, as one cell of its own column."""
    parts = []
    if row.overdue and row.due_at:
        parts.append(tr(locale, "overdue_ago",
                        ago=format_duration(max(0, int(time.time() - row.due_at / 1000)))))
    elif row.due_at:
        parts.append(row.due_label or "")
    if row.eff_state == "blocked":
        parts.append("⚠ permission prompt" if row.permission_prompt
                      else (row.waiting_for or "blocked"))
        if row.kind == "background" and row.pid is None:
            # no live worker: attach may fail ("no saved transcript"). Say the
            # fact, not a guess — R removes it from the list.
            parts.append(tr(locale, "no_process"))
    if row.next_step:
        parts.append(f"→ {row.next_step}")
    if row.checklist:
        parts.append(f"[{row.checklist[0]}/{row.checklist[1]}]")
    if row.stale:
        parts.append("(stale)")
    return Text("  ".join(p for p in parts if p), style="#deddda")


def row_renderable(row: Row, locale: str, layout: Layout):
    """One session row as a grid.

    A long pending text wraps INSIDE its own column instead of continuing at the
    left edge of the window, and the target/account columns are centred so they
    read as columns down the list.
    """
    icon = {"blocked": "⏸", "working": "▶", "idle": "·", "task": "☐"}.get(
        row.eff_state, "⏱" if row.overdue or row.due_at else "·")
    icon_style = {"blocked": "bold #ff7b63", "working": "bold #8ff0a4",
                  "idle": CWD_STYLE, "task": "bold #dc8add"}.get(
        row.eff_state, "bold #f8e45c")
    if row.wait_s is not None and row.wait_s >= ESCALATION_ALARM_S:
        icon_style = "bold #ff5050"

    wait = ""
    wait_style = ""
    if row.wait_s is not None:
        prefix = "≥" if row.wait_uncertain else ""
        marker = "▲" if row.wait_s >= ESCALATION_ALARM_S else ""
        wait = f"{prefix}{format_duration(row.wait_s)}{marker}"
        wait_style = "bold #57e389"
        if row.wait_s >= ESCALATION_ALARM_S:
            wait_style = "bold white on #c01c28"
        elif row.wait_s >= ESCALATION_WARN_S:
            wait_style = "bold black on #e5a50a"

    # the markers belong to the name: as their own column they floated in the
    # middle of the row once the name column became the elastic one
    name = Text(row.display_name, style="bold white")
    if row.never_named:
        name.append(" ✎", style="#f8e45c")
    if row.pinned:
        name.append(" ★", style="#f8e45c")

    # Every column has a fixed width except the two spacers, which split the
    # slack evenly: the metadata block lands centred between the names and the
    # pending, and — because the widths are fixed — `Mac` sits in the very same
    # column whether or not that row also shows an account.
    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(width=1, no_wrap=True)                        # state icon
    grid.add_column(width=layout.name, no_wrap=True,
                    overflow="ellipsis")                          # name + ✎ ★
    if layout.left_gap:
        grid.add_column(width=layout.left_gap)                     # left gap
    if layout.show_wait:
        grid.add_column(width=WAIT_WIDTH, no_wrap=True, justify="right")
    if layout.account:
        grid.add_column(width=layout.account, no_wrap=True, justify="center")
    grid.add_column(width=layout.target, no_wrap=True, justify="center")
    if layout.right_gap:
        grid.add_column(width=layout.right_gap)                    # right gap
    grid.add_column(width=layout.pending, overflow="fold")

    cells = [Text(icon, style=icon_style), name]
    if layout.left_gap:
        cells.append(Text(""))
    if layout.show_wait:
        cells.append(Text(wait, style=wait_style))
    if layout.account:
        cells.append(Text(row.target_account[:layout.account], style="#c0bfbc"))
    cells.append(Text(row.target_label[:layout.target], style="bold #62a0ea"))
    if layout.right_gap:
        cells.append(Text(""))
    cells.append(pending_text(row, locale))
    grid.add_row(*cells)

    if not row.cwd:
        return grid
    return Group(grid, Text(f"    {row.cwd}", style=CWD_STYLE,
                            no_wrap=True, overflow="ellipsis"))
