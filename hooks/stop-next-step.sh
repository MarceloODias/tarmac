#!/bin/bash
# Stop hook (SPEC §10): records what a session last said, for the panel.
#
# This hook costs NOTHING and cannot loop. The `Stop` event hands us
# `last_assistant_message` — the text of the reply that just ended — so the
# panel can show where a session stopped without asking a model anything.
#
# What it replaced: a SessionEnd hook that ran `claude -p --resume <id>` to have
# a model summarise the session. That command CONTINUES the session, so
# finishing it fired SessionEnd again with the same id — a feedback loop that
# replayed the whole transcript every lap and burned a usage limit once (35
# calls for 5 sessions in a day). Four guards were built to contain it: an
# exported env guard, a dedupe file, a daily cap and a lock. None of them are
# needed here: this hook spawns nothing, so there is no loop, no cap, and no
# spend to bound. The two cwd lists survive, for noise and privacy rather than
# for cost — the note text lands in the panel's database.
#
# The note is the session's own words, truncated. It is not a model's opinion
# about what is pending, and that is deliberate: a summary can be wrong in a way
# a quote cannot, and this column is read as fact ("next_step que não mente").
#
# Install/uninstall with:  tarmac hook install  /  tarmac hook uninstall
#
# Stop fires at the end of EVERY turn, so the note is rewritten as the session
# moves; the collector clears it when the session starts working again
# (SPEC §10), and a next_step typed by hand (origin 'manual') is never
# overwritten.

set -u

INPUT=$(cat)
STATE_DIR="${TARMAC_HOME:-$HOME/.tarmac}"
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0

# Which session universe is this? The queue lives in ~/.tarmac, which every
# CLAUDE_CONFIG_DIR on the machine shares, so the collector needs to know who
# wrote each line to hand it to the right target (SPEC §10).
CFG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

printf '%s' "$INPUT" | \
  TARMAC_Q_CFG="$CFG_DIR" \
  TARMAC_Q_QUEUE="$STATE_DIR/next-steps.jsonl" \
  python3 -c '
import json, os, re, sys, time

MAX = 200

def expand(path):
    home = os.environ.get("HOME", "")
    if path == "~":
        return home
    if path.startswith("~/"):
        return os.path.join(home, path[2:])
    return path

def allowed(cwd):
    """The two cwd lists, with the same semantics the cost guards had."""
    only = os.environ.get("TARMAC_NEXTSTEP_ONLY", "")
    deny = os.environ.get("TARMAC_NEXTSTEP_EXCLUDE", "")
    if only:
        prefixes = [expand(p) for p in only.split(":") if p]
        if not cwd or not any(cwd.startswith(p) for p in prefixes):
            return False
    if deny:
        # unknown cwd + a deny list means we cannot prove this is allowed: DENY.
        # (payloads without `cwd` used to slip past every exclusion silently)
        if not cwd:
            return False
        for p in deny.split(":"):
            if p and cwd.startswith(expand(p)):
                return False
    return True

def condense(text):
    """One line of prose out of a markdown reply.

    Code blocks are dropped whole: a note that opens with three lines of diff
    says nothing about where the session stopped, and it is the first 200
    characters that reach the panel.
    """
    lines, fenced = [], False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```") or line.startswith("~~~"):
            fenced = not fenced
            continue
        if fenced or not line:
            continue
        line = re.sub(r"^(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)", "", line)
        lines.append(line)
    out = " ".join(lines)
    out = out.replace("`", "").replace("**", "").replace("__", "")
    out = re.sub(r"\s+", " ", out).strip()
    if len(out) > MAX:
        cut = out[:MAX]
        space = cut.rfind(" ")
        if space > MAX // 2:          # never leave half a word
            cut = cut[:space]
        out = cut.rstrip(" ,;:-") + "…"
    return out

try:
    payload = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
if not isinstance(payload, dict):
    raise SystemExit(0)

session_id = str(payload.get("session_id") or "")
cwd = str(payload.get("cwd") or "")
message = payload.get("last_assistant_message")
if not session_id or not isinstance(message, str) or not allowed(cwd):
    raise SystemExit(0)

note = condense(message)
if not note:
    raise SystemExit(0)

line = json.dumps({
    "session_id": session_id,
    "next_step": note,
    # expanded here, not in the shell: the collector matches this against a
    # target config_dir and a literal "~" matches nothing (SPEC §10)
    "config_dir": expand(os.environ["TARMAC_Q_CFG"]),
    "at": int(time.time() * 1000),
}, ensure_ascii=False)
# one write, one line: concurrent session ends append to the same queue
with open(os.environ["TARMAC_Q_QUEUE"], "a") as f:
    f.write(line + "\n")
' 2>/dev/null

exit 0
