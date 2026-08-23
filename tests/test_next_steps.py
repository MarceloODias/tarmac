"""Drain of the SessionEnd next_step queue (SPEC §10)."""

import json
from pathlib import Path

from tarmac import db as dbm
from tarmac.collect import (
    TargetResult,
    apply_next_steps,
    apply_result,
    entry_target,
    fetch_next_steps,
    machine_key,
    route_next_steps,
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


def test_fetch_local_reads_and_truncates(tmp_path, monkeypatch):
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    q = tarmac_home() / "next-steps.jsonl"
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text(
        json.dumps({"session_id": "abc12345-1111-2222-3333-444455556666",
                    "next_step": "revisar o PR", "at": 1}) + "\n"
        + "linha inválida\n"
    )
    t = make_target()
    entries = fetch_next_steps(t)
    assert len(entries) == 1
    assert q.read_text() == ""          # drained
    assert fetch_next_steps(t) == []    # idempotent


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


# --- two config dirs on one machine share one queue (SPEC §10) ---------------

MINE = "abc12345-1111-2222-3333-444455556666"
THEIRS = "dddddddd-2222-3333-4444-555566667777"


def two_local_targets():
    return [
        make_target(id="mac", config_dir="~/.claude"),
        make_target(id="mac-personal", config_dir="~/.claude-personal"),
    ]


def test_targets_on_one_machine_share_a_queue_key():
    a, b = two_local_targets()
    assert machine_key(a) == machine_key(b)
    remote = make_target(id="ec2", transport="ssh", ssh_host="h", claude_bin="/bin/claude")
    assert machine_key(remote) != machine_key(a)
    other_host = make_target(id="ubu", transport="ssh", ssh_host="h2", claude_bin="/bin/claude")
    assert machine_key(remote) != machine_key(other_host)


def test_entry_is_routed_by_the_config_dir_the_hook_ran_under(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    targets = two_local_targets()
    home = str(Path("~").expanduser())
    assert entry_target(conn, targets, {
        "session_id": THEIRS, "config_dir": f"{home}/.claude-personal",
    }).id == "mac-personal"
    assert entry_target(conn, targets, {
        "session_id": MINE, "config_dir": f"{home}/.claude",
    }).id == "mac"


def test_legacy_entry_without_config_dir_falls_back_to_the_session_row(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    targets = two_local_targets()
    raw = [{"id": "abc12345", "kind": "background", "state": "done",
            "sessionId": MINE, "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(targets[1], parse_agents_json(json.dumps(raw))))
    assert entry_target(conn, targets, {"session_id": MINE}).id == "mac-personal"
    # nothing to go on: first target, i.e. today's behaviour
    assert entry_target(conn, targets, {"session_id": THEIRS}).id == "mac"


def test_route_keeps_each_account_next_step_on_its_own_target(tmp_path, monkeypatch):
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    conn = dbm.connect(tmp_path / "t.db")
    targets = two_local_targets()
    home = str(Path("~").expanduser())
    route_next_steps(conn, targets, [
        {"session_id": MINE, "next_step": "revisar o PR",
         "config_dir": f"{home}/.claude", "at": 1},
        {"session_id": THEIRS, "next_step": "terminar o rascunho",
         "config_dir": f"{home}/.claude-personal", "at": 2},
    ])
    assert dbm.get_meta(conn, "mac", MINE)["next_step"] == "revisar o PR"
    assert dbm.get_meta(conn, "mac-personal", THEIRS)["next_step"] == "terminar o rascunho"
    # neither leaked into the other account
    assert dbm.get_meta(conn, "mac", THEIRS) is None
    assert dbm.get_meta(conn, "mac-personal", MINE) is None


def test_ssh_target_matches_by_path_suffix_since_remote_home_is_unknown():
    t = make_target(id="ec2", transport="ssh", ssh_host="h", claude_bin="/bin/claude",
                    config_dir="~/.claude-alt")
    assert t.matches_config_dir("/home/ec2-user/.claude-alt")
    assert not t.matches_config_dir("/home/ec2-user/.claude")


def test_collect_drains_the_shared_queue_once_for_both_targets(tmp_path, monkeypatch):
    """The bug: the first local target drained everything and mis-filed it."""
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    from tarmac.collect import collect
    from tarmac.config import Config, Settings
    conn = dbm.connect(tmp_path / "t.db")
    targets = two_local_targets()
    home = str(Path("~").expanduser())
    q = tarmac_home() / "next-steps.jsonl"
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text(
        json.dumps({"session_id": MINE, "next_step": "um",
                    "config_dir": f"{home}/.claude", "at": 1}) + "\n"
        + json.dumps({"session_id": THEIRS, "next_step": "dois",
                      "config_dir": f"{home}/.claude-personal", "at": 2}) + "\n"
    )
    monkeypatch.setattr("tarmac.collect.collect_target",
                        lambda t: TargetResult(t, parse_agents_json("[]")))
    collect(Config(targets=targets, settings=Settings()), conn, force=True)
    assert q.read_text() == ""
    assert dbm.get_meta(conn, "mac", MINE)["next_step"] == "um"
    assert dbm.get_meta(conn, "mac-personal", THEIRS)["next_step"] == "dois"


# --- an auto note describes the moment the session ENDED --------------------

def test_working_again_clears_the_auto_note(tmp_path, monkeypatch):
    """A session that resumed kept showing the note from its previous end —
    including one that was two weeks old."""
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    conn = dbm.connect(tmp_path / "t.db")
    t = seed(conn)                                   # 'abc12345', state done
    dbm.upsert_meta(conn, "t1", "abc12345", next_step="corrigir a MV",
                    next_step_origin="auto")

    raw = [{"id": "abc12345", "kind": "background", "state": "working",
            "sessionId": "abc12345-1111-2222-3333-444455556666",
            "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    assert dbm.get_meta(conn, "t1", "abc12345")["next_step"] is None


def test_working_again_keeps_what_i_typed(tmp_path, monkeypatch):
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    conn = dbm.connect(tmp_path / "t.db")
    t = seed(conn)
    dbm.upsert_meta(conn, "t1", "abc12345", next_step="retomo terça",
                    next_step_origin="manual")

    raw = [{"id": "abc12345", "kind": "background", "state": "working",
            "sessionId": "abc12345-1111-2222-3333-444455556666",
            "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    assert dbm.get_meta(conn, "t1", "abc12345")["next_step"] == "retomo terça"


def test_an_idle_session_keeps_its_auto_note(tmp_path, monkeypatch):
    """Only working invalidates it: idle is still 'waiting on me'."""
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    conn = dbm.connect(tmp_path / "t.db")
    t = seed(conn)
    dbm.upsert_meta(conn, "t1", "abc12345", next_step="revisar o PR",
                    next_step_origin="auto")
    raw = [{"sessionId": "abc12345-1111-2222-3333-444455556666",
            "kind": "interactive", "status": "idle", "cwd": "/x", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    assert dbm.get_meta(conn, "t1", "abc12345")["next_step"] == "revisar o PR"
