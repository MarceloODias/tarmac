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


def test_idle_sessions_group_by_folder(tmp_path):
    """4. Sessões do mesmo projeto ficavam espalhadas na lista: em IDLE a ordem
    era a do banco (started_at), então dois assuntos vizinhos apareciam
    separados por meia dúzia de linhas de outros projetos."""
    conn = conn_for(tmp_path)
    t = target()
    raw = [
        {"sessionId": "aaa00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/proj/benji-dp", "name": "ingestao", "startedAt": 1},
        {"sessionId": "bbb00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/proj/rtb-index", "name": "stress", "startedAt": 2},
        {"sessionId": "ccc00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/proj/benji-dp", "name": "backfill", "startedAt": 3},
        {"sessionId": "ddd00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/proj/rtb-index", "name": "deploy", "startedAt": 4},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    view = build_view(config_for(t), conn)

    folders = [r.cwd for r in view.other]
    assert folders == ["/proj/benji-dp", "/proj/benji-dp",
                       "/proj/rtb-index", "/proj/rtb-index"], \
        f"sessões do mesmo projeto ficaram separadas: {folders}"


def test_same_size_folders_follow_the_path_not_the_session_name(tmp_path):
    """6. Pastas de mesmo tamanho desempatavam pelo nome da sessão, o que
    espalhava as irmãs de uma mesma árvore (/inpowered/*) pela lista."""
    conn = conn_for(tmp_path)
    t = target()
    raw = [
        {"sessionId": "zzz00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/inpowered/alpha", "name": "zulu", "startedAt": 1},
        {"sessionId": "aaa00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/outro/beta", "name": "alfa", "startedAt": 2},
        {"sessionId": "mmm00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/inpowered/omega", "name": "mike", "startedAt": 3},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    view = build_view(config_for(t), conn)

    folders = [r.cwd for r in view.other]
    assert folders == ["/inpowered/alpha", "/inpowered/omega", "/outro/beta"], \
        f"as pastas de /inpowered deviam ficar vizinhas: {folders}"


def test_folder_with_more_sessions_comes_first(tmp_path):
    """5. Ordem das pastas: a que concentra mais sessões vai para cima, mesmo
    que a espera mais longa esteja numa pasta menor. O badge, que existe para
    escalar, passa a ler a pior espera em vez da primeira linha."""
    conn = conn_for(tmp_path)
    t = target()
    raw = [
        {"id": "big00001", "kind": "background", "state": "blocked",
         "cwd": "/proj/grande", "startedAt": 1, "name": "g1"},
        {"id": "big00002", "kind": "background", "state": "blocked",
         "cwd": "/proj/grande", "startedAt": 2, "name": "g2"},
        {"id": "sml00001", "kind": "background", "state": "blocked",
         "cwd": "/proj/pequeno", "startedAt": 3, "name": "p1"},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    now = dbm.now_ms()
    # a espera mais longa (2h) está na pasta com UMA sessão
    for sid, minutes in (("big00001", 10), ("big00002", 5), ("sml00001", 120)):
        conn.execute(
            "UPDATE transitions SET at = ? WHERE target_id = ? AND session_id = ?",
            (now - minutes * 60_000, "t1", sid))
    conn.commit()

    view = build_view(config_for(t), conn)
    assert [r.display_name for r in view.blocked] == ["g1", "g2", "p1"], \
        "a pasta com mais sessões devia vir primeiro"
    text, severity = badge(view)
    assert "2h" in text, f"badge devia mostrar a pior espera (2h), mostrou: {text}"
    assert severity == "alarm", f"a escalação se perdeu com a nova ordem: {severity}"


def test_blocked_keeps_longest_wait_on_top_while_grouping(tmp_path):
    """Empate de contagem: aí quem manda é a espera. A pasta da sessão mais
    antiga vem primeiro e a irmã de pasta a acompanha, em vez de cair no fim."""
    conn = conn_for(tmp_path)
    t = target()
    raw = [
        {"id": "old00001", "kind": "background", "state": "blocked",
         "cwd": "/proj/a", "startedAt": 1, "name": "antiga"},
        {"id": "mid00001", "kind": "background", "state": "blocked",
         "cwd": "/proj/b", "startedAt": 2, "name": "media"},
        {"id": "new00001", "kind": "background", "state": "blocked",
         "cwd": "/proj/a", "startedAt": 3, "name": "recente"},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    now = dbm.now_ms()
    for sid, minutes in (("old00001", 90), ("mid00001", 45), ("new00001", 5)):
        conn.execute(
            "UPDATE transitions SET at = ? WHERE target_id = ? AND session_id = ?",
            (now - minutes * 60_000, "t1", sid))
    conn.commit()

    view = build_view(config_for(t), conn)
    names = [r.display_name for r in view.blocked]
    assert names[0] == "antiga", f"a maior espera saiu do topo: {names}"
    assert names == ["antiga", "recente", "media"], \
        f"a pasta /proj/a devia vir junta, depois /proj/b: {names}"
