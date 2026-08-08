#!/bin/bash
# SessionEnd hook (SPEC §10): asks the finished session what is still pending
# and appends it to ~/.tarmac/next-steps.jsonl on THIS machine. The tarmac
# collector drains that file on every cycle (locally or over ssh) and stores
# it as the session's next_step (origin auto — never overwrites manual).
#
# Install (user-level, both machines):
#   ~/.claude/settings.json -> hooks.SessionEnd -> command: this script
#
# TARMAC_NEXTSTEP_EXCLUDE (optional): colon-separated cwd prefixes to skip —
# use it for service-session directories (chatops); one claude -p per Slack
# message would be cost without value (SPEC §10).

set -u
INPUT=$(cat)

SESSION_ID=$(printf '%s' "$INPUT" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("session_id",""))
except Exception: print("")' 2>/dev/null)
CWD=$(printf '%s' "$INPUT" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("cwd",""))
except Exception: print("")' 2>/dev/null)

[ -n "$SESSION_ID" ] || exit 0

IFS=':' read -ra EXCLUDES <<< "${TARMAC_NEXTSTEP_EXCLUDE:-}"
for prefix in "${EXCLUDES[@]:-}"; do
  [ -n "$prefix" ] && case "$CWD" in "$prefix"*) exit 0;; esac
done

# The slow part runs detached: the hook must never hold up shutdown (SPEC §10).
(
  NEXT=$(claude -p --resume "$SESSION_ID" --output-format json \
    "Em uma única frase curta, em português: o que ficou pendente nesta sessão?" \
    2>/dev/null | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("result","").strip().replace("\n"," "))
except Exception: print("")' 2>/dev/null)
  if [ -n "$NEXT" ]; then
    mkdir -p "$HOME/.tarmac"
    printf '%s' "$NEXT" | python3 -c "import json,sys,time
print(json.dumps({'session_id': '$SESSION_ID', 'next_step': sys.stdin.read(),
                  'at': int(time.time()*1000)}, ensure_ascii=False))" \
      >> "$HOME/.tarmac/next-steps.jsonl"
  fi
) >/dev/null 2>&1 &
disown 2>/dev/null || true
exit 0
