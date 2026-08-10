"""End-to-end tests for the SessionEnd hook, with a fake `claude` binary.

These exist because a unit test never would have caught the real bug: the hook
called `claude -p --resume <id>`, which CONTINUES that session, so finishing it
fired SessionEnd again with the same id — an infinite feedback loop, each lap
replaying the whole transcript. The fake claude below re-enacts exactly that
recursion, so the guards are proven, not assumed.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

HOOK = Path(__file__).parent.parent / "hooks" / "session-end-next-step.sh"


def make_fake_claude(bin_dir: Path, calls_log: Path, recurse_as: str | None = None) -> None:
    """A `claude` that answers like the real one AND, like the real one, causes
    a nested SessionEnd for the session it resumed."""
    recursion = ""
    if recurse_as is not None:
        recursion = f'''
printf '%s' '{{"session_id": "{recurse_as}", "cwd": "/work/project"}}' | "{HOOK}" || true
'''
    (bin_dir / "claude").write_text(f'''#!/bin/bash
echo "call guard=${{TARMAC_HOOK_GUARD:-unset}} args=$*" >> "{calls_log}"
{recursion}
echo '{{"result": "pendente: revisar o diff"}}'
''')
    (bin_dir / "claude").chmod(0o755)


def run_hook(home: Path, bin_dir: Path, payload: dict, **env_extra) -> None:
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "TARMAC_HOME": str(home / ".tarmac"),
        "PATH": f"{bin_dir}:{env['PATH']}",
    })
    env.update({k: str(v) for k, v in env_extra.items()})
    subprocess.run(
        ["bash", str(HOOK)], input=json.dumps(payload), text=True,
        env=env, capture_output=True, timeout=30,
    )


def wait_for_settle(home: Path, seconds: float = 6.0) -> None:
    """The hook detaches the API call; give it time, then let it finish."""
    queue = home / ".tarmac" / "next-steps.jsonl"
    deadline = time.time() + seconds
    while time.time() < deadline:
        if queue.exists() and queue.read_text().strip():
            time.sleep(0.5)  # let any (buggy) recursion pile up too
            return
        time.sleep(0.2)


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    (home / ".tarmac").mkdir(parents=True)
    bin_dir.mkdir()
    return home, bin_dir, tmp_path / "calls.log"


def test_recursion_with_same_id_produces_exactly_one_call(env):
    # THE regression: resumed session ends -> hook fires again with same id
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log, recurse_as="sess-1")
    run_hook(home, bin_dir, {"session_id": "sess-1", "cwd": "/work/project"})
    wait_for_settle(home)

    calls = log.read_text().splitlines() if log.exists() else []
    assert len(calls) == 1, f"loop de realimentação: {len(calls)} chamadas\n{calls}"
    entries = (home / ".tarmac" / "next-steps.jsonl").read_text().strip().splitlines()
    assert len(entries) == 1


def test_recursion_with_new_id_is_stopped_by_env_guard(env):
    # if the resumed session got a NEW id, dedupe wouldn't help — the exported
    # TARMAC_HOOK_GUARD must be what stops it
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log, recurse_as="sess-child-999")
    run_hook(home, bin_dir, {"session_id": "sess-1", "cwd": "/work/project"})
    wait_for_settle(home)

    calls = log.read_text().splitlines()
    assert len(calls) == 1, f"guard não segurou: {calls}"
    # the guard is what the child inherits — it must be set on the API call
    assert "guard=1" in calls[0], "guard precisa ser exportado para o processo claude"


def test_same_session_never_summarised_twice(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    for _ in range(3):
        run_hook(home, bin_dir, {"session_id": "sess-1", "cwd": "/work/project"})
        wait_for_settle(home, 3)
    assert len(log.read_text().splitlines()) == 1


def test_daily_cap_stops_spending(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    for i in range(5):
        run_hook(home, bin_dir, {"session_id": f"s-{i}", "cwd": "/work/p"},
                 TARMAC_NEXTSTEP_MAX_DAY=2)
        wait_for_settle(home, 3)
    assert len(log.read_text().splitlines()) == 2


def test_excluded_cwd_spends_nothing(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-x", "cwd": "/home/u/ai-agent-skills/Bot"},
             TARMAC_NEXTSTEP_EXCLUDE="/home/u/ai-agent-skills")
    wait_for_settle(home, 3)
    assert not log.exists() or log.read_text().strip() == ""


def test_allow_list_restricts_to_named_dirs(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-a", "cwd": "/other/place"},
             TARMAC_NEXTSTEP_ONLY="/work")
    wait_for_settle(home, 3)
    assert not log.exists() or log.read_text().strip() == ""

    run_hook(home, bin_dir, {"session_id": "s-b", "cwd": "/work/project"},
             TARMAC_NEXTSTEP_ONLY="/work")
    wait_for_settle(home, 4)
    assert len(log.read_text().splitlines()) == 1


def test_uses_cheap_model_by_default(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-m", "cwd": "/work/p"})
    wait_for_settle(home, 4)
    assert "haiku" in log.read_text()
