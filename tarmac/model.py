"""Parse `claude agents --json` entries into a tolerant internal model.

Golden rule (SPEC §2): the JSON is the only source of truth, it is in research
preview, and the parser must be tolerant — unknown fields are ignored, missing
fields never crash the collector. Verified against v2.1.226 fixtures
(FINDINGS.md, blocks A/B/D).
"""

from __future__ import annotations

import json
import os.path
import re
from dataclasses import dataclass

# Unified "effective state" used for transitions, sections and the badge.
# Background sessions have `state`; interactive ones only `status`
# (FINDINGS A4). We fold both into one vocabulary:
#   blocked | working | idle | done | failed | stopped | unknown
BLOCKED, WORKING, IDLE = "blocked", "working", "idle"
DONE, FAILED, STOPPED, UNKNOWN = "done", "failed", "stopped", "unknown"
TERMINAL_STATES = {DONE, FAILED, STOPPED}


@dataclass
class Session:
    session_id: str            # 'id' (background) or 'sessionId' (interactive) — SPEC §5.1
    short_id: str | None       # 'id', for attach/logs/stop
    uuid: str | None           # 'sessionId', for claude --resume
    name: str | None
    kind: str                  # 'interactive' | 'background' | 'unknown'
    state: str | None          # raw, background only
    status: str | None         # raw, live process only
    waiting_for: str | None
    cwd: str | None
    pid: int | None
    started_at: int | None     # epoch ms, as delivered
    raw_json: str              # full payload, for schema-change debugging

    @property
    def effective_state(self) -> str:
        if self.state in (BLOCKED, WORKING, DONE, FAILED, STOPPED):
            return self.state
        if self.status == "waiting":
            return BLOCKED
        if self.status == "busy":
            return WORKING
        if self.status == "idle":
            # FINDINGS E: a background session blocked on a question reports
            # status=idle with state=blocked — state (checked above) wins.
            return IDLE
        return UNKNOWN

    @property
    def never_named(self) -> bool:
        """Default-name detection (SPEC §6.5, adjusted per FINDINGS D2).

        Default names are derived from the *lowercased* basename of cwd plus a
        two-char suffix (observed: BackendHealthMonitor -> backendhealthmonitor-51).
        """
        if not self.name or not self.cwd:
            return False
        base = re.escape(os.path.basename(self.cwd.rstrip("/")).lower())
        return re.fullmatch(rf"{base}-[a-z0-9]{{2}}", self.name.lower()) is not None


def parse_session(entry: dict) -> Session | None:
    """One JSON entry -> Session. Returns None only if there is no usable id."""
    if not isinstance(entry, dict):
        return None
    short_id = entry.get("id") if isinstance(entry.get("id"), str) else None
    uuid = entry.get("sessionId") if isinstance(entry.get("sessionId"), str) else None
    session_id = short_id or uuid
    if not session_id:
        return None

    def _str(key: str) -> str | None:
        v = entry.get(key)
        return v if isinstance(v, str) else None

    def _int(key: str) -> int | None:
        v = entry.get(key)
        return v if isinstance(v, int) else None

    return Session(
        session_id=session_id,
        short_id=short_id,
        uuid=uuid,
        name=_str("name"),
        kind=_str("kind") or "unknown",
        state=_str("state"),
        status=_str("status"),
        waiting_for=_str("waitingFor"),
        cwd=_str("cwd"),
        pid=_int("pid"),
        started_at=_int("startedAt"),
        raw_json=json.dumps(entry, ensure_ascii=False, sort_keys=True),
    )


def parse_agents_json(text: str) -> list[Session]:
    """Full `agents --json` output -> sessions. Raises ValueError on non-JSON."""
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("saída do agents --json não é um array")
    out = []
    for entry in data:
        s = parse_session(entry)
        if s is not None:
            out.append(s)
    return out
