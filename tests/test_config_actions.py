"""Config validation, command building (§4.2, §9.1) and the no-JSONL rule (§2)."""

import subprocess
from pathlib import Path

import pytest

from tarmac.actions import attach_command, resume_command
from tarmac.collect import build_command
from tarmac.config import Target, load_config
from tarmac.derive import Row


def make_row(**kw) -> Row:
    defaults = dict(
        target_id="t", target_label="T", session_id="abc12345",
        display_name="x", eff_state="blocked", kind="background",
        cwd="/x", short_id="abc12345", uuid="abc12345-1111-2222-3333-444455556666",
        waiting_for=None, gone=False, stale=False,
    )
    defaults.update(kw)
    return Row(**defaults)


def test_local_collect_command():
    t = Target(id="mac", transport="local")
    cmd = build_command(t)
    assert cmd[-1] == "claude agents --json --all"


def test_local_collect_command_with_config_dir():
    t = Target(id="mac", transport="local", config_dir="~/.claude-alt")
    assert "CLAUDE_CONFIG_DIR" in build_command(t)[-1]


def test_ssh_collect_command():
    t = Target(id="ec2", transport="ssh", ssh_host="ec2-runner",
               claude_bin="/home/u/.local/bin/claude")
    cmd = build_command(t)
    assert cmd[0] == "ssh"
    assert "BatchMode=yes" in cmd
    assert "ConnectTimeout=5" in cmd
    assert cmd[-1] == "/home/u/.local/bin/claude agents --json --all"


def test_ssh_user_prefix():
    t = Target(id="ec2", transport="ssh", ssh_host="host", ssh_user="frank",
               claude_bin="/bin/claude")
    assert "frank@host" in build_command(t)


def test_attach_uses_short_id_then_uuid():
    t = Target(id="ec2", transport="ssh", ssh_host="h", claude_bin="/bin/claude")
    assert "attach abc12345" in attach_command(t, make_row())
    assert attach_command(t, make_row()).startswith("ssh -t ")  # -t mandatory
    no_short = make_row(short_id=None)
    assert "--resume abc12345-1111" in attach_command(t, no_short)
    with pytest.raises(ValueError):
        attach_command(t, make_row(short_id=None, uuid=None))


def test_resume_command_uses_uuid():
    t = Target(id="mac", transport="local")
    assert resume_command(t, make_row()) == \
        "claude --resume abc12345-1111-2222-3333-444455556666"


def test_config_validation(tmp_path):
    bad = tmp_path / "targets.yaml"
    bad.write_text("targets:\n  - id: x\n    transport: ssh\n    ssh_host: h\n")
    with pytest.raises(ValueError, match="claude_bin"):
        load_config(bad)
    dup = tmp_path / "dup.yaml"
    dup.write_text("targets:\n  - id: a\n  - id: a\n")
    with pytest.raises(ValueError, match="duplicados"):
        load_config(dup)


def test_missing_config_gives_implicit_local(tmp_path):
    config = load_config(tmp_path / "nope.yaml")
    assert len(config.targets) == 1
    assert config.targets[0].transport == "local"


def test_no_code_path_reads_claude_projects():
    # SPEC §13 2: nothing in the codebase touches ~/.claude/projects/ or the
    # jobs state files — the --json is the only source of truth.
    pkg = Path(__file__).parent.parent / "tarmac"
    grep = subprocess.run(
        ["grep", "-rn", "--include=*.py",
         "-e", ".claude/projects", "-e", "state.json", str(pkg)],
        capture_output=True, text=True,
    )
    assert grep.stdout == "", f"código lendo fonte proibida:\n{grep.stdout}"
