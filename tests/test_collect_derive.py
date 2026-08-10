"""Collector + view pipeline over an in-memory DB, driven by the fixtures.

Covers the acceptance criteria that don't need a live target (SPEC §13):
service classification (6b-6d), transitions/blocked_since (7), badge (8),
gone marking, offline vs error classification (4, 5), uncertain waits (6).
"""

import json
import time
from pathlib import Path

from tarmac import db as dbm
from tarmac.collect import TargetResult, apply_result, classify_failure
from tarmac.config import Config, SessionClassRule, Settings, Target
from tarmac.derive import badge, build_view
from tarmac.model import parse_agents_json

FIXTURES = Path(__file__).parent.parent / "fixtures"


def make_conn(tmp_path):
    return dbm.connect(tmp_path / "test.db")


def make_target(**kw) -> Target:
    defaults = dict(id="t1", label="T1", mine=True, transport="local")
    defaults.update(kw)
    return Target(**defaults)


def make_config(*targets) -> Config:
    return Config(targets=list(targets), settings=Settings())


def sessions_from(name: str):
    return parse_agents_json((FIXTURES / name).read_text())


def test_collect_populates_and_marks_gone(tmp_path):
    conn = make_conn(tmp_path)
    t = make_target()
    apply_result(conn, TargetResult(t, sessions_from("agents-local.json")))
    n = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
    assert n == 9

    # next cycle: only the first 3 sessions remain -> others gone=1, not deleted
    remaining = sessions_from("agents-local.json")[:3]
    apply_result(conn, TargetResult(t, remaining))
    gone = conn.execute("SELECT COUNT(*) c FROM sessions WHERE gone = 1").fetchone()["c"]
    assert gone == 6
    assert conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 9


def test_transition_recorded_and_blocked_since(tmp_path):
    conn = make_conn(tmp_path)
    t = make_target()
    working = [s for s in sessions_from("agents-ec2.json")]
    apply_result(conn, TargetResult(t, working))
    blocked = [s for s in working if s.effective_state == "blocked"]
    assert blocked
    bs = dbm.blocked_since(conn, "t1", blocked[0].session_id)
    assert bs is not None
    at, uncertain = bs
    assert not uncertain
    assert abs(at - dbm.now_ms()) < 5000


def test_service_classification_excluded_from_badge(tmp_path):
    # SPEC §13 6b-6c: service sessions never hit the list nor the badge
    conn = make_conn(tmp_path)
    rule = SessionClassRule(class_="service", label="chatops",
                            match_cwd="/home/user/ai-agent-skills/**")
    t = make_target(session_classes=[rule])
    raw = [
        {"sessionId": f"svc-{i}", "kind": "interactive", "status": "busy",
         "cwd": "/home/user/ai-agent-skills/Bot", "startedAt": 1}
        for i in range(3)
    ]
    raw.append({"id": "own00001", "kind": "background", "state": "blocked",
                "cwd": "/home/user/projects/app", "startedAt": 1})
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))

    view = build_view(make_config(t), conn)
    assert len(view.blocked) == 1            # only the owned one
    assert len(view.services) == 1
    assert view.services[0].active == 3
    text, _ = badge(view)
    assert "⏸ 1" in text                     # 3 service sessions don't inflate it


def test_service_stuck_alert(tmp_path):
    # SPEC §13 6d: a service session blocked > threshold adds '1 travada' + ⚠
    conn = make_conn(tmp_path)
    rule = SessionClassRule(class_="service", label="chatops",
                            match_cwd="/svc/**")
    t = make_target(session_classes=[rule])
    raw = [{"sessionId": "svc-stuck", "kind": "interactive", "status": "waiting",
            "cwd": "/svc/bot", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    # backdate the blocked transition beyond the 30 min threshold
    conn.execute("UPDATE transitions SET at = ?", (dbm.now_ms() - 45 * 60_000,))
    view = build_view(make_config(t), conn)
    assert view.services[0].stuck == 1


def test_offline_vs_error_classification():
    assert classify_failure("ssh: connect to host x: Operation timed out") == "offline"
    assert classify_failure("ssh: No route to host") == "offline"
    assert classify_failure("Permission denied (publickey)") == "error"
    assert classify_failure("bash: claude: command not found") == "error"


def test_offline_target_stays_calm_error_target_is_loud(tmp_path):
    # SPEC §13 4-5: bad credential -> ⚠; VPN down on intermittent target -> gray
    conn = make_conn(tmp_path)
    vpn = make_target(id="vpn", transport="ssh", ssh_host="h", claude_bin="/bin/claude",
                      expect_intermittent=True, offline_after=1)
    bad = make_target(id="bad", transport="ssh", ssh_host="h2", claude_bin="/bin/claude")
    apply_result(conn, TargetResult(vpn, None, error="timed out", error_kind="offline"))
    apply_result(conn, TargetResult(bad, None, error="Permission denied", error_kind="error"))

    view = build_view(make_config(vpn, bad), conn)
    states = {t.target_id: t.state for t in view.targets}
    assert states["vpn"] == "offline"
    assert states["bad"] == "error"
    assert view.has_error
    _, severity = badge(view)
    assert severity != "ok"


def test_uncertain_blocked_after_blind_window(tmp_path):
    # SPEC §13 6: a session that blocked while the target was offline shows >=
    conn = make_conn(tmp_path)
    t = make_target(id="vpn", transport="ssh", ssh_host="h", claude_bin="/bin/claude",
                    expect_intermittent=True, offline_after=2)
    raw_working = [{"id": "sess0001", "kind": "background", "state": "working",
                    "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw_working))))

    # go blind for two cycles
    apply_result(conn, TargetResult(t, None, error="timed out", error_kind="offline"))
    apply_result(conn, TargetResult(t, None, error="timed out", error_kind="offline"))

    # reconnect: now blocked — we cannot know when it happened
    raw_blocked = [{"id": "sess0001", "kind": "background", "state": "blocked",
                    "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw_blocked))))

    bs = dbm.blocked_since(conn, "vpn", "sess0001")
    assert bs is not None and bs[1] is True  # uncertain
    view = build_view(make_config(t), conn)
    assert view.blocked[0].wait_uncertain


def test_gone_session_meta_survives(tmp_path):
    # SPEC §13 17: checklist survives the session vanishing from the JSON
    conn = make_conn(tmp_path)
    t = make_target()
    raw = [{"id": "temp0001", "kind": "background", "state": "working",
            "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    conn.execute(
        "INSERT INTO session_checklist (target_id, session_id, position, text, created_at) "
        "VALUES ('t1', 'temp0001', 1, 'revisar PR', ?)", (dbm.now_ms(),))
    dbm.upsert_meta(conn, "t1", "temp0001", next_step="responder pergunta")

    apply_result(conn, TargetResult(t, []))  # vanished
    row = conn.execute("SELECT gone FROM sessions WHERE session_id='temp0001'").fetchone()
    assert row["gone"] == 1
    assert dbm.get_meta(conn, "t1", "temp0001")["next_step"] == "responder pergunta"
    n = conn.execute("SELECT COUNT(*) c FROM session_checklist").fetchone()["c"]
    assert n == 1


def test_service_retention_prunes_after_24h(tmp_path):
    # SPEC §13 6e
    conn = make_conn(tmp_path)
    rule = SessionClassRule(class_="service", match_cwd="/svc/**")
    t = make_target(session_classes=[rule])
    raw = [{"sessionId": "svc-old", "kind": "interactive", "status": "busy",
            "cwd": "/svc/bot", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    apply_result(conn, TargetResult(t, []))  # gone
    conn.execute("UPDATE sessions SET last_seen_at = ?",
                 (dbm.now_ms() - 25 * 3_600_000,))
    assert dbm.prune_service_sessions(conn) == 1
    assert conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 0


def test_badge_states(tmp_path):
    conn = make_conn(tmp_path)
    t = make_target()
    view = build_view(make_config(t), conn)
    text, severity = badge(view)
    assert text.startswith("✓")

    raw = [{"id": "block001", "kind": "background", "state": "blocked",
            "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    conn.execute("UPDATE transitions SET at = ?", (dbm.now_ms() - 45 * 60_000,))
    view = build_view(make_config(t), conn)
    text, severity = badge(view)
    assert text.startswith("⏸ 1 · 45m")
    assert severity == "alarm"


def test_overdue_rises_and_stays(tmp_path):
    # SPEC §13 15: overdue goes to PRA HOJE and stays until resolved
    conn = make_conn(tmp_path)
    t = make_target()
    raw = [{"id": "due00001", "kind": "background", "state": "working",
            "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    dbm.upsert_meta(conn, "t1", "due00001",
                    due_at=dbm.now_ms() - 3_600_000, due_label="ontem")
    view = build_view(make_config(t), conn)
    assert len(view.overdue) == 1
    text, _ = badge(view)
    assert "⏱ 1" in text

    dbm.upsert_meta(conn, "t1", "due00001", resolved_at=dbm.now_ms())
    view = build_view(make_config(t), conn)
    assert not view.overdue


def test_defer_hides_until_due(tmp_path):
    # SPEC §13 16: deferred session leaves the main list, keeps next_step
    conn = make_conn(tmp_path)
    t = make_target()
    raw = [{"id": "defer001", "kind": "background", "state": "blocked",
            "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    dbm.upsert_meta(conn, "t1", "defer001", due_at=dbm.now_ms() + 86_400_000,
                    due_label="amanhã", hide_until_due=1, next_step="revisar")
    view = build_view(make_config(t), conn)
    assert not view.blocked
    assert len(view.scheduled) == 1
    assert view.scheduled[0].next_step == "revisar"


def test_mine_filter(tmp_path):
    # SPEC §13 3: second owner works without touching code; mine filter holds
    conn = make_conn(tmp_path)
    mine = make_target(id="mine")
    theirs = make_target(id="theirs", mine=False)
    raw = [{"id": "aaaa0001", "kind": "background", "state": "working",
            "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(mine, parse_agents_json(json.dumps(raw))))
    apply_result(conn, TargetResult(theirs, parse_agents_json(json.dumps(raw))))
    config = make_config(mine, theirs)
    assert len(build_view(config, conn, mine_only=True).working) == 1
    assert len(build_view(config, conn, mine_only=False).working) == 2
