"""End-to-end tests for the Stop hook that feeds next_step (SPEC §10).

The hook this replaced ran `claude -p --resume <id>`, which CONTINUES that
session, so finishing it fired the hook again with the same id — an infinite
feedback loop, each lap replaying the whole transcript. Four guards existed to
contain it. The `Stop` event carries `last_assistant_message`, so there is
nothing to ask a model and nothing to guard: the first test here is that no
process is spawned at all, which is what makes the other three guards
unnecessary rather than merely absent.
"""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

HOOK = Path(__file__).parent.parent / "hooks" / "stop-next-step.sh"


def run_hook(home: Path, bin_dir: Path, payload: dict, **env_extra) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "TARMAC_HOME": str(home / ".tarmac"),
        "PATH": f"{bin_dir}:{env['PATH']}",
    })
    for key in ("TARMAC_NEXTSTEP_ONLY", "TARMAC_NEXTSTEP_EXCLUDE"):
        env.pop(key, None)
    env.update({k: str(v) for k, v in env_extra.items()})
    return subprocess.run(
        ["bash", str(HOOK)], input=json.dumps(payload), text=True,
        env=env, capture_output=True, timeout=30,
    )


def queue_lines(home: Path) -> list[dict]:
    path = home / ".tarmac" / "next-steps.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def stop(session_id="s-1", message="Fixed the parser.", cwd="/work/project") -> dict:
    return {"session_id": session_id, "cwd": cwd, "hook_event_name": "Stop",
            "last_assistant_message": message}


@pytest.fixture
def env(tmp_path):
    """A home, plus a PATH whose every binary logs any call.

    The log is how "this hook spends nothing" is proven rather than asserted:
    if the hook ever shells out to claude again, the file appears.
    """
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    (home / ".tarmac").mkdir(parents=True)
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    for name in ("claude", "curl"):
        (bin_dir / name).write_text(
            f'#!/bin/bash\necho "{name} $*" >> "{calls}"\necho "{{}}"\n')
        (bin_dir / name).chmod(0o755)
    return home, bin_dir, calls


# ---------- the whole point: no model call, no loop to guard ----------

def test_the_note_costs_nothing(env):
    home, bin_dir, calls = env
    run_hook(home, bin_dir, stop(message="I stopped at the migration: "
                                        "the index still has to be rebuilt."))
    assert not calls.exists(), f"o hook gastou uma chamada: {calls.read_text()}"
    assert queue_lines(home)[0]["next_step"] == (
        "I stopped at the migration: the index still has to be rebuilt.")


def test_a_second_stop_from_the_same_session_just_writes_a_newer_note(env):
    """No dedupe file, and none needed: the collector upserts, last one wins."""
    home, bin_dir, _ = env
    run_hook(home, bin_dir, stop(message="first"))
    run_hook(home, bin_dir, stop(message="second"))
    assert [e["next_step"] for e in queue_lines(home)] == ["first", "second"]


def test_no_daily_cap_can_starve_a_session_of_its_note(env):
    home, bin_dir, _ = env
    for i in range(25):
        run_hook(home, bin_dir, stop(session_id=f"s-{i}", message=f"note {i}"))
    assert len(queue_lines(home)) == 25


def test_concurrent_stops_do_not_corrupt_the_queue(env):
    home, bin_dir, _ = env
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda i: run_hook(home, bin_dir, stop(session_id=f"c-{i}",
                                                   message=f"note {i}")),
            range(24),
        ))
    entries = queue_lines(home)  # json.loads on every line: a torn write fails here
    assert len({e["session_id"] for e in entries}) == 24


# ---------- what reaches the panel is one readable line ----------

def test_a_markdown_reply_becomes_one_line_without_its_code(env):
    home, bin_dir, _ = env
    run_hook(home, bin_dir, stop(message=(
        "## Done\n\n"
        "- rebuilt the index\n"
        "- **left**: the backfill\n\n"
        "```sql\nSELECT * FROM huge_table WHERE everything;\n```\n\n"
        "Run it when you can."
    )))
    note = queue_lines(home)[0]["next_step"]
    assert note == "Done rebuilt the index left: the backfill Run it when you can."
    assert "SELECT" not in note


def test_a_long_reply_is_cut_at_a_word_with_an_ellipsis(env):
    home, bin_dir, _ = env
    run_hook(home, bin_dir, stop(message="palavra " * 80))
    note = queue_lines(home)[0]["next_step"]
    assert len(note) <= 201 and note.endswith("…")
    assert not note.endswith("palav…"), "cortou no meio de uma palavra"


def test_a_stop_without_a_message_writes_nothing(env):
    """Not every Stop payload carries one, and an empty note is worse than none."""
    home, bin_dir, _ = env
    run_hook(home, bin_dir, {"session_id": "s-x", "cwd": "/work/p"})
    run_hook(home, bin_dir, stop(message="```\njust code\n```"))
    assert queue_lines(home) == []


def test_garbage_on_stdin_is_survived(env):
    home, bin_dir, _ = env
    proc = subprocess.run(
        ["bash", str(HOOK)], input="not json at all", text=True,
        env={**os.environ, "HOME": str(home), "TARMAC_HOME": str(home / ".tarmac")},
        capture_output=True, timeout=30,
    )
    assert proc.returncode == 0
    assert queue_lines(home) == []


# ---------- the two cwd lists (noise and privacy, no longer cost) ----------

def test_excluded_cwd_writes_nothing(env):
    home, bin_dir, _ = env
    run_hook(home, bin_dir, stop(cwd="/home/u/ai-agent-skills/Bot"),
             TARMAC_NEXTSTEP_EXCLUDE="/home/u/ai-agent-skills")
    assert queue_lines(home) == []


def test_deny_prefix_written_with_a_tilde_still_denies(env):
    home, bin_dir, _ = env
    run_hook(home, bin_dir, stop(cwd=f"{home}/ai-agent-skills/Bot"),
             TARMAC_NEXTSTEP_EXCLUDE="~/ai-agent-skills")
    assert queue_lines(home) == []


def test_missing_cwd_cannot_slip_past_a_deny_list(env):
    home, bin_dir, _ = env
    run_hook(home, bin_dir, {"session_id": "s-nocwd", "last_assistant_message": "x"},
             TARMAC_NEXTSTEP_EXCLUDE="/home/u/ai-agent-skills")
    assert queue_lines(home) == []


def test_allow_list_restricts_to_named_dirs(env):
    home, bin_dir, _ = env
    run_hook(home, bin_dir, stop(session_id="s-a", cwd="/other/place"),
             TARMAC_NEXTSTEP_ONLY="/work")
    assert queue_lines(home) == []
    run_hook(home, bin_dir, stop(session_id="s-b", cwd="/work/project"),
             TARMAC_NEXTSTEP_ONLY="/work")
    assert [e["session_id"] for e in queue_lines(home)] == ["s-b"]


# ---------- one queue line must say which session universe wrote it ----------

def test_entry_records_the_default_config_dir(env):
    home, bin_dir, _ = env
    run_hook(home, bin_dir, stop())
    assert queue_lines(home)[0]["config_dir"] == f"{home}/.claude"


def test_entry_records_the_account_that_ran_it(env):
    """Without this the collector cannot tell two accounts' entries apart."""
    home, bin_dir, _ = env
    run_hook(home, bin_dir, stop(), CLAUDE_CONFIG_DIR=f"{home}/.claude-personal")
    assert queue_lines(home)[0]["config_dir"] == f"{home}/.claude-personal"


def test_a_tilde_in_the_env_is_expanded_before_it_is_recorded(env):
    home, bin_dir, _ = env
    run_hook(home, bin_dir, stop(), CLAUDE_CONFIG_DIR="~/.claude-personal")
    assert queue_lines(home)[0]["config_dir"] == f"{home}/.claude-personal"


# ---------- installing: per CLAUDE_CONFIG_DIR, and the legacy hook goes ------

def test_install_targets_one_config_dir_and_leaves_the_other_alone(tmp_path, monkeypatch):
    from tarmac import hookmgr
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path / ".tarmac"))
    main = tmp_path / ".claude"
    alt = tmp_path / ".claude-personal"

    hookmgr.install(config_dir=str(alt))
    assert set(hookmgr.status(str(alt))) == set(hookmgr.SPECS)
    assert hookmgr.status(str(main)) == {}
    assert not (main / "settings.json").exists()

    hookmgr.install(config_dir=str(main))
    assert set(hookmgr.status(str(main))) == set(hookmgr.SPECS)

    assert hookmgr.uninstall(config_dir=str(alt))
    assert hookmgr.status(str(alt)) == {}
    assert set(hookmgr.status(str(main))) == set(hookmgr.SPECS)  # the other survives
    assert hookmgr.uninstall(config_dir=str(alt)) == []


def test_installing_one_hook_leaves_the_other_alone(tmp_path, monkeypatch):
    from tarmac import hookmgr
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path / ".tarmac"))
    cfg = tmp_path / ".claude"
    hookmgr.install(["needs-you"], config_dir=str(cfg))
    assert list(hookmgr.status(str(cfg))) == ["needs-you"]
    hookmgr.install(["next-step"], config_dir=str(cfg))
    assert set(hookmgr.status(str(cfg))) == {"needs-you", "next-step"}
    hookmgr.uninstall(["needs-you"], config_dir=str(cfg))
    assert list(hookmgr.status(str(cfg))) == ["next-step"]


def test_installing_twice_does_not_duplicate_the_entry(tmp_path, monkeypatch):
    from tarmac import hookmgr
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path / ".tarmac"))
    cfg = tmp_path / ".claude"
    hookmgr.install(config_dir=str(cfg))
    hookmgr.install(config_dir=str(cfg))
    data = json.loads((cfg / "settings.json").read_text())
    assert len(data["hooks"]["Stop"]) == 1
    assert len(data["hooks"]["Notification"]) == 1


def test_install_preserves_foreign_hooks_in_that_settings_file(tmp_path, monkeypatch):
    from tarmac import hookmgr
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path / ".tarmac"))
    cfg = tmp_path / ".claude-personal"
    cfg.mkdir()
    other = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "/usr/local/bin/meu-script"}]}]}}
    (cfg / "settings.json").write_text(json.dumps(other))

    hookmgr.install(config_dir=str(cfg))
    data = json.loads((cfg / "settings.json").read_text())
    commands = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
    assert "/usr/local/bin/meu-script" in commands
    assert any(hookmgr.NEXT_STEP.sentinel in c for c in commands)

    hookmgr.uninstall(config_dir=str(cfg))
    data = json.loads((cfg / "settings.json").read_text())
    commands = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
    assert commands == ["/usr/local/bin/meu-script"]


def test_installing_removes_the_legacy_sessionend_hook(tmp_path, monkeypatch):
    """The one that spent money. An upgrade that left it behind would keep it
    running: its script is still sitting in ~/.tarmac from the old install."""
    from tarmac import hookmgr
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path / ".tarmac"))
    cfg = tmp_path / ".claude"
    cfg.mkdir()
    (cfg / "settings.json").write_text(json.dumps({"hooks": {"SessionEnd": [
        {"hooks": [{"type": "command",
                    "command": 'TARMAC_NEXTSTEP_MAX_DAY=20 "$HOME/.tarmac/session-end-next-step.sh"'}]}]}}))
    assert "legacy" in hookmgr.status(str(cfg))

    hookmgr.install(config_dir=str(cfg))
    assert "legacy" not in hookmgr.status(str(cfg))
    data = json.loads((cfg / "settings.json").read_text())
    assert "SessionEnd" not in data["hooks"]


def test_uninstall_removes_the_legacy_hook_too(tmp_path, monkeypatch):
    from tarmac import hookmgr
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path / ".tarmac"))
    cfg = tmp_path / ".claude"
    cfg.mkdir()
    (cfg / "settings.json").write_text(json.dumps({"hooks": {"SessionEnd": [
        {"hooks": [{"type": "command",
                    "command": '"$HOME/.tarmac/session-end-next-step.sh"'}]}]}}))
    assert hookmgr.uninstall(config_dir=str(cfg)) == ["legacy"]
    assert hookmgr.status(str(cfg)) == {}


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


# --- the hooks have to be installable from the INSTALLED binary ------------
# `tarmac hook install` looked for ../hooks/ relative to the package, which
# only exists in a checkout: from ~/.local/bin/tarmac it died with
# FileNotFoundError, so the panel could never update its own hook.

def test_hook_source_is_found_the_way_the_wheel_ships_it(tmp_path, monkeypatch):
    from tarmac import hookmgr
    packaged = tmp_path / "tarmac" / "hooks"
    packaged.mkdir(parents=True)
    (packaged / hookmgr.NEXT_STEP.script).write_text("#!/bin/bash\n")
    monkeypatch.setattr(hookmgr, "__file__", str(tmp_path / "tarmac" / "hookmgr.py"))
    assert hookmgr.hook_source(hookmgr.NEXT_STEP) == packaged / hookmgr.NEXT_STEP.script


def test_every_shipped_hook_exists_in_the_checkout():
    from tarmac import hookmgr
    for spec in hookmgr.SPECS.values():
        assert hookmgr.hook_source(spec).is_file()
