"""Load and validate targets.yaml.

The config file lives at ~/.tarmac/targets.yaml by default; override with
TARMAC_HOME (directory) or TARMAC_TARGETS (file). The real file is gitignored
(SPEC §15.4); targets.example.yaml documents the format.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_DIR = "~/.claude"


def tarmac_home() -> Path:
    home = os.environ.get("TARMAC_HOME")
    return Path(home).expanduser() if home else Path("~/.tarmac").expanduser()


def targets_path() -> Path:
    explicit = os.environ.get("TARMAC_TARGETS")
    if explicit:
        return Path(explicit).expanduser()
    return tarmac_home() / "targets.yaml"


@dataclass
class SessionClassRule:
    class_: str  # 'service' | 'owned'
    label: str = ""
    match_cwd: str | None = None
    match_name: str | None = None

    def matches(self, cwd: str | None, name: str | None) -> bool:
        if self.match_cwd is not None:
            if not cwd or not self._cwd_matches(cwd):
                return False
        if self.match_name is not None:
            if not name or not re.search(self.match_name, name):
                return False
        return self.match_cwd is not None or self.match_name is not None

    def _cwd_matches(self, cwd: str) -> bool:
        """`/dir/**` means "that tree", which includes /dir itself.

        A session started in the very directory (backend-agent lives in
        ~/ai-agent-skills, not below it) otherwise leaks into the main list.
        The sibling /ai-agent-skills-old must still NOT match."""
        pattern = self.match_cwd or ""
        cwd = cwd.rstrip("/")
        if fnmatch.fnmatch(cwd, pattern):
            return True
        if pattern.endswith("/**"):
            return cwd == pattern[:-3].rstrip("/")
        return False


@dataclass
class Target:
    id: str
    label: str = ""
    account: str = ""   # Claude account/config dir shown as its own column;
                        # blank = the default one (SPEC §3)
    owner: str = ""
    mine: bool = True
    enabled: bool = True
    transport: str = "local"  # 'local' | 'ssh'
    ssh_host: str = ""
    ssh_user: str = ""
    claude_bin: str = "claude"
    config_dir: str = DEFAULT_CONFIG_DIR
    expect_intermittent: bool = False
    offline_after: int = 3
    session_classes: list[SessionClassRule] = field(default_factory=list)

    def classify(self, cwd: str | None, name: str | None) -> str:
        """'service' if any rule matches, else 'owned' (SPEC §3.3)."""
        for rule in self.session_classes:
            if rule.class_ == "service" and rule.matches(cwd, name):
                return "service"
        return "owned"

    @property
    def needs_config_dir_export(self) -> bool:
        return self.config_dir not in ("", DEFAULT_CONFIG_DIR)

    @property
    def config_dir_prefix(self) -> str:
        """`CLAUDE_CONFIG_DIR=<dir> ` to glue in front of the binary, or ''.

        A leading `~` must survive the quoting. `shlex.quote("~/.claude-alt")`
        yields `'~/.claude-alt'` and the CLI takes it literally: it reads a
        directory named `~`, finds nothing, and exits 0 with an empty list — an
        invisible target with no error to notice. Locally we expand the tilde
        ourselves; over ssh only the remote knows its $HOME, so we leave the
        expansion to the remote shell and quote only the rest of the path.
        """
        if not self.needs_config_dir_export:
            return ""
        cfg = self.config_dir
        if self.transport == "local":
            quoted = shlex.quote(str(Path(cfg).expanduser()))
        elif cfg == "~":
            quoted = '"$HOME"'
        elif cfg.startswith("~/"):
            quoted = '"$HOME"/' + shlex.quote(cfg[2:])
        else:
            quoted = shlex.quote(cfg)
        return f"CLAUDE_CONFIG_DIR={quoted} "


    def matches_config_dir(self, absolute: str) -> bool:
        """Is `absolute` — a path recorded on the machine itself, already
        expanded — this target's config dir?

        Used to route the machine-wide next_step queue: two config dirs on one
        box share `~/.tarmac/next-steps.jsonl`, so each entry has to be handed
        to the target it came from. Over ssh we don't know the remote $HOME, so
        a `~/x` target matches any absolute path ending in `/x` — enough to
        tell two config dirs on the same host apart, which is all we need.
        """
        if not absolute:
            return False
        candidate = os.path.normpath(absolute)
        mine = self.config_dir or DEFAULT_CONFIG_DIR
        if not mine.startswith("~"):
            return candidate == os.path.normpath(mine)
        if self.transport == "local":
            return candidate == os.path.normpath(str(Path(mine).expanduser()))
        rest = mine[1:].strip("/")
        if not rest:  # bare '~': nothing to match on remotely
            return False
        return candidate.endswith("/" + rest)


@dataclass
class Settings:
    locale: str = "pt"          # 'pt' | 'en' (SPEC §15.2)
    default_hour: int = 9       # anchor for dates without time (SPEC §6.3)
    end_of_day_hour: int = 18
    service_stuck_min: int = 30  # service blocked alert threshold (SPEC §3.3)
    stale_after_s: int = 60      # collect_if_stale window (SPEC §8.0)
    stale_data_after_s: int = 300  # data older than this is not to be trusted
    idle_tab_min: int = 30       # 'close resolved tabs' threshold (SPEC §9.0.1)
    permission_prompt_anomalous: bool = True  # SPEC §15.2: configurable
    notify: bool = True          # macOS alert on entering NEEDS YOU (SPEC §7.3)
    notify_sound: str = "Ping"   # macOS sound name; '' = notification, no sound


@dataclass
class Config:
    targets: list[Target]
    settings: Settings

    def enabled_targets(self) -> list[Target]:
        return [t for t in self.targets if t.enabled]

    def target(self, target_id: str) -> Target | None:
        for t in self.targets:
            if t.id == target_id:
                return t
        return None


def _parse_rule(raw: dict) -> SessionClassRule:
    return SessionClassRule(
        class_=str(raw.get("class", "service")),
        label=str(raw.get("label", "")),
        match_cwd=raw.get("match_cwd"),
        match_name=raw.get("match_name"),
    )


def _parse_target(raw: dict) -> Target:
    tid = raw.get("id")
    if not tid:
        raise ValueError("target sem 'id' no targets.yaml")
    transport = raw.get("transport", "local")
    if transport not in ("local", "ssh"):
        raise ValueError(f"target {tid}: transport inválido {transport!r}")
    if transport == "ssh" and not raw.get("ssh_host"):
        raise ValueError(f"target {tid}: transport ssh exige ssh_host")
    if transport == "ssh" and not raw.get("claude_bin"):
        raise ValueError(f"target {tid}: transport ssh exige claude_bin absoluto (SPEC §4.3)")
    return Target(
        id=str(tid),
        label=str(raw.get("label", tid)),
        account=str(raw.get("account", "")),
        owner=str(raw.get("owner", "")),
        mine=bool(raw.get("mine", True)),
        enabled=bool(raw.get("enabled", True)),
        transport=transport,
        ssh_host=str(raw.get("ssh_host", "")),
        ssh_user=str(raw.get("ssh_user", "")),
        claude_bin=str(raw.get("claude_bin", "claude")),
        config_dir=str(raw.get("config_dir", DEFAULT_CONFIG_DIR)),
        expect_intermittent=bool(raw.get("expect_intermittent", False)),
        offline_after=int(raw.get("offline_after", 3)),
        session_classes=[_parse_rule(r) for r in raw.get("session_classes", [])],
    )


def load_config(path: Path | None = None) -> Config:
    path = path or targets_path()
    if not path.exists():
        # No config: single implicit local target, so `tarmac` works out of the box.
        return Config(
            targets=[Target(id="local", label="Local", mine=True, transport="local")],
            settings=Settings(),
        )
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    targets = [_parse_target(t) for t in raw.get("targets", [])]
    ids = [t.id for t in targets]
    if len(ids) != len(set(ids)):
        raise ValueError("ids duplicados no targets.yaml")
    s = raw.get("settings", {}) or {}
    settings = Settings(
        locale=str(s.get("locale", "pt")),
        default_hour=int(s.get("default_hour", 9)),
        end_of_day_hour=int(s.get("end_of_day_hour", 18)),
        service_stuck_min=int(s.get("service_stuck_min", 30)),
        stale_after_s=int(s.get("stale_after_s", 60)),
        stale_data_after_s=int(s.get("stale_data_after_s", 300)),
        idle_tab_min=int(s.get("idle_tab_min", 30)),
        permission_prompt_anomalous=bool(s.get("permission_prompt_anomalous", True)),
        notify=bool(s.get("notify", True)),
        notify_sound=str(s.get("notify_sound", "Ping")),
    )
    return Config(targets=targets, settings=settings)
