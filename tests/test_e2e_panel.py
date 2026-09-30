"""Key sweep: press every binding on every kind of row and require no crash.

This is the test that would have caught the bugs Marcelo hit by hand — the
sqlite cross-thread error on `Enter`, and the markup crash on `l` — because it
drives the real app with the real action code, with only the outside world
(osascript / ssh) faked at the process boundary.
"""

import json
import os

import pytest
from textual.widgets import OptionList

from tarmac import db as dbm
from tarmac.collect import TargetResult, apply_result
from tarmac.config import Config, SessionClassRule, Settings, Target
from tarmac.model import parse_agents_json
from tarmac.render.tui_app import TarmacApp
from tarmac.tasks import add_task

# every binding the panel exposes, minus quit
KEYS = ["enter", "c", "C", "p", "m", "a", "n", "N", "l", "x", "S", "r", "u", "t"]


@pytest.fixture
def panel(tmp_path, monkeypatch):
    """A panel over a DB holding one row of every kind, with the outside
    world (osascript, claude) replaced by fakes on PATH."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "osascript.log"
    (fake_bin / "osascript").write_text(f'''#!/bin/bash
echo "$@" >> "{calls}"
script="$*"
case "$script" in
  *"exists application"*) echo true ;;
  *"create window"*) echo "HANDLE-NEW" ;;
  *"close t"*) echo closed ;;
  *"if id of sess"*) echo missing ;;
  *) echo "" ;;
esac
''')
    (fake_bin / "osascript").chmod(0o755)
    claude_log = tmp_path / "claude.log"
    (fake_bin / "claude").write_text(
        f'#!/bin/bash\necho "$@" >> "{claude_log}"\n'
        'echo "logs [Bash] com \\033[32mcores\\033[0m e [colchetes]"\n')
    (fake_bin / "claude").chmod(0o755)
    (fake_bin / "ssh").write_text('#!/bin/bash\necho "saida remota"\n')
    (fake_bin / "ssh").chmod(0o755)
    (fake_bin / "pbcopy").write_text('#!/bin/bash\ncat > /dev/null\n')
    (fake_bin / "pbcopy").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))

    conn = dbm.connect(tmp_path / "tarmac.db")
    local = Target(id="mac", label="Mac", mine=True, transport="local")
    remote = Target(
        id="ec2", label="EC2", mine=True, transport="ssh", ssh_host="h",
        claude_bin="/bin/claude", expect_intermittent=True,
        session_classes=[SessionClassRule(class_="service", label="chatops",
                                          match_cwd="/svc/**")],
    )
    apply_result(conn, TargetResult(local, parse_agents_json(json.dumps([
        {"id": "blk00001", "sessionId": "blk00001-0000-0000-0000-000000000000",
         "kind": "background", "state": "blocked", "cwd": "/p/a",
         "name": "bloqueada", "startedAt": 1},
        {"id": "wrk00001", "sessionId": "wrk00001-0000-0000-0000-000000000000",
         "kind": "background", "state": "working", "cwd": "/p/b",
         "name": "trabalhando", "startedAt": 2},
        {"sessionId": "idl00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/p/c", "name": "c-9f", "startedAt": 3},
        {"id": "don00001", "sessionId": "don00001-0000-0000-0000-000000000000",
         "kind": "background", "state": "done", "cwd": "/p/d",
         "name": "concluida", "startedAt": 4},
        {"sessionId": "prm00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "waiting", "waitingFor": "permission prompt", "cwd": "/p/e",
         "name": "permissao", "startedAt": 5},
    ]))))
    apply_result(conn, TargetResult(remote, parse_agents_json(json.dumps([
        {"sessionId": "svc00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "busy", "cwd": "/svc/bot", "name": "bot", "startedAt": 6},
        {"id": "rem00001", "sessionId": "rem00001-0000-0000-0000-000000000000",
         "kind": "background", "state": "blocked", "cwd": "/r/a",
         "name": "remota", "startedAt": 7},
    ]))))
    # an overdue reminder and a standalone task, so those row kinds exist too
    dbm.upsert_meta(conn, "mac", "wrk00001", due_at=dbm.now_ms() - 3_600_000,
                    due_label="ontem")
    add_task(conn, "no p, preciso terminar aquilo")
    dbm.kv_set(conn, "last_collect_at", str(dbm.now_ms()))
    conn.commit()

    config = Config(targets=[local, remote], settings=Settings(stale_after_s=10_000))
    return config, tmp_path, calls, claude_log


async def test_every_key_on_every_row_never_crashes(panel):
    config, tmp_path, _, _ = panel
    app = TarmacApp(config)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        ol = app.query_one("#sessions", OptionList)
        selectable = [i for i in range(ol.option_count)
                      if (o := ol.get_option_at_index(i)) is not None and o.id]
        # 6 kinds: vencida, bloqueada, idle, permissão, serviço-remoto, tarefa.
        # A sétima sessão da amostra está 'done' e NÃO deve aparecer.
        assert len(selectable) >= 6, "faltou tipo de linha na amostra"
        listed = {ol.get_option_at_index(i).id for i in selectable}
        assert not any("don00001" in key for key in listed), \
            "sessão concluída voltou para a listagem"

        for index in selectable:
            ol.highlighted = index
            for key in KEYS:
                await pilot.press(key)
                await pilot.pause()
                # dismiss whatever modal the key may have opened
                await pilot.press("escape")
                await pilot.pause()
                assert app.is_running, f"app morreu na tecla {key!r} (linha {index})"
        await app.workers.wait_for_complete()
        assert app.is_running


async def test_open_records_handle_and_logs_render(panel):
    config, tmp_path, calls, claude_log = panel
    app = TarmacApp(config)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        ol = app.query_one("#sessions", OptionList)
        # first selectable row is the overdue one; find the blocked session
        target_index = next(
            i for i in range(ol.option_count)
            if (o := ol.get_option_at_index(i)) is not None
            and o.id == "mac|blk00001"
        )
        ol.highlighted = target_index
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        conn = dbm.connect(tmp_path / "tarmac.db")
        row = conn.execute(
            "SELECT handle FROM terminal_handles WHERE session_id = 'blk00001'"
        ).fetchone()
        assert row is not None and row["handle"] == "HANDLE-NEW"
        assert "create window" in calls.read_text()

        # `claude logs` replays a full-screen TUI: it must go to a terminal
        # window, never into a widget (that was a real crash)
        calls.write_text("")
        await pilot.press("l")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.is_running
        osa = calls.read_text()
        assert "create window" in osa and "logs blk00001" in osa


async def test_service_row_is_not_selectable(panel):
    config, _, _, _ = panel
    app = TarmacApp(config)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        ol = app.query_one("#sessions", OptionList)
        ids = [o.id for i in range(ol.option_count)
               if (o := ol.get_option_at_index(i)) is not None]
        assert "ec2|svc00001" not in ids  # service sessions are counted, not listed


async def test_remove_asks_and_then_calls_claude_rm(panel):
    """R drops a dead row from the agent view — the 44-day zombie case."""
    config, tmp_path, calls, claude_log = panel
    app = TarmacApp(config)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        ol = app.query_one("#sessions", OptionList)
        ol.highlighted = next(
            i for i in range(ol.option_count)
            if (o := ol.get_option_at_index(i)) is not None and o.id == "mac|blk00001"
        )
        await pilot.press("R")
        await pilot.pause()
        await pilot.press("escape")          # cancelling must not remove anything
        await app.workers.wait_for_complete()
        assert "rm blk00001" not in (claude_log.read_text() if claude_log.exists() else "")

        await pilot.press("R")
        await pilot.pause()
        await pilot.press("enter")           # confirm
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "rm blk00001" in claude_log.read_text()


async def test_respawn_asks_and_then_calls_claude_respawn(panel):
    """`r` restarts the process of a session that stopped responding.

    It sits one shift away from `R` (remove from the list), which is why it
    asks before doing anything: the two keys are neighbours and only one of
    them is recoverable."""
    config, tmp_path, calls, claude_log = panel
    app = TarmacApp(config)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        ol = app.query_one("#sessions", OptionList)
        ol.highlighted = next(
            i for i in range(ol.option_count)
            if (o := ol.get_option_at_index(i)) is not None and o.id == "mac|blk00001"
        )
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("escape")          # cancelling must not restart anything
        await app.workers.wait_for_complete()
        assert "respawn blk00001" not in (claude_log.read_text()
                                          if claude_log.exists() else "")

        await pilot.press("r")
        await pilot.pause()
        await pilot.press("enter")           # confirm
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "respawn blk00001" in claude_log.read_text()
        assert app.is_running
