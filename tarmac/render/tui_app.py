"""Interactive TUI (textual) — the primary renderer (SPEC §8.0, DECISIONS #7
revised by Marcelo: interactive now).

Keys: ↑/↓ navigate · Enter open-or-focus the session's iTerm tab · c copy
resume · p pin · m reminder · a defer · l logs · x resolve · S stop ·
u refresh · q quit.
"""

from __future__ import annotations

import time
from dataclasses import replace

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from .. import actions
from ..collect import collect, collect_if_stale
from ..config import Config, Target
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
from ..strings import set_locale, tr

# badge renders as a full-width status bar; background = severity
BADGE_STYLE = {
    "alarm": "bold white on #c01c28",
    "warn": "bold black on #e5a50a",
    "info": "bold black on #62a0ea",
    "ok": "bold black on #57e389",
}
SECTION_STYLE = {
    "for_today": "bold #f8e45c",
    "needs_you": "bold #ff7b63",
    "working": "bold #8ff0a4",
    "scheduled": "bold #99c1f1",
    "services": "bold #dc8add",
    "idle": "bold #b0b0b0",
    "done": "bold #b0b0b0",
}
CWD_STYLE = "#8c8c8c"


def _row_text(row: Row, locale: str, name_width: int = 34) -> Text:
    """One session line. name_width flexes with the terminal: the default
    assumption is a fullscreen window on a dedicated monitor (SPEC §8.0), so
    wide terminals get wide, untruncated names."""
    icon = {"blocked": "⏸", "working": "▶", "idle": "·", "task": "☐"}.get(
        row.eff_state, "⏱" if row.overdue or row.due_at else "·")
    icon_style = {"blocked": "bold #ff7b63", "working": "bold #8ff0a4",
                  "idle": CWD_STYLE, "task": "bold #dc8add"}.get(
        row.eff_state, "bold #f8e45c")
    if row.wait_s is not None and row.wait_s >= ESCALATION_ALARM_S:
        icon_style = "bold #ff5050"
    text = Text()
    text.append(f"{icon} ", style=icon_style)
    text.append(f"{row.display_name[:name_width]:<{name_width}}", style="bold white")
    if row.never_named:
        text.append("✎", style="#f8e45c")
    else:
        text.append(" ")
    if row.pinned:
        text.append("★", style="#f8e45c")
    else:
        text.append(" ")
    text.append(f" {row.target_label[:8]:<8}", style="bold #62a0ea")

    wait = ""
    if row.wait_s is not None:
        prefix = "≥" if row.wait_uncertain else ""
        marker = "▲" if row.wait_s >= ESCALATION_ALARM_S else ""
        wait = f"{prefix}{format_duration(row.wait_s)}{marker}"
    style = ""
    if row.wait_s is not None:
        style = "bold #57e389"
        if row.wait_s >= ESCALATION_ALARM_S:
            style = "bold white on #c01c28"
        elif row.wait_s >= ESCALATION_WARN_S:
            style = "bold black on #e5a50a"
    text.append(f" {wait:>7}", style=style)

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
    if parts:
        text.append("  " + "  ".join(p for p in parts if p), style="#deddda")
    if row.cwd:
        text.append(f"\n    {row.cwd}", style=CWD_STYLE)
    return text


class TextPrompt(ModalScreen[str | None]):
    """One-line input modal (reminder / defer / next step)."""

    CSS = """
    TextPrompt { align: center middle; }
    #box { width: 60; height: auto; border: round $accent; padding: 1 2; }
    """

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label(self._title)
            yield Input(placeholder=self._placeholder)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def key_escape(self) -> None:
        self.dismiss(None)


class ConfirmPrompt(ModalScreen[bool]):
    CSS = TextPrompt.CSS.replace("TextPrompt", "ConfirmPrompt")

    def __init__(self, question: str, hint: str) -> None:
        super().__init__()
        self._question = question
        self._hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label(self._question)
            yield Label(self._hint)

    def key_enter(self) -> None:
        self.dismiss(True)

    def key_escape(self) -> None:
        self.dismiss(False)


class FolderPick(ModalScreen[tuple[str, str] | None]):
    """Ask which folder a task should start in, most-used first."""

    CSS = """
    FolderPick { align: center middle; }
    #box { width: 90; height: auto; max-height: 80%; border: round $accent; padding: 1 2; }
    """

    def __init__(self, title: str, candidates: list[tuple[str, str, str]]) -> None:
        # candidates: (target_id, target_label, cwd)
        super().__init__()
        self._title = title
        self._candidates = candidates

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label(self._title)
            ol = OptionList()
            for target_id, target_label, cwd in self._candidates:
                ol.add_option(Option(
                    Text.assemble((f"{target_label:<8}", "bold #62a0ea"), f" {cwd}"),
                    id=f"{target_id}|{cwd}",
                ))
            yield ol

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        target_id, cwd = event.option.id.split("|", 1)
        self.dismiss((target_id, cwd))

    def key_escape(self) -> None:
        self.dismiss(None)


class TarmacApp(App):
    TITLE = "tarmac"
    CSS = """
    #badge { padding: 0 1; height: 1; }
    #sessions { border: none; height: 1fr; padding: 0 1; }
    #sessions > .option-list--option-highlighted {
        background: #1c4d8f;
        text-style: bold;
    }
    #sessions:focus > .option-list--option-highlighted {
        background: #1a5fb4;
    }
    """
    # descriptions are placeholders: __init__ rewrites them in settings.locale
    BINDINGS = [
        Binding("enter", "open", "bind_open", priority=False),
        Binding("t", "new_task", "bind_task"),
        Binding("c", "resume_tab", "bind_resume_tab"),
        Binding("C", "copy_resume", "bind_copy_resume"),
        Binding("p", "pin", "bind_pin"),
        Binding("m", "remember", "bind_remember"),
        Binding("a", "defer", "bind_defer"),
        Binding("n", "next_step", "bind_next_step"),
        Binding("l", "logs", "bind_logs"),
        Binding("x", "resolve", "bind_resolve"),
        Binding("S", "stop", "bind_stop"),
        Binding("R", "remove", "bind_remove"),
        Binding("u", "refresh", "bind_refresh"),
        Binding("q", "quit", "bind_quit"),
    ]

    def __init__(self, config: Config, interval_s: int = 60) -> None:
        super().__init__()
        self.config = config
        self.interval_s = interval_s
        self.conn = connect()
        self.rows: dict[str, Row] = {}
        self.view: View | None = None
        set_locale(config.settings.locale)  # actions.py raises/returns text too
        self._localize_bindings()

    def _t(self, key: str, **kwargs) -> str:
        return tr(self.config.settings.locale, key, **kwargs)

    def _localize_bindings(self) -> None:
        """Footer labels in settings.locale.

        BINDINGS is a class attribute, evaluated at import time — before any
        config exists — so it carries string KEYS and the per-instance
        BindingsMap copy gets the translated descriptions here.

        New lists on purpose: BindingsMap.copy() is shallow, so the instance
        shares the class-level lists and editing one in place would rewrite
        the class BINDINGS for every later instance."""
        self._bindings.key_to_bindings = {
            key: [replace(b, description=self._t(b.description))
                  if b.description.startswith("bind_") else b
                  for b in bindings]
            for key, bindings in self._bindings.key_to_bindings.items()
        }

    def compose(self) -> ComposeResult:
        yield Static("", id="badge")
        yield OptionList(id="sessions")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()
        self.set_interval(self.interval_s, self.refresh_data)

    # ---------- data ----------

    def refresh_data(self) -> None:
        # own group: an exclusive refresh must never cancel an action the
        # user just fired (it silently killed opens, stops and removals)
        self.run_worker(self._collect_and_render, thread=True, exclusive=True,
                        group='collect')

    def _collect_and_render(self) -> None:
        conn = connect()  # thread-local connection
        try:
            collect_if_stale(self.config, conn)
        except Exception as e:
            self.call_from_thread(self.notify, self._t("collect_failed", err=e),
                                  severity="error")
        view = build_view(self.config, conn)
        self.call_from_thread(self._render, view)

    def on_resize(self, event) -> None:
        if self.view is not None:
            self._render(self.view)

    def _render(self, view: View) -> None:
        self.view = view
        locale = self.config.settings.locale
        # name column flexes: fullscreen on a big monitor shows full names
        name_width = max(34, min(80, (self.size.width or 100) - 60))
        text, severity = badge(view)
        badge_widget = self.query_one("#badge", Static)
        extra = ""
        if view.last_collect_ms:
            ago = format_duration(int(time.time() - view.last_collect_ms / 1000))
            extra = f"   [dim]{tr(locale, 'updated_ago', ago=ago)}[/dim]"
        for tl in view.targets:
            if tl.state == "offline":
                extra += f"   [dim]{tl.label} {tr(locale, 'offline_for', ago=format_duration(tl.age_s or 0))}[/dim]"
            elif tl.state == "error":
                extra += f"   [red]⚠ {tl.label}[/red]"
        badge_widget.update(
            f"[{BADGE_STYLE.get(severity, '')}]  {text}  [/]" + extra
        )

        options: list[Option | None] = []
        self.rows = {}

        def add_section(title_key: str, rows: list[Row]) -> None:
            if not rows:
                return
            header = Text()
            header.append("▍", style=SECTION_STYLE.get(title_key, "bold"))
            header.append(f"{tr(locale, title_key)} ",
                          style=SECTION_STYLE.get(title_key, "bold"))
            header.append("─" * 40, style=CWD_STYLE)
            options.append(Option(header, disabled=True))
            for row in rows:
                key = f"{row.target_id}|{row.session_id}"
                self.rows[key] = row
                options.append(Option(_row_text(row, locale, name_width), id=key))
            options.append(None)  # separator

        add_section("for_today", view.overdue)
        add_section("needs_you", view.blocked)
        add_section("working", view.working)
        add_section("scheduled", view.scheduled)

        if view.services:
            header = Text()
            header.append("▍", style=SECTION_STYLE["services"])
            header.append(f"{tr(locale, 'services')} ", style=SECTION_STYLE["services"])
            header.append("─" * 40, style=CWD_STYLE)
            options.append(Option(header, disabled=True))
            for s in view.services:
                line = f"⚙ {s.label}  {s.target_label}  {s.active} {tr(locale, 'active')}"
                if s.stuck:
                    line += f" · {s.stuck} {tr(locale, 'stuck')} {format_duration(s.stuck_oldest_s)} ⚠"
                options.append(Option(
                    Text(line, style="bold #f8e45c" if s.stuck else "#deddda"),
                    disabled=True))
            options.append(None)

        add_section("idle", view.other)
        # DONE is not rendered: a finished session is not work, and the tail of
        # them pushed the live sections off screen. build_view still fills
        # view.done — the CLI resolves session ids through it.

        session_list = self.query_one("#sessions", OptionList)
        highlighted = session_list.highlighted
        session_list.clear_options()
        session_list.add_options(options)
        if self.rows:
            session_list.highlighted = min(
                highlighted if highlighted is not None else 1,
                session_list.option_count - 1,
            )

    # ---------- helpers ----------

    def _current(self) -> tuple[Target, Row] | None:
        session_list = self.query_one("#sessions", OptionList)
        if session_list.highlighted is None:
            return None
        option = session_list.get_option_at_index(session_list.highlighted)
        if option is None or option.id is None:
            return None
        row = self.rows.get(option.id)
        if row is None:
            return None
        target = self.config.target(row.target_id)
        if target is None:
            if row.kind == "task":
                # unresolved task: folder (and target) get picked on open
                return Target(id="", transport="local"), row
            return None
        return target, row

    def _run_bg(self, fn, done_msg: str | None = None) -> None:
        def work():
            try:
                result = fn()
            except Exception as e:
                self.call_from_thread(self.notify, str(e), severity="error")
                return
            msg = done_msg or (str(result) if result else None)
            if msg:
                self.call_from_thread(self.notify, msg)
        self.run_worker(work, thread=True, group='actions')

    # ---------- actions ----------

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_open()

    def action_open(self) -> None:
        cur = self._current()
        if cur is None:
            return
        target, row = cur
        if row.kind == "task":
            self._open_task(row)
            return
        # sqlite connections are thread-bound: each worker opens its own
        self._run_bg(lambda: actions.open_or_focus(connect(), target, row))

    # ---------- standalone tasks ----------

    def action_new_task(self) -> None:
        def handle(value: str | None) -> None:
            if not value:
                return
            from ..tasks import add_task, cwd_candidates, infer_folder
            task_id = add_task(self.conn, value)
            guess = infer_folder(value, cwd_candidates(self.conn))
            if guess:
                from ..tasks import set_task_folder
                set_task_folder(self.conn, task_id, guess.target_id, guess.cwd)
                self.notify(self._t("task_created_folder", cwd=guess.cwd))
            else:
                self.notify(self._t("task_created_ask"))
            self.refresh_data()

        self.push_screen(
            TextPrompt(self._t("new_task"), self._t("new_task_hint")),
            handle,
        )

    def _open_task(self, row: Row) -> None:
        from ..tasks import cwd_candidates, infer_folder, mark_opened, set_task_folder
        task_id = int(row.session_id.split(":", 1)[1])
        text = row.display_name

        def launch(target_id: str, cwd: str) -> None:
            target = self.config.target(target_id)
            if target is None:
                self.notify(self._t("unknown_target", target=target_id),
                            severity="error")
                return
            set_task_folder(self.conn, task_id, target_id, cwd)
            mark_opened(self.conn, task_id)
            self._run_bg(lambda: actions.open_task(connect(), target, task_id, cwd, text))

        if row.target_id and row.cwd:
            launch(row.target_id, row.cwd)
            return
        guess = infer_folder(text, cwd_candidates(self.conn))
        if guess:
            launch(guess.target_id, guess.cwd)
            return
        labels = {t.id: t.label for t in self.config.enabled_targets()}
        candidates = [(c.target_id, labels.get(c.target_id, c.target_id), c.cwd)
                      for c in cwd_candidates(self.conn)]
        if not candidates:
            self.notify(self._t("no_folder_history"), severity="warning")
            return

        def picked(choice: tuple[str, str] | None) -> None:
            if choice:
                launch(*choice)

        self.push_screen(
            FolderPick(self._t("folder_pick_title"), candidates), picked)

    def action_resume_tab(self) -> None:
        """`c`: open a tab already inside this session's conversation.

        Finished/vanished sessions get `claude --resume`; live ones are
        ATTACHED instead — the CLI refuses --resume while the session runs as
        a bg agent (FINDINGS E), so attach is the only verb that works there."""
        cur = self._current()
        if cur is None:
            return
        target, row = cur
        if row.kind == "task":
            self._open_task(row)
            return
        if actions.session_is_live(row):
            self.notify(self._t("attaching_live"))
            self._run_bg(lambda: actions.open_or_focus(connect(), target, row))
            return
        try:
            cmd = actions.resume_command(target, row)
        except ValueError as e:
            self.notify(str(e), severity="error")
            return
        self._run_bg(lambda: actions.open_or_focus(connect(), target, row, command=cmd))

    def action_copy_resume(self) -> None:
        cur = self._current()
        if cur is None:
            return
        target, row = cur
        try:
            cmd = actions.resume_command(target, row)
        except ValueError as e:
            self.notify(str(e), severity="error")
            return
        actions.copy_to_clipboard(cmd)
        self.notify(self._t("copied", cmd=cmd))

    def action_pin(self) -> None:
        cur = self._current()
        if cur is None:
            return
        _, row = cur
        from .. import db as dbm
        meta = dbm.get_meta(self.conn, row.target_id, row.session_id)
        new = 0 if (meta and meta["pinned"]) else 1
        dbm.upsert_meta(self.conn, row.target_id, row.session_id, pinned=new)
        self.conn.commit()
        self.refresh_data()

    def _prompt_due(self, title: str, hide: int) -> None:
        cur = self._current()
        if cur is None:
            return
        _, row = cur

        def handle(value: str | None) -> None:
            if not value:
                return
            from .. import db as dbm
            from ..dates import DateParseError, human_confirmation, parse_with_fallback
            try:
                due = parse_with_fallback(
                    value,
                    default_hour=self.config.settings.default_hour,
                    end_of_day_hour=self.config.settings.end_of_day_hour,
                )
            except DateParseError as e:
                self.notify(str(e), severity="error")  # keep field open? re-prompt
                self._prompt_due(title, hide)
                return
            if row.kind == "task":
                task_id = int(row.session_id.split(":", 1)[1])
                self.conn.execute(
                    "UPDATE tasks SET due_at = ?, due_label = ? WHERE id = ?",
                    (int(due.timestamp() * 1000), value, task_id))
            else:
                dbm.upsert_meta(
                    self.conn, row.target_id, row.session_id,
                    due_at=int(due.timestamp() * 1000), due_label=value,
                    hide_until_due=hide, resolved_at=None,
                )
            self.conn.commit()
            self.notify(human_confirmation(due, locale=self.config.settings.locale))
            self.refresh_data()

        self.push_screen(
            TextPrompt(title, self._t("due_hint")), handle,
        )

    def action_remember(self) -> None:
        self._prompt_due(tr(self.config.settings.locale, "remind_in"), hide=0)

    def action_defer(self) -> None:
        self._prompt_due(tr(self.config.settings.locale, "defer_until"), hide=1)

    def action_next_step(self) -> None:
        cur = self._current()
        if cur is None:
            return
        _, row = cur

        def handle(value: str | None) -> None:
            if value is None:
                return
            from .. import db as dbm
            dbm.upsert_meta(self.conn, row.target_id, row.session_id,
                            next_step=value or None, next_step_origin="manual")
            self.conn.commit()
            self.refresh_data()

        self.push_screen(
            TextPrompt(tr(self.config.settings.locale, "set_next_step")), handle,
        )

    def action_logs(self) -> None:
        """`l`: open the logs in a real terminal window.

        `claude logs` replays a full-screen TUI (cursor-positioning escapes,
        74KB of them for a busy session) — rendering that inside a widget is
        both unreadable and a crash; a terminal is exactly where it belongs."""
        cur = self._current()
        if cur is None:
            return
        target, row = cur
        try:
            cmd = actions.logs_command(target, row)
        except ValueError as e:
            self.notify(str(e), severity="warning")
            return
        self._run_bg(lambda: actions.create_window(cmd)[1])

    def action_resolve(self) -> None:
        cur = self._current()
        if cur is None:
            return
        _, row = cur
        if row.kind == "task":
            from ..tasks import resolve_task
            resolve_task(self.conn, int(row.session_id.split(":", 1)[1]))
            self.refresh_data()
            return
        if not row.overdue:
            # `x` clears a reminder I set; it cannot clear a session that is
            # genuinely blocked — only answering it can. Say so, never no-op.
            if row.eff_state == "blocked":
                self.notify(self._t("really_blocked"), severity="warning")
            else:
                self.notify(self._t("nothing_to_resolve"), severity="warning")
            return
        from .. import db as dbm
        dbm.upsert_meta(self.conn, row.target_id, row.session_id,
                        resolved_at=dbm.now_ms())
        self.conn.commit()
        self.refresh_data()

    def action_stop(self) -> None:
        cur = self._current()
        if cur is None:
            return
        target, row = cur
        if not row.short_id:
            self.notify(self._t("no_short_id_stop"), severity="warning")
            return

        def handle(confirmed: bool) -> None:
            if not confirmed:
                return
            def work():
                proc = actions.remote_claude(target, "stop", row.short_id)
                msg = (proc.stdout or proc.stderr).strip()
                conn = connect()
                collect(self.config, conn, force=True)
                self.call_from_thread(self.notify, msg or self._t("stopped"))
                self.call_from_thread(self.refresh_data)
            self.run_worker(work, thread=True, group='actions')

        self.push_screen(
            ConfirmPrompt(self._t("confirm_stop", name=row.display_name),
                          self._t("confirm_hint")), handle)

    def action_remove(self) -> None:
        """`R`: drop a session from the agent view (`claude rm`).

        The case that forced this: a session listed as blocked for 44 days that
        can never be attached — it was stopped before its first response, so it
        has no transcript. It is not work waiting on you, it is a dead row
        holding the badge hostage."""
        cur = self._current()
        if cur is None:
            return
        target, row = cur
        if row.kind == "task":
            self.notify(self._t("task_use_x"), severity="warning")
            return
        if not row.short_id:
            self.notify(self._t("interactive_no_remove"), severity="warning")
            return

        def handle(confirmed: bool) -> None:
            if not confirmed:
                return

            def work():
                proc = actions.remote_claude(target, "rm", row.short_id)
                msg = (proc.stdout or proc.stderr).strip()
                collect(self.config, connect(), force=True)
                self.call_from_thread(self.notify, msg or self._t("removed"))
                self.call_from_thread(self.refresh_data)
            self.run_worker(work, thread=True, group='actions')

        self.push_screen(ConfirmPrompt(
            self._t("confirm_remove", name=row.display_name[:40],
                    short_id=row.short_id),
            self._t("confirm_hint"),
        ), handle)

    def action_refresh(self) -> None:
        def work():
            conn = connect()
            collect(self.config, conn, force=True)
            view = build_view(self.config, conn)
            self.call_from_thread(self._render, view)
        self.run_worker(work, thread=True, group='actions')


def run_tui(config: Config, interval_s: int = 60) -> None:
    TarmacApp(config, interval_s).run()
