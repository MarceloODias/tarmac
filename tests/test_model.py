"""Parser tests against the real sanitized fixtures (SPEC §14: fixtures from
the actual --json output, captured in task 0)."""

import json
from pathlib import Path

import pytest

from tarmac.model import parse_agents_json, parse_session

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parses_local_fixture():
    sessions = parse_agents_json(load("agents-local.json"))
    assert len(sessions) == 9
    by_kind = {}
    for s in sessions:
        by_kind.setdefault(s.kind, []).append(s)
    assert len(by_kind["interactive"]) == 6
    assert len(by_kind["background"]) == 3


def test_parses_ec2_fixture_with_failed_state():
    sessions = parse_agents_json(load("agents-ec2.json"))
    states = {s.effective_state for s in sessions}
    assert "failed" in states
    assert "blocked" in states
    assert "done" in states


def test_effective_state_background_blocked_without_waiting_for():
    # FINDINGS A4/E: bg session blocked on a question reports status=idle,
    # no waitingFor — state must win.
    s = parse_session({
        "id": "abc12345", "kind": "background", "state": "blocked",
        "status": "idle", "cwd": "/x",
    })
    assert s.effective_state == "blocked"
    assert s.waiting_for is None


def test_effective_state_interactive_waiting():
    s = parse_session({
        "sessionId": "u-1", "kind": "interactive", "status": "waiting",
        "waitingFor": "permission prompt",
    })
    assert s.effective_state == "blocked"
    assert s.waiting_for == "permission prompt"


def test_tolerates_missing_and_unknown_fields():
    # SPEC §2: unknown fields ignored, missing fields never crash
    s = parse_session({"id": "x", "novoCampoDesconhecido": {"a": 1}})
    assert s is not None
    assert s.effective_state == "unknown"
    assert parse_session({"kind": "interactive"}) is None  # no id at all
    assert parse_session("not-a-dict") is None


def test_non_array_json_raises():
    with pytest.raises(ValueError):
        parse_agents_json(json.dumps({"sessions": []}))


def test_never_named_heuristic_lowercases_basename():
    # FINDINGS D2: BackendHealthMonitor -> backendhealthmonitor-51
    s = parse_session({
        "sessionId": "u-2", "kind": "interactive",
        "cwd": "/home/u/ai-agent-skills/ClaudeCode/Monitoring/BackendHealthMonitor",
        "name": "backendhealthmonitor-51",
    })
    assert s.never_named

    named = parse_session({
        "sessionId": "u-3", "kind": "interactive",
        "cwd": "/home/u/projects/api", "name": "fix-login-bug",
    })
    assert not named.never_named


def test_never_named_on_local_fixture():
    # G1: 3 of the 7 interactive sessions match the default-name pattern
    sessions = parse_agents_json(load("agents-local.json"))
    unnamed = [s for s in sessions if s.kind == "interactive" and s.never_named]
    assert len(unnamed) == 3
