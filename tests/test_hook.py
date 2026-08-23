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


# --- hardening that used to live only in the installed copy -----------------
# These were fixed straight in ~/.tarmac and never in the repo, so every
# `tarmac hook install` quietly reinstalled the older, weaker script.

def test_deny_prefix_written_with_a_tilde_still_denies(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-t", "cwd": f"{home}/ai-agent-skills/Bot"},
             TARMAC_NEXTSTEP_EXCLUDE="~/ai-agent-skills")
    wait_for_settle(home, 3)
    assert not log.exists() or log.read_text().strip() == ""


def test_missing_cwd_cannot_slip_past_a_deny_list(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-nocwd"},
             TARMAC_NEXTSTEP_EXCLUDE="/home/u/ai-agent-skills")
    wait_for_settle(home, 3)
    assert not log.exists() or log.read_text().strip() == ""


def test_seen_file_stays_bounded(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    seen = home / ".tarmac" / "next-steps.seen"
    seen.write_text("".join(f"old-{i}\n" for i in range(2500)))
    run_hook(home, bin_dir, {"session_id": "s-new", "cwd": "/work/p"})
    wait_for_settle(home, 4)
    assert len(seen.read_text().splitlines()) <= 1001


# --- one queue line must say which session universe wrote it ---------------

def queue_entry(home: Path) -> dict:
    line = (home / ".tarmac" / "next-steps.jsonl").read_text().strip()
    return json.loads(line)


def test_entry_records_the_default_config_dir(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-cfg", "cwd": "/work/p"})
    wait_for_settle(home, 4)
    assert queue_entry(home)["config_dir"] == f"{home}/.claude"


def test_entry_records_the_account_that_ran_it(env):
    """Without this the collector cannot tell two accounts' entries apart."""
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-alt", "cwd": "/work/p"},
             CLAUDE_CONFIG_DIR=f"{home}/.claude-personal")
    wait_for_settle(home, 4)
    assert queue_entry(home)["config_dir"] == f"{home}/.claude-personal"


def test_a_tilde_in_the_env_is_expanded_before_it_is_recorded(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-tilde", "cwd": "/work/p"},
             CLAUDE_CONFIG_DIR="~/.claude-personal")
    wait_for_settle(home, 4)
    assert queue_entry(home)["config_dir"] == f"{home}/.claude-personal"


# --- installing per CLAUDE_CONFIG_DIR (a second account has its own) -------

def test_install_targets_one_config_dir_and_leaves_the_other_alone(tmp_path, monkeypatch):
    from tarmac import hookmgr
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path / ".tarmac"))
    main = tmp_path / ".claude"
    alt = tmp_path / ".claude-personal"

    hookmgr.install(config_dir=str(alt))
    assert hookmgr.status(str(alt))[0] is True
    assert hookmgr.status(str(main))[0] is False
    assert not (main / "settings.json").exists()

    hookmgr.install(config_dir=str(main))
    assert hookmgr.status(str(main))[0] is True

    assert hookmgr.uninstall(str(alt)) is True
    assert hookmgr.status(str(alt))[0] is False
    assert hookmgr.status(str(main))[0] is True  # the other one survives
    assert hookmgr.uninstall(str(alt)) is False


def test_install_preserves_foreign_hooks_in_that_settings_file(tmp_path, monkeypatch):
    from tarmac import hookmgr
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path / ".tarmac"))
    cfg = tmp_path / ".claude-personal"
    cfg.mkdir()
    other = {"hooks": {"SessionEnd": [{"hooks": [
        {"type": "command", "command": "/usr/local/bin/meu-script"}]}]}}
    (cfg / "settings.json").write_text(json.dumps(other))

    hookmgr.install(config_dir=str(cfg))
    data = json.loads((cfg / "settings.json").read_text())
    commands = [h["command"] for e in data["hooks"]["SessionEnd"] for h in e["hooks"]]
    assert "/usr/local/bin/meu-script" in commands
    assert any(hookmgr.SENTINEL in c for c in commands)

    hookmgr.uninstall(str(cfg))
    data = json.loads((cfg / "settings.json").read_text())
    commands = [h["command"] for e in data["hooks"]["SessionEnd"] for h in e["hooks"]]
    assert commands == ["/usr/local/bin/meu-script"]


def test_local_config_dirs_covers_every_account_on_this_machine():
    from tarmac.config import Config, Settings, Target
    from tarmac.hookmgr import local_config_dirs
    config = Config(settings=Settings(), targets=[
        Target(id="mac", transport="local", config_dir="~/.claude"),
        Target(id="mac-personal", transport="local", config_dir="~/.claude-personal"),
        Target(id="dupe", transport="local", config_dir="~/.claude"),
        Target(id="off", transport="local", config_dir="~/.claude-off", enabled=False),
        Target(id="ec2", transport="ssh", ssh_host="h", claude_bin="/bin/claude"),
    ])
    assert local_config_dirs(config) == ["~/.claude", "~/.claude-personal"]
    assert local_config_dirs(Config(settings=Settings(), targets=[])) == ["~/.claude"]


# --- an API error is not a pending note ------------------------------------
# Ten sessions carried "You've hit your individual spend limit · run
# /usage-credits…" as their note for two weeks: the hook read `result` without
# looking at `is_error`.

def make_refusing_claude(bin_dir: Path, calls_log: Path) -> None:
    (bin_dir / "claude").write_text(f'''#!/bin/bash
echo "call args=$*" >> "{calls_log}"
echo '{{"is_error": true, "subtype": "error_during_execution", "result": "You&#39;ve hit your individual spend limit · run /usage-credits to raise it"}}'
''')
    (bin_dir / "claude").chmod(0o755)


def test_a_refused_call_writes_nothing_to_the_queue(env):
    home, bin_dir, log = env
    make_refusing_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-refused", "cwd": "/work/p"})
    wait_for_settle(home, 4)
    queue = home / ".tarmac" / "next-steps.jsonl"
    assert log.exists(), "the call did happen"
    assert not queue.exists() or queue.read_text().strip() == "", \
        f"stored an API error as a pending note: {queue.read_text()!r}"


def test_a_refused_call_releases_the_dedupe_slot(env):
    """Otherwise that session can never get a real note: one shot, wasted."""
    home, bin_dir, log = env
    make_refusing_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-refused", "cwd": "/work/p"})
    wait_for_settle(home, 4)
    seen = home / ".tarmac" / "next-steps.seen"
    assert "s-refused" not in seen.read_text()

    # and the retry works
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-refused", "cwd": "/work/p"})
    wait_for_settle(home, 4)
    assert "s-refused" in seen.read_text()
    assert "revisar o diff" in (home / ".tarmac" / "next-steps.jsonl").read_text()
