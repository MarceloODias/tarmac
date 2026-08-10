"""Race and bypass tests for the SessionEnd hook, from the cost audit.

The audit ran the real hook under concurrency and broke three of the four
guards. Each test below re-enacts one of those attacks:

- dedupe: `grep .seen` + `>> .seen` were not atomic → 30 simultaneous session
  ends produced up to 13 calls for ONE session.
- daily cap: read-modify-write without a lock → blew past MAX_DAY in 4 of 5
  rounds, up to 2x.
- deny-list: a payload without `cwd`, or a prefix written as `~/dir`, made
  every exclusion silently inert.
"""

import json
import os
import subprocess

import pytest

from test_hook import HOOK, make_fake_claude, run_hook, wait_for_settle


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    (home / ".tarmac").mkdir(parents=True)
    bin_dir.mkdir()
    return home, bin_dir, tmp_path / "calls.log"


def _spawn(hook_env, payloads):
    procs = [
        (payload, subprocess.Popen(
            ["bash", str(HOOK)], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=hook_env, text=True))
        for payload in payloads
    ]
    for payload, proc in procs:
        proc.communicate(json.dumps(payload))


def _env_for(home, bin_dir, **extra):
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "TARMAC_HOME": str(home / ".tarmac"),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    })
    env.update({k: str(v) for k, v in extra.items()})
    return env


def test_concurrent_hooks_never_double_spend_same_session(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    _spawn(_env_for(home, bin_dir),
           [{"session_id": "sess-race", "cwd": "/work/p"}] * 30)
    wait_for_settle(home, 8)
    calls = log.read_text().splitlines() if log.exists() else []
    assert len(calls) == 1, f"dedupe furado por corrida: {len(calls)} chamadas"


def test_concurrent_hooks_respect_the_daily_cap(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    _spawn(_env_for(home, bin_dir, TARMAC_NEXTSTEP_MAX_DAY=3),
           [{"session_id": f"s-{i}", "cwd": "/work/p"} for i in range(30)])
    wait_for_settle(home, 8)
    calls = log.read_text().splitlines() if log.exists() else []
    assert len(calls) <= 3, f"teto estourado: {len(calls)} chamadas (teto 3)"


def test_payload_without_cwd_is_denied_when_a_deny_list_exists(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-nocwd"},
             TARMAC_NEXTSTEP_EXCLUDE="/home/u/chatops")
    wait_for_settle(home, 3)
    assert not log.exists() or log.read_text().strip() == ""


def test_tilde_in_deny_prefix_is_expanded(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir,
             {"session_id": "s-tilde", "cwd": f"{home}/chatops/bot"},
             TARMAC_NEXTSTEP_EXCLUDE="~/chatops")
    wait_for_settle(home, 3)
    assert not log.exists() or log.read_text().strip() == ""


def test_allowed_dir_still_works_after_the_hardening(env):
    home, bin_dir, log = env
    make_fake_claude(bin_dir, log)
    run_hook(home, bin_dir, {"session_id": "s-ok", "cwd": "/work/project"},
             TARMAC_NEXTSTEP_EXCLUDE="/other")
    wait_for_settle(home, 4)
    assert len(log.read_text().splitlines()) == 1
