"""Install/uninstall the Claude Code hooks the panel uses.

Two hooks, both free and both off until asked for explicitly (they edit a
settings.json that is not ours):

  next_step  Stop          -> hooks/stop-next-step.sh
             writes what a session last said into ~/.tarmac/next-steps.jsonl
             (SPEC §10). Costs nothing: the Stop payload carries the text.

  needs_you  Notification  -> hooks/notification-needs-you.sh
             asks tarmac to collect the local targets the moment a session
             starts waiting, so the alert does not wait for the 60s cycle
             (SPEC §7.3).

Both are installed per CLAUDE_CONFIG_DIR: a second account on the same machine
reads its own settings.json, so installing only in ~/.claude leaves that account
with no hook and no sign of one.

Installing also REMOVES the legacy SessionEnd hook (LEGACY_SENTINELS). That one
asked a model, through `claude -p --resume`, which continued the session and
fired the hook again — the loop that burned a usage limit. It is gone; leaving
an old entry behind would keep it spending after an upgrade.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_CONFIG_DIR, Config, tarmac_home


@dataclass(frozen=True)
class HookSpec:
    key: str            # what `tarmac hook install --which` takes
    event: str          # Claude Code hook event
    script: str         # file name, in hooks/ and in ~/.tarmac
    matcher: str = ""   # '' = every occurrence of the event
    timeout: int = 10

    @property
    def sentinel(self) -> str:
        """How we recognise our own entry in someone else's settings.json."""
        return self.script


NEXT_STEP = HookSpec(key="next-step", event="Stop", script="stop-next-step.sh")

# Every notification type that means "a human is being waited on". `idle_prompt`
# is deliberately absent: it fires 60s after every turn of every session, and an
# idle session is not blocked — the panel would poll the machine for nothing.
NEEDS_YOU_TYPES = (
    "permission_prompt",
    "agent_needs_input",
    "elicitation_dialog",
    "elicitation_url_dialog",
)
NEEDS_YOU = HookSpec(
    key="needs-you", event="Notification", script="notification-needs-you.sh",
    # only letters, digits, '_' and '|': Claude Code reads this as a list of
    # exact strings, not as a regular expression
    matcher="|".join(NEEDS_YOU_TYPES),
    timeout=10,
)

SPECS: dict[str, HookSpec] = {h.key: h for h in (NEXT_STEP, NEEDS_YOU)}

# entries from earlier versions of tarmac, removed wherever they are found
LEGACY_SENTINELS = ("session-end-next-step.sh",)


def hook_script_path(spec: HookSpec) -> Path:
    return tarmac_home() / spec.script


def hook_source(spec: HookSpec) -> Path:
    """Where the shipped hook lives — inside the wheel, or in the checkout.

    The installed binary has no repo above it, so the checkout-relative path
    alone made `tarmac hook install` fail with FileNotFoundError everywhere
    except a `uv run` from the repo (pyproject force-includes the first path).
    """
    for candidate in (Path(__file__).parent / "hooks" / spec.script,
                      Path(__file__).parent.parent / "hooks" / spec.script):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"{spec.script} não veio no pacote nem no checkout")


def install_script(spec: HookSpec) -> Path:
    """Copy the shipped hook next to the DB so it survives the repo moving."""
    src = hook_source(spec)
    dst = hook_script_path(spec)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    dst.chmod(0o755)
    return dst


def script_ref(spec: HookSpec) -> str:
    """How the settings.json entry names the script.

    `$HOME/.tarmac/…` on purpose while TARMAC_HOME is the default: the same
    settings.json is copied by hand onto remote hosts, where $HOME differs.
    A custom TARMAC_HOME has no portable spelling, so that one goes in absolute.
    """
    path = hook_script_path(spec)
    default = Path("~/.tarmac").expanduser() / spec.script
    if path == default:
        return f'"$HOME/.tarmac/{spec.script}"'
    return shlex.quote(str(path))


def tarmac_bin() -> str:
    """Absolute path to this tarmac, for a hook that runs without a login PATH."""
    override = os.environ.get("TARMAC_BIN")
    if override:
        return override
    found = shutil.which("tarmac")
    if found:
        # NOT resolved: `~/.local/bin/tarmac` is a symlink into whatever
        # directory the installer happens to use this week, and the stable
        # name is the one worth writing into someone else's settings.json
        return found if os.path.isabs(found) else str(Path(found).resolve())
    argv0 = Path(sys.argv[0])
    if argv0.name == "tarmac" and argv0.exists():
        return str(argv0.resolve())
    return ""


def build_command(spec: HookSpec, exclude: str = "", only: str = "") -> str:
    """The exact command line written into settings.json."""
    env = []
    if spec is NEXT_STEP:
        if exclude:
            env.append(f"TARMAC_NEXTSTEP_EXCLUDE={shlex.quote(exclude)}")
        if only:
            env.append(f"TARMAC_NEXTSTEP_ONLY={shlex.quote(only)}")
    if spec is NEEDS_YOU:
        binary = tarmac_bin()
        if binary:
            env.append(f"TARMAC_BIN={shlex.quote(binary)}")
    prefix = " ".join(env)
    return f"{prefix} {script_ref(spec)}".strip()


def _settings_path(config_dir: str = DEFAULT_CONFIG_DIR) -> Path:
    """settings.json of ONE session universe."""
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


def _commands(entry: dict) -> list[str]:
    return [str(h.get("command", "")) for h in entry.get("hooks", [])]


def _strip(data: dict, sentinels: tuple[str, ...]) -> bool:
    """Drop our entries from EVERY event; returns whether anything changed.

    Every event, not just the one we install into: the next_step hook moved from
    SessionEnd to Stop, and a leftover entry under the old event would keep
    running the old script.
    """
    hooks = data.get("hooks") or {}
    changed = False
    for event in list(hooks):
        before = hooks.get(event) or []
        after = [e for e in before
                 if not any(s in c for c in _commands(e) for s in sentinels)]
        if len(after) == len(before):
            continue
        changed = True
        if after:
            hooks[event] = after
        else:
            hooks.pop(event)
    if changed and not hooks:
        data.pop("hooks", None)
    return changed


def status(config_dir: str = DEFAULT_CONFIG_DIR) -> dict[str, str]:
    """{hook key: command} for every one of ours installed there.

    The legacy SessionEnd hook, if still present, reports under the key
    'legacy': it is not one of ours to run any more, but it is very much
    something to see — it is the one that spends money.
    """
    data = _load(_settings_path(config_dir))
    found: dict[str, str] = {}
    for event, entries in (data.get("hooks") or {}).items():
        for entry in entries:
            for command in _commands(entry):
                for spec in SPECS.values():
                    if spec.sentinel in command and spec.event == event:
                        found[spec.key] = command
                for legacy in LEGACY_SENTINELS:
                    if legacy in command:
                        found["legacy"] = command
    return found


def install(keys: list[str] | None = None, config_dir: str = DEFAULT_CONFIG_DIR,
            exclude: str = "", only: str = "") -> dict[str, str]:
    """Install the named hooks (default: all) into one config dir."""
    specs = [SPECS[k] for k in (keys or list(SPECS))]
    path = _settings_path(config_dir)
    data = _load(path)
    _strip(data, LEGACY_SENTINELS)
    _strip(data, tuple(s.sentinel for s in specs))

    commands: dict[str, str] = {}
    hooks = data.setdefault("hooks", {})
    for spec in specs:
        install_script(spec)
        command = build_command(spec, exclude, only)
        entry: dict = {"hooks": [
            {"type": "command", "command": command, "timeout": spec.timeout}
        ]}
        if spec.matcher:
            entry = {"matcher": spec.matcher, **entry}
        hooks.setdefault(spec.event, []).append(entry)
        commands[spec.key] = command
    _save(path, data)
    return commands


def uninstall(keys: list[str] | None = None,
              config_dir: str = DEFAULT_CONFIG_DIR) -> list[str]:
    """Remove the named hooks (default: all, legacy included). Returns what went."""
    specs = [SPECS[k] for k in (keys or list(SPECS))]
    path = _settings_path(config_dir)
    data = _load(path)
    before = status(config_dir)
    sentinels = tuple(s.sentinel for s in specs)
    if keys is None:
        sentinels += LEGACY_SENTINELS
    if not _strip(data, sentinels):
        return []
    _save(path, data)
    after = status(config_dir)
    return [k for k in before if k not in after]
