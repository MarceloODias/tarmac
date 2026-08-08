"""Standalone tasks: folder inference from task text + view integration."""

import json

from tarmac import db as dbm
from tarmac.collect import TargetResult, apply_result
from tarmac.config import Config, Settings, Target
from tarmac.derive import build_view
from tarmac.model import parse_agents_json
from tarmac.tasks import (
    Candidate,
    add_task,
    cwd_candidates,
    infer_folder,
    resolve_task,
)


def seed_history(conn):
    """Sessions history across two targets — the source of folder frequency."""
    mac = Target(id="mac", label="Mac", transport="local")
    ec2 = Target(id="ec2", label="EC2", transport="ssh", ssh_host="h",
                 claude_bin="/bin/claude")
    raw_mac = [
        {"sessionId": f"m{i}-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/Users/u/projects/inpowered/benji-dp",
         "startedAt": i} for i in range(4)
    ] + [
        {"sessionId": "w1-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle",
         "cwd": "/Users/u/projects/acervo/.claude/worktrees/fix-x", "startedAt": 9},
        {"sessionId": "w2-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/Users/u/projects/acervo", "startedAt": 10},
    ]
    raw_ec2 = [{"id": "abcd1234", "kind": "background", "state": "working",
                "cwd": "/home/u/prod-versions/Index", "startedAt": 1}]
    apply_result(conn, TargetResult(mac, parse_agents_json(json.dumps(raw_mac))))
    apply_result(conn, TargetResult(ec2, parse_agents_json(json.dumps(raw_ec2))))
    return mac, ec2


def test_candidates_fold_worktrees_and_rank_by_use(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    seed_history(conn)
    cands = cwd_candidates(conn)
    cwds = [c.cwd for c in cands]
    assert "/Users/u/projects/inpowered/benji-dp" == cwds[0]  # most used
    assert "/Users/u/projects/acervo" in cwds
    assert not any(".claude/worktrees" in c for c in cwds)    # folded


def test_infer_folder_from_text(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    seed_history(conn)
    cands = cwd_candidates(conn)

    hit = infer_folder("no benji-dp, preciso dividir os rampids em ssps", cands)
    assert hit is not None and hit.cwd.endswith("benji-dp")

    assert infer_folder("comprar café", cands) is None
    # substring inside a word must NOT match ("index" in "indexação"? guard)
    assert infer_folder("melhorar a indexação geral", cands) is None


def test_infer_ambiguous_prefers_dominant():
    cands = [Candidate("mac", "/a/api", 9), Candidate("ec2", "/b/api", 1)]
    hit = infer_folder("consertar o api", cands)
    assert hit is not None and hit.target_id == "mac"
    # close counts -> ambiguous -> ask
    cands = [Candidate("mac", "/a/api", 3), Candidate("ec2", "/b/api", 2)]
    assert infer_folder("consertar o api", cands) is None


def test_tasks_appear_in_view_and_resolve(tmp_path):
    conn = dbm.connect(tmp_path / "t.db")
    mac, ec2 = seed_history(conn)
    config = Config(targets=[mac, ec2], settings=Settings())

    tid = add_task(conn, "no benji-dp, dividir rampids em ssps")
    view = build_view(config, conn)
    tasks = [r for r in view.scheduled if r.kind == "task"]
    assert len(tasks) == 1
    assert tasks[0].session_id == f"task:{tid}"

    # overdue task rises to PRA HOJE
    conn.execute("UPDATE tasks SET due_at = ? WHERE id = ?",
                 (dbm.now_ms() - 1000, tid))
    conn.commit()
    view = build_view(config, conn)
    assert any(r.kind == "task" for r in view.overdue)

    resolve_task(conn, tid)
    view = build_view(config, conn)
    assert not any(r.kind == "task" for r in view.overdue + view.scheduled)
