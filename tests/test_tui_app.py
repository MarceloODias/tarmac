"""Headless tests for the interactive TUI (textual run_test harness)."""

import json

import pytest

from tarmac import db as dbm
from tarmac.collect import TargetResult, apply_result
from tarmac.config import Config, Settings, Target
from tarmac.model import parse_agents_json
from tarmac.render.tui_app import TarmacApp
from textual.widgets import OptionList


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    conn = dbm.connect(tmp_path / "tarmac.db")
    t = Target(id="t1", label="T1", mine=True, transport="local")
    raw = [
        {"id": "block001", "kind": "background", "state": "blocked",
         "cwd": "/x/projeto-a", "startedAt": 1, "name": "revisar-auth"},
        {"id": "work0001", "kind": "background", "state": "working",
         "cwd": "/x/projeto-b", "startedAt": 2, "name": "backfill"},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    conn.commit()
    dbm.kv_set(conn, "last_collect_at", str(dbm.now_ms()))
    conn.commit()
    # no enabled targets in the config -> collect_if_stale is a no-op, but the
    # DB above still feeds build_view via... actually build_view filters by
    # enabled targets, so keep t1 in the config.
    return Config(targets=[t], settings=Settings(stale_after_s=10_000))


async def test_app_lists_sessions_and_navigates(app_env):
    app = TarmacApp(app_env)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        ol = app.query_one("#sessions", OptionList)
        assert ol.option_count > 0
        ids = [ol.get_option_at_index(i).id for i in range(ol.option_count)
               if ol.get_option_at_index(i) is not None]
        assert "t1|block001" in ids
        assert "t1|work0001" in ids

        # arrow keys move the highlight across enabled options only
        await pilot.press("down")
        cur = app._current()
        assert cur is not None
        _, row = cur
        assert row.session_id in ("block001", "work0001")


async def test_pin_action_persists(app_env):
    app = TarmacApp(app_env)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app._current() is not None
        app.action_pin()
        await pilot.pause()
        meta = dbm.get_meta(app.conn, "t1", app._current()[1].session_id)
        assert meta is not None and meta["pinned"] == 1


async def test_wide_terminal_gets_wide_names(app_env):
    app = TarmacApp(app_env)
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.pause()
        # fullscreen default: name column flexes up to 80 cols
        assert app.size.width == 200
