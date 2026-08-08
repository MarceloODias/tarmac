#!/bin/bash
# SessionEnd hook (SPEC §10): asks the finished session what is still pending
# and stores it as the panel's next_step. Install per target in
# ~/.claude/settings.json:
#
#   { "hooks": { "SessionEnd": [ { "hooks": [ { "type": "command",
#     "command": "/path/to/session-end-next-step.sh <target_id> &" } ] } ] } }
#
# Notes:
# - The trailing '&' matters: the hook must never hold up session shutdown.
# - Do NOT install this in the settings of service-session directories
#   (chatops dirs) — one claude -p per Slack message is cost without value.
# - It never overwrites a next_step you typed by hand (origin manual wins).

set -euo pipefail
TARGET_ID="${1:?uso: session-end-next-step.sh <target_id>}"

INPUT=$(cat)
SESSION_ID=$(printf '%s' "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_id",""))')
[ -n "$SESSION_ID" ] || exit 0

NEXT=$(claude -p --resume "$SESSION_ID" --output-format json \
  "Em uma única frase curta, em português: o que ficou pendente nesta sessão?" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("result","").strip())')
[ -n "$NEXT" ] || exit 0

tarmac next-step "$TARGET_ID" "$SESSION_ID" --origin auto "$NEXT"
