"""Install/uninstall the SessionEnd next_step hook (SPEC §10).

Off by default, and it stays off until asked for explicitly: the naive version
of this hook fed itself in a loop and burned a usage limit (see hooks/ header
and DECISIONS #26). Installing always writes the guards into the command line.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import DEFAULT_CONFIG_DIR, Config, tarmac_home

HOOK_NAME = "session-end-next-step.sh"
SENTINEL = HOOK_NAME  # how we recognise our own hook entry


def hook_script_path() -> Path:
    return tarmac_home() / HOOK_NAME


def install_script() -> Path:
    """Copy the shipped hook next to the DB so it survives the repo moving."""
    src = Path(__file__).parent.parent / "hooks" / HOOK_NAME
    dst = hook_script_path()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    dst.chmod(0o755)
    return dst


def build_command(exclude: str = "", only: str = "", max_day: int = 20,
                  model: str = "") -> str:
    env = []
    if exclude:
        env.append(f"TARMAC_NEXTSTEP_EXCLUDE={exclude}")
    if only:
        env.append(f"TARMAC_NEXTSTEP_ONLY={only}")
    env.append(f"TARMAC_NEXTSTEP_MAX_DAY={max_day}")
    if model:
        env.append(f"TARMAC_NEXTSTEP_MODEL={model}")
    prefix = " ".join(env)
    return f'{prefix} "$HOME/.tarmac/{HOOK_NAME}"'.strip()


def _settings_path(config_dir: str = DEFAULT_CONFIG_DIR) -> Path:
    """settings.json of ONE session universe.

    The hook has to be installed per CLAUDE_CONFIG_DIR: a second account on the
    same machine reads its own settings.json, so installing only in ~/.claude
    leaves that account with no hook and no sign of one.
    """
    return Path(config_dir or DEFAULT_CONFIG_DIR).expanduser() / "settings.json"


def local_config_dirs(config: Config) -> list[str]:
    """Every session universe on THIS machine, in config order."""
    dirs: list[str] = []
    for t in config.enabled_targets():
        if t.transport != "local":
            continue
        cfg = t.config_dir or DEFAULT_CONFIG_DIR
        if cfg not in dirs:
            dirs.append(cfg)
    return dirs or [DEFAULT_CONFIG_DIR]


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f) or {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_suffix(".json.tarmac-bak")
    if path.exists():
        shutil.copyfile(path, backup)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.replace(path)


def status(config_dir: str = DEFAULT_CONFIG_DIR) -> tuple[bool, str]:
    data = _load(_settings_path(config_dir))
    for entry in data.get("hooks", {}).get("SessionEnd", []):
        for h in entry.get("hooks", []):
            if SENTINEL in str(h.get("command", "")):
                return True, str(h.get("command"))
    return False, ""


def install(exclude: str = "", only: str = "", max_day: int = 20,
            model: str = "", config_dir: str = DEFAULT_CONFIG_DIR) -> str:
    install_script()
    path = _settings_path(config_dir)
    data = _load(path)
    hooks = data.setdefault("hooks", {})
    session_end = [
        e for e in hooks.get("SessionEnd", [])
        if not any(SENTINEL in str(h.get("command", "")) for h in e.get("hooks", []))
    ]
    command = build_command(exclude, only, max_day, model)
    session_end.append({"hooks": [
        {"type": "command", "command": command, "timeout": 10}
    ]})
    hooks["SessionEnd"] = session_end
    _save(path, data)
    return command


def uninstall(config_dir: str = DEFAULT_CONFIG_DIR) -> bool:
    path = _settings_path(config_dir)
    data = _load(path)
    hooks = data.get("hooks", {})
    before = hooks.get("SessionEnd", [])
    after = [
        e for e in before
        if not any(SENTINEL in str(h.get("command", "")) for h in e.get("hooks", []))
    ]
    if len(after) == len(before):
        return False
    if after:
        hooks["SessionEnd"] = after
    else:
        hooks.pop("SessionEnd", None)
    _save(path, data)
    return True
