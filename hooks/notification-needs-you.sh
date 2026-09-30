#!/bin/bash
# Notification hook (SPEC §7.3): a session on THIS machine just started waiting.
#
# Why this exists: the alert used to depend on the collect cycle, so a session
# that blocked one second after a cycle waited up to 60s for its notification —
# and if the panel was closed, forever. Claude Code fires `Notification` the
# moment a session needs a human, so the machine can say so itself.
#
# What it does NOT do: decide anything. The payload is a hint; the panel's truth
# is still `claude agents --json` (SPEC §2). All this hook does is ask tarmac to
# collect the LOCAL targets right now — `tarmac poke` — and that collect goes
# through the same claim, the same mute and the same service/mine filters as any
# other. So a hook that fires twice, or fires for a session the panel does not
# alert on, still produces exactly the alerts the panel would have produced 60
# seconds later.
#
# Timing (from the hook docs):
#   permission_prompt  fires ~6s after the prompt appears, and every keystroke
#                      defers it — i.e. only when you are actually away.
#   agent_needs_input  fires when a background session starts waiting, but ONLY
#                      while `claude agents` is open in a terminal.
#   elicitation_*      an MCP server is waiting on you, same 6s gate.
#
# The work is detached: a hook must never hold up the session that fired it.
#
# Install/uninstall with:  tarmac hook install  /  tarmac hook uninstall

set -u

INPUT=$(cat)

TYPE=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('notification_type') or '')
except Exception:
    print('')
" 2>/dev/null)

# `tarmac hook install` writes a matcher, so Claude Code has already filtered.
# A hand-installed entry with no matcher (how remote hosts get their hooks) has
# not, and an idle_prompt firing 60s after every turn of every session would
# poll the machine for nothing. An empty type means we cannot tell: poke.
case "$TYPE" in
  permission_prompt|agent_needs_input|elicitation_dialog|elicitation_url_dialog|"") ;;
  *) exit 0 ;;
esac

# The hook runs with the session's environment, which after a launchd start is
# not an interactive shell's PATH — the same trap that once marked every row
# (stale) when a bare `claude` fell off PATH. `tarmac hook install` bakes the
# absolute path in as TARMAC_BIN; the rest is fallback for a hand-install.
BIN="${TARMAC_BIN:-}"
if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
  BIN=$(command -v tarmac 2>/dev/null)
fi
if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
  for candidate in "$HOME/.local/bin/tarmac" "/opt/homebrew/bin/tarmac" "/usr/local/bin/tarmac"; do
    if [ -x "$candidate" ]; then BIN="$candidate"; break; fi
  done
fi
[ -n "$BIN" ] && [ -x "$BIN" ] || exit 0

# Twice, three seconds apart. `permission_prompt` arrives well after the state
# is visible in the JSON, but `agent_needs_input` fires as the session starts
# waiting and can beat the listing by a moment; the second poke closes that
# window. It costs one more local `agents --json`, and the claim makes the
# second one silent whenever the first already alerted.
(
  "$BIN" poke
  sleep 3
  "$BIN" poke
) >/dev/null 2>&1 &
disown 2>/dev/null || true

exit 0
