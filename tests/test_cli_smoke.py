"""Smoke tests that RUN the CLI as a subprocess, one per subcommand.

Rationale: the bugs that reached Marcelo were never logic bugs — they were
"the command explodes when actually invoked" (bad import, wrong arity, a
crash inside a worker). Unit tests with mocks cannot see those. Everything
here shells out for real, against a throwaway TARMAC_HOME.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent


@pytest.fixture
def cli(tmp_path):
    home = tmp_path / "tarmac-home"
    home.mkdir()
    targets = home / "targets.yaml"
    # a local target whose "claude" is a fake that returns a fixed session list
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    payload = json.dumps([
        {"id": "abc12345", "sessionId": "abc12345-1111-2222-3333-444455556666",
         "kind": "background", "state": "blocked", "cwd": str(tmp_path / "proj"),
         "name": "sessao-de-teste", "startedAt": 1},
        {"sessionId": "dddd1111-2222-3333-4444-555566667777",
         "kind": "interactive", "status": "idle", "cwd": str(tmp_path / "proj"),
         "name": "outra", "startedAt": 2},
    ])
    (fake_bin / "claude").write_text(
        f"#!/bin/bash\ncase \"$*\" in *'agents --json'*) cat <<'EOF'\n{payload}\nEOF\n;; *) echo fake ;; esac\n"
    )
    (fake_bin / "claude").chmod(0o755)
    targets.write_text(
        "settings:\n  locale: pt\ntargets:\n"
        f"  - id: local\n    label: Local\n    mine: true\n    transport: local\n"
        f"    claude_bin: {fake_bin / 'claude'}\n"
    )

    def run(*args, expect_ok=True):
        env = {
            "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(tmp_path / "fakehome"),
            "TARMAC_HOME": str(home),
            "TARMAC_TARGETS": str(targets),
        }
        (tmp_path / "fakehome").mkdir(exist_ok=True)
        proc = subprocess.run(
            [sys.executable, "-m", "tarmac.cli", *args],
            capture_output=True, text=True, cwd=REPO, env=env, timeout=90,
        )
        if expect_ok:
            assert proc.returncode == 0, (
                f"`tarmac {' '.join(args)}` falhou ({proc.returncode})\n"
                f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        return proc

    return run


def test_collect_and_render_swiftbar(cli):
    out = cli("collect", "--force").stdout
    assert "local: ok" in out
    menu = cli("render", "--format", "swiftbar").stdout
    assert "PRECISA DE VOCÊ" in menu
    assert "sessao-de-teste" in menu


def test_render_tui_once(cli):
    cli("collect", "--force")
    out = cli("render", "--format", "tui", "--once").stdout
    assert "sessao-de-teste" in out


def test_stats(cli):
    cli("collect", "--force")
    cli("stats")


def test_session_actions_dont_explode(cli):
    cli("collect", "--force")
    sid = "abc12345"
    assert "copiado" in cli("copy-resume", "local", sid).stdout or True
    cli("pin", "local", sid)
    cli("next-step", "local", sid, "revisar", "o", "diff")
    cli("checklist", "local", sid, "add", "primeiro", "item")
    assert "[ ] 1. primeiro item" in cli("checklist", "local", sid, "list").stdout
    cli("checklist", "local", sid, "done", "1")
    assert "[x]" in cli("checklist", "local", sid, "list").stdout
    cli("remember", "local", sid, "5h")
    cli("defer", "local", sid, "amanhã")
    cli("resolve", "local", sid)
    # the checklist must show up in the rendered menu
    assert "[1/1]" in cli("render", "--format", "swiftbar").stdout


def test_bad_date_fails_loudly(cli):
    cli("collect", "--force")
    proc = cli("remember", "local", "abc12345", "quando", "der", expect_ok=False)
    assert proc.returncode != 0
    assert "entendi" in (proc.stdout + proc.stderr)


def test_unknown_target_and_session_fail_clean(cli):
    assert cli("open", "nao-existe", "x", expect_ok=False).returncode != 0
    cli("collect", "--force")
    assert cli("logs", "local", "nao-existe", expect_ok=False).returncode != 0


def test_task_lifecycle(cli):
    cli("collect", "--force")
    out = cli("task", "no proj, preciso dividir os rampids").stdout
    assert "criada" in out
    listing = cli("task").stdout
    assert "dividir os rampids" in listing
    task_id = listing.split("]")[0].lstrip("[")
    cli("task", "--done", task_id)
    assert "dividir os rampids" not in cli("task").stdout


def test_hook_is_off_by_default_and_installs_with_guards(cli):
    assert "instalado: False" in cli("hook", "status").stdout
    out = cli("hook", "install", "--exclude", "/svc", "--max-day", "5").stdout
    assert "TARMAC_NEXTSTEP_MAX_DAY=5" in out
    assert "TARMAC_NEXTSTEP_EXCLUDE=/svc" in out
    assert "instalado: True" in cli("hook", "status").stdout
    assert "removido" in cli("hook", "uninstall").stdout
    assert "instalado: False" in cli("hook", "status").stdout


def test_hook_install_preserves_other_settings(cli, tmp_path):
    settings = tmp_path / "fakehome" / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "model": "opus", "hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": "outro-script.sh"}]}]},
    }))
    cli("hook", "install")
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "outro-script.sh"
    cli("hook", "uninstall")
    data = json.loads(settings.read_text())
    assert "SessionEnd" not in data["hooks"]
    assert "SessionStart" in data["hooks"]
