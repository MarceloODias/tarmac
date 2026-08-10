"""Regressions found by Marcelo while using the panel.

1. `backend-agent` (a chatops session) showed up as TRABALHANDO: its cwd was
   exactly the dir in match_cwd, and `dir/**` didn't match `dir`.
2. That same row had already vanished from the --json (gone=1) hours earlier
   and kept rendering as live — a vanished blocked session would inflate the
   badge forever, and the badge is load-bearing.
3. `x` (resolve) did nothing on a blocked row: it only applies to overdue
   reminders, but it failed silently instead of saying so.
"""

import json

import pytest
from textual.widgets import OptionList

from tarmac import db as dbm
from tarmac.collect import TargetResult, apply_result
from tarmac.config import Config, SessionClassRule, Settings, Target
from tarmac.derive import badge, build_view
from tarmac.model import parse_agents_json
from tarmac.render.tui_app import TarmacApp


def conn_for(tmp_path):
    return dbm.connect(tmp_path / "t.db")


def target(**kw):
    base = dict(id="t1", label="T1", mine=True, transport="local")
    base.update(kw)
    return Target(**base)


def config_for(*targets):
    return Config(targets=list(targets), settings=Settings(stale_after_s=10_000))


def test_vanished_session_stops_rendering_as_live(tmp_path):
    conn = conn_for(tmp_path)
    t = target()
    raw = [
        {"id": "work0001", "kind": "background", "state": "working",
         "cwd": "/x", "startedAt": 1, "name": "trabalho"},
        {"id": "blok0001", "kind": "background", "state": "blocked",
         "cwd": "/x", "startedAt": 2, "name": "bloqueada"},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    cfg = config_for(t)
    assert len(build_view(cfg, conn).working) == 1
    assert len(build_view(cfg, conn).blocked) == 1

    apply_result(conn, TargetResult(t, []))  # both vanished from the listing
    view = build_view(cfg, conn)
    assert view.working == [], "sessão sumida continuou em TRABALHANDO"
    assert view.blocked == [], "sessão sumida continuou contando no badge"
    assert badge(view)[0].startswith("✓")


def test_vanished_session_with_a_reminder_still_shows(tmp_path):
    # intent outlives the listing (SPEC §5.2): a reminder must survive
    conn = conn_for(tmp_path)
    t = target()
    raw = [{"id": "work0001", "kind": "background", "state": "working",
            "cwd": "/x", "startedAt": 1, "name": "trabalho"}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    dbm.upsert_meta(conn, "t1", "work0001", due_at=dbm.now_ms() - 1000,
                    due_label="ontem")
    apply_result(conn, TargetResult(t, []))
    assert len(build_view(config_for(t), conn).overdue) == 1


def test_pinned_vanished_session_still_shows(tmp_path):
    conn = conn_for(tmp_path)
    t = target()
    raw = [{"id": "work0001", "kind": "background", "state": "working",
            "cwd": "/x", "startedAt": 1, "name": "trabalho"}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    dbm.upsert_meta(conn, "t1", "work0001", pinned=1)
    apply_result(conn, TargetResult(t, []))
    assert len(build_view(config_for(t), conn).working) == 1


def test_service_glob_matches_the_directory_itself(tmp_path):
    conn = conn_for(tmp_path)
    rule = SessionClassRule(class_="service", label="chatops",
                            match_cwd="/home/u/ai-agent-skills/**")
    t = target(session_classes=[rule])
    raw = [
        {"sessionId": "svc-root", "kind": "interactive", "status": "busy",
         "cwd": "/home/u/ai-agent-skills", "name": "backend-agent", "startedAt": 1},
        {"sessionId": "svc-deep", "kind": "interactive", "status": "busy",
         "cwd": "/home/u/ai-agent-skills/Monitoring/Bot", "name": "bot", "startedAt": 2},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    view = build_view(config_for(t), conn)
    assert view.working == [], "sessão de serviço vazou para a lista principal"
    assert view.services and view.services[0].active == 2


def test_sibling_directory_is_not_swallowed_by_the_glob(tmp_path):
    # /ai-agent-skills-old must NOT match /ai-agent-skills/**
    conn = conn_for(tmp_path)
    rule = SessionClassRule(class_="service", match_cwd="/home/u/ai-agent-skills/**")
    t = target(session_classes=[rule])
    raw = [{"sessionId": "mine", "kind": "interactive", "status": "busy",
            "cwd": "/home/u/ai-agent-skills-old", "name": "meu", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    assert len(build_view(config_for(t), conn).working) == 1


@pytest.fixture
def blocked_app(tmp_path, monkeypatch):
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    conn = dbm.connect(tmp_path / "tarmac.db")
    t = target()
    raw = [{"id": "blok0001", "kind": "background", "state": "blocked",
            "cwd": "/x", "startedAt": 1, "name": "bloqueada"}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    dbm.kv_set(conn, "last_collect_at", str(dbm.now_ms()))
    conn.commit()
    return config_for(t)


async def test_resolve_on_blocked_row_explains_itself(blocked_app):
    """`x` on a blocked session can't resolve anything — the session really is
    waiting. It must SAY so instead of doing nothing."""
    app = TarmacApp(blocked_app)
    notes = []
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.notify = lambda msg, **kw: notes.append(msg)
        ol = app.query_one("#sessions", OptionList)
        ol.highlighted = next(
            i for i in range(ol.option_count)
            if (o := ol.get_option_at_index(i)) is not None and o.id == "t1|blok0001"
        )
        await pilot.press("x")
        await pilot.pause()
        assert notes, "tecla x não deu retorno nenhum ao usuário"
        assert "bloqueada" in notes[0].lower() or "vencid" in notes[0].lower()


async def test_error_footer_shows_the_reason(tmp_path, monkeypatch):
    """A bare ⚠ next to (stale) rows is not enough: the footer must name the
    actual failure, or the user has to go ask someone what broke."""
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    conn = dbm.connect(tmp_path / "tarmac.db")
    t = target()
    apply_result(conn, TargetResult(
        t, None, error="sh: claude: command not found", error_kind="error"))
    dbm.kv_set(conn, "last_collect_at", str(dbm.now_ms()))
    conn.commit()

    app = TarmacApp(config_for(t))
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        from textual.widgets import Static
        rendered = str(app.query_one("#badge", Static).render())
        assert "command not found" in rendered, rendered
