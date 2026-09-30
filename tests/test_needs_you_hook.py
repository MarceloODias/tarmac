"""Push alert on entering NEEDS YOU: the Notification hook and `tarmac poke`.

The alert used to depend on the collect cycle: a session that blocked one
second after a cycle waited up to 60s to be announced, and with the panel
closed it was never announced at all. Claude Code fires `Notification` the
moment a session needs a human, so the machine can say so itself.

The design this file has to hold to its promises:

  * the hook DECIDES NOTHING. It asks tarmac to collect, and the ordinary claim,
    mute and service/mine filters decide what is announced — so a hook that
    fires twice, or fires for something the panel would not alert on, still
    produces exactly the alerts the 60s cycle would have produced.
  * it never holds up the session that fired it.
  * it never drags an ssh target (15s timeout) into a path meant to alert in
    seconds.

Everything below runs the real script and the real binary, with only the
outside world (claude, ssh, osascript) faked at the process boundary — the
layer where every bug that reached Marcelo lived.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
HOOK = REPO / "hooks" / "notification-needs-you.sh"

darwin_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="notificação do macOS")


# ============================ the hook script ==============================

@pytest.fixture
def hook_env(tmp_path):
    """A fake `tarmac` that logs every invocation, plus a runner for the hook."""
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    log = tmp_path / "poke.log"

    def fake_tarmac(body: str = "") -> Path:
        path = bin_dir / "tarmac"
        path.write_text(f'#!/bin/bash\n{body}echo "$*" >> "{log}"\n')
        path.chmod(0o755)
        return path

    def run(notification_type: str | None = "permission_prompt", *,
            bin_on_path: bool = False, **env_extra) -> subprocess.CompletedProcess:
        payload = {"session_id": "s-1", "cwd": "/work/p",
                   "hook_event_name": "Notification",
                   "message": "Claude needs your permission"}
        if notification_type is not None:
            payload["notification_type"] = notification_type
        env = dict(os.environ)
        env.update({
            "HOME": str(home),
            # PATH without the fake bin unless the test asks: TARMAC_BIN is the
            # normal path, and a hook that only works from a login PATH is the
            # bug that once marked every row (stale)
            "PATH": f"{bin_dir}:{env['PATH']}" if bin_on_path else "/usr/bin:/bin",
        })
        env.pop("TARMAC_BIN", None)
        env.update({k: str(v) for k, v in env_extra.items()})
        return subprocess.run(
            ["bash", str(HOOK)], input=json.dumps(payload), text=True,
            env=env, capture_output=True, timeout=30,
        )

    def pokes(wait: float = 2.0) -> list[str]:
        deadline = time.time() + wait
        while time.time() < deadline:
            if log.exists() and log.read_text().strip():
                break
            time.sleep(0.05)
        return log.read_text().split() if log.exists() else []

    return type("HookEnv", (), dict(
        run=staticmethod(run), pokes=staticmethod(pokes),
        fake_tarmac=staticmethod(fake_tarmac), log=log, bin_dir=bin_dir))


@pytest.mark.parametrize("kind", [
    "permission_prompt", "agent_needs_input",
    "elicitation_dialog", "elicitation_url_dialog",
])
def test_every_type_that_means_a_human_is_waiting_pokes(hook_env, kind):
    binary = hook_env.fake_tarmac()
    assert hook_env.run(kind, TARMAC_BIN=binary).returncode == 0
    assert hook_env.pokes()[:1] == ["poke"]


@pytest.mark.parametrize("kind", ["idle_prompt", "auth_success", "quota_auto_resume_fired"])
def test_types_that_do_not_mean_that_are_ignored(hook_env, kind):
    """`idle_prompt` fires 60s after every turn of every session. Poking on it
    would poll the machine forever for sessions that are merely idle."""
    binary = hook_env.fake_tarmac()
    assert hook_env.run(kind, TARMAC_BIN=binary).returncode == 0
    time.sleep(0.4)
    assert hook_env.pokes(0) == []


def test_a_payload_without_a_type_still_pokes(hook_env):
    """A hand-installed entry (how remote hosts get hooks) has no matcher, and
    an unreadable payload must fail towards alerting, not towards silence."""
    binary = hook_env.fake_tarmac()
    hook_env.run(None, TARMAC_BIN=binary)
    assert hook_env.pokes()[:1] == ["poke"]


def test_the_hook_returns_immediately_even_if_the_poke_is_slow(hook_env):
    """A hook that blocks blocks the session that fired it."""
    binary = hook_env.fake_tarmac(body="sleep 5\n")
    started = time.time()
    hook_env.run(TARMAC_BIN=binary)
    assert time.time() - started < 2.0


def test_it_pokes_again_a_few_seconds_later(hook_env):
    """`agent_needs_input` can beat the listing by a moment: the second poke
    closes that window, and the claim makes it silent when the first alerted."""
    binary = hook_env.fake_tarmac()
    hook_env.run("agent_needs_input", TARMAC_BIN=binary)

    def so_far() -> int:
        return hook_env.log.read_text().split().count("poke") if hook_env.log.exists() else 0

    deadline = time.time() + 8
    while time.time() < deadline and so_far() < 2:
        time.sleep(0.2)
    assert so_far() == 2


def test_the_binary_is_found_on_path_when_no_TARMAC_BIN_was_baked_in(hook_env):
    hook_env.fake_tarmac()
    hook_env.run(bin_on_path=True)
    assert hook_env.pokes()[:1] == ["poke"]


def test_a_stale_TARMAC_BIN_falls_back_instead_of_dying(hook_env):
    hook_env.fake_tarmac()
    hook_env.run(bin_on_path=True, TARMAC_BIN="/nowhere/tarmac")
    assert hook_env.pokes()[:1] == ["poke"]


def test_no_tarmac_anywhere_is_a_quiet_exit_zero(hook_env):
    proc = hook_env.run()
    assert proc.returncode == 0
    assert hook_env.pokes(0.5) == []


# ============================== installing =================================

def test_install_writes_the_matcher_with_every_waiting_type(tmp_path, monkeypatch):
    from tarmac import hookmgr
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path / ".tarmac"))
    monkeypatch.setenv("TARMAC_BIN", "/opt/tarmac")
    cfg = tmp_path / ".claude"

    hookmgr.install(["needs-you"], config_dir=str(cfg))
    entries = json.loads((cfg / "settings.json").read_text())["hooks"]["Notification"]
    assert len(entries) == 1
    assert set(entries[0]["matcher"].split("|")) == set(hookmgr.NEEDS_YOU_TYPES)
    assert "idle_prompt" not in entries[0]["matcher"]
    # the hook runs without a login PATH: the binary goes in absolute
    assert "TARMAC_BIN=/opt/tarmac" in entries[0]["hooks"][0]["command"]


def test_the_installed_script_is_the_one_this_repo_ships(tmp_path, monkeypatch):
    from tarmac import hookmgr
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path / ".tarmac"))
    hookmgr.install(["needs-you"], config_dir=str(tmp_path / ".claude"))
    installed = hookmgr.hook_script_path(hookmgr.NEEDS_YOU)
    assert installed.read_text() == HOOK.read_text()
    assert os.access(installed, os.X_OK)


# ========================== poke, end to end ===============================

def write_targets(path: Path, fake_claude: Path, fake_ssh_bin: Path) -> None:
    path.write_text(
        "settings:\n  locale: en\n  notify: true\ntargets:\n"
        f"  - id: local\n    label: Mac\n    mine: true\n    transport: local\n"
        f"    claude_bin: {fake_claude}\n"
        f"  - id: remote\n    label: EC2\n    mine: true\n    transport: ssh\n"
        f"    ssh_host: somewhere\n    claude_bin: /bin/claude\n"
    )


def session(state: str) -> str:
    return json.dumps([{
        "id": "abc12345", "sessionId": "abc12345-1111-2222-3333-444455556666",
        "kind": "background", "state": state, "cwd": "/work/proj",
        "name": "a-refatorar", "startedAt": 1,
    }])


@pytest.fixture
def poked(tmp_path):
    """A real `tarmac` subprocess against fake claude / ssh / osascript."""
    home = tmp_path / "tarmac-home"
    fakehome = tmp_path / "fakehome"
    bin_dir = tmp_path / "bin"
    for d in (home, fakehome, bin_dir):
        d.mkdir()
    state = tmp_path / "agents.json"
    state.write_text(session("working"))
    alerts = tmp_path / "osascript.log"
    ssh_log = tmp_path / "ssh.log"

    claude = bin_dir / "claude"
    claude.write_text(
        f'#!/bin/bash\ncase "$*" in *"agents --json"*) cat "{state}" ;; '
        '*) echo fake ;; esac\n')
    claude.chmod(0o755)
    ssh = bin_dir / "ssh"
    ssh.write_text(f'#!/bin/bash\necho "$*" >> "{ssh_log}"\necho "[]"\n')
    ssh.chmod(0o755)
    osascript = bin_dir / "osascript"
    osascript.write_text(f'#!/bin/bash\necho "$*" >> "{alerts}"\n')
    osascript.chmod(0o755)

    targets = home / "targets.yaml"
    write_targets(targets, claude, ssh)

    def run(*args) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(fakehome),
            "TARMAC_HOME": str(home),
            "TARMAC_TARGETS": str(targets),
            "TARMAC_OSASCRIPT": str(osascript),
        }
        proc = subprocess.run(
            [sys.executable, "-m", "tarmac.cli", *args],
            capture_output=True, text=True, cwd=REPO, env=env, timeout=90,
        )
        assert proc.returncode == 0, f"tarmac {args}: {proc.stdout}{proc.stderr}"
        return proc

    def alerts_sent() -> list[str]:
        return alerts.read_text().splitlines() if alerts.exists() else []

    return type("Poked", (), dict(
        run=staticmethod(run), alerts=staticmethod(alerts_sent),
        state=state, ssh_log=ssh_log, home=home))


@darwin_only
def test_poke_alerts_the_moment_a_session_blocks(poked):
    poked.run("collect", "--force")            # warm: a cold target is silent
    assert poked.alerts() == []

    poked.state.write_text(session("blocked"))
    poked.run("poke")
    assert len(poked.alerts()) == 1, poked.alerts()
    assert "a-refatorar" in poked.alerts()[0]


@darwin_only
def test_a_second_poke_on_the_same_episode_is_silent(poked):
    """The hook pokes twice by design, and Claude Code can fire more than once."""
    poked.run("collect", "--force")
    poked.state.write_text(session("blocked"))
    poked.run("poke")
    poked.run("poke")
    poked.run("poke")
    assert len(poked.alerts()) == 1


@darwin_only
def test_a_muted_panel_stays_muted_when_poked(poked):
    poked.run("collect", "--force")
    poked.run("notify", "off")
    poked.state.write_text(session("blocked"))
    poked.run("poke")
    assert poked.alerts() == []
    # and unmuting does not retroactively fire it: the episode is claimed only
    # when it is announced, so the next real block still alerts
    poked.run("notify", "on")
    poked.state.write_text(session("working"))
    poked.run("poke")
    poked.state.write_text(session("blocked"))
    poked.run("poke")
    assert len(poked.alerts()) == 1


def test_poke_never_touches_an_ssh_target(poked):
    """15s per ssh target has no place in a path that has to alert in seconds."""
    poked.run("poke")
    assert not poked.ssh_log.exists(), poked.ssh_log.read_text()
    # ... while a full collect does read it
    poked.run("collect", "--force")
    assert poked.ssh_log.exists()


def test_poke_leaves_the_data_fresh_for_the_panel(poked):
    """It is a real collect, not a side channel: the panel's next cycle sees it."""
    poked.state.write_text(session("blocked"))
    poked.run("poke")
    out = poked.run("render", "--format", "swiftbar").stdout
    assert "a-refatorar" in out


@darwin_only
def test_two_pokes_racing_on_the_same_transition_alert_once(poked):
    """The hook fires per session, so a machine where two sessions block at the
    same second runs two pokes at the same second — over the same database,
    over the same state change. What is asserted is the OUTCOME: one alert, and
    neither poke reporting the target as failing (a collect that loses a write
    race must not go down as "this target is broken").

    Which of the two mechanisms produced that outcome is not this test's
    business: in practice SQLite serialises the two transactions and only one
    of them ever sees the state change. The claim is the belt underneath, and
    it is covered directly in test_notify.py.
    """
    from concurrent.futures import ThreadPoolExecutor

    poked.run("collect", "--force")
    poked.state.write_text(session("blocked"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        outs = [f.result().stdout for f in
                [pool.submit(poked.run, "poke"), pool.submit(poked.run, "poke")]]
    assert len(poked.alerts()) == 1, poked.alerts()
    assert all("local: ok" in out for out in outs), outs


def test_the_command_names_the_stable_binary_not_the_installer_path(tmp_path, monkeypatch):
    """`~/.local/bin/tarmac` is a symlink into whatever directory the installer
    used this week; resolving it wrote that private path into settings.json."""
    from tarmac import hookmgr
    monkeypatch.delenv("TARMAC_BIN", raising=False)
    real = tmp_path / "uv" / "tools" / "tarmac" / "bin" / "tarmac"
    real.parent.mkdir(parents=True)
    real.write_text("#!/bin/bash\n")
    real.chmod(0o755)
    stable_dir = tmp_path / "local" / "bin"
    stable_dir.mkdir(parents=True)
    (stable_dir / "tarmac").symlink_to(real)
    monkeypatch.setenv("PATH", f"{stable_dir}:{os.environ['PATH']}")

    assert hookmgr.tarmac_bin() == str(stable_dir / "tarmac")
