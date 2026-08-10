"""Drain of the SessionEnd next_step queue (SPEC §10)."""

import json

from tarmac import db as dbm
from tarmac.collect import (
    QUEUE_SENTINEL, TargetResult, apply_next_steps, apply_result, parse_queue,
)
from tarmac.config import Target, tarmac_home
from tarmac.model import parse_agents_json


def make_target(**kw) -> Target:
    defaults = dict(id="t1", label="T1", mine=True, transport="local")
    defaults.update(kw)
    return Target(**defaults)


def seed(conn):
    t = make_target()
    raw = [{"id": "abc12345", "kind": "background", "state": "done",
            "sessionId": "abc12345-1111-2222-3333-444455556666",
            "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    return t


def test_queue_is_parsed_from_the_collection_output(tmp_path, monkeypatch):
    """The queue now rides back on the same round trip as `agents --json`,
    instead of a second SSH connection per cycle (1440/day, all empty)."""
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    stdout = (
        '[]\n' + QUEUE_SENTINEL + '\n'
        + json.dumps({"session_id": "abc12345-1111-2222-3333-444455556666",
                      "next_step": "revisar o PR", "at": 1}) + "\n"
        + "linha inválida\n"
    )
    payload, _, queue = stdout.partition(QUEUE_SENTINEL)
    entries = parse_queue(queue)
    assert payload.strip() == "[]"
    assert len(entries) == 1 and entries[0]["next_step"] == "revisar o PR"


def test_apply_matches_by_uuid_and_respects_manual(tmp_path, monkeypatch):
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    conn = dbm.connect(tmp_path / "t.db")
    t = seed(conn)

    apply_next_steps(conn, t, [{"session_id": "abc12345-1111-2222-3333-444455556666",
                                "next_step": "rodar os testes", "at": 1}])
    meta = dbm.get_meta(conn, "t1", "abc12345")  # keyed by short id (bg)
    assert meta["next_step"] == "rodar os testes"
    assert meta["next_step_origin"] == "auto"

    # manual wins over later auto
    dbm.upsert_meta(conn, "t1", "abc12345", next_step="meu texto",
                    next_step_origin="manual")
    apply_next_steps(conn, t, [{"session_id": "abc12345-1111-2222-3333-444455556666",
                                "next_step": "outra coisa", "at": 2}])
    assert dbm.get_meta(conn, "t1", "abc12345")["next_step"] == "meu texto"


def test_apply_unknown_session_stores_by_uuid(tmp_path, monkeypatch):
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    conn = dbm.connect(tmp_path / "t.db")
    t = seed(conn)
    apply_next_steps(conn, t, [{"session_id": "ffffffff-0000-0000-0000-000000000000",
                                "next_step": "pendente", "at": 1}])
    meta = dbm.get_meta(conn, "t1", "ffffffff-0000-0000-0000-000000000000")
    assert meta is not None and meta["next_step_origin"] == "auto"
