#!/bin/bash
# SessionEnd hook (SPEC §10): records "what is still pending" for the panel.
#
# ⚠️ COST SAFETY — read before enabling. `claude -p --resume <id>` CONTINUES
# that session, so when it finishes it fires SessionEnd again, with the same
# id: the naive version of this hook feeds itself in a loop, and every lap
# replays the whole transcript as input. That burned a real usage limit
# (35 calls for 5 sessions in one day). Four independent guards now:
#
#   1. TARMAC_HOOK_GUARD  — exported before calling claude; a nested hook sees
#                           it and exits. Kills the loop at the source.
#   2. dedupe file        — one next_step per session id, ever.
#   3. daily cap          — hard ceiling per machine (TARMAC_NEXTSTEP_MAX_DAY).
#   4. allow/deny lists   — by cwd, so chatops dirs never spend a token.
#
# Install/uninstall with:  tarmac hook install   /   tarmac hook uninstall
# Off by default. Cheap model by default (TARMAC_NEXTSTEP_MODEL).

set -u

# --- guard 1: never let a hook-spawned session trigger the hook again -------
if [ -n "${TARMAC_HOOK_GUARD:-}" ]; then
  exit 0
fi

INPUT=$(cat)
STATE_DIR="${TARMAC_HOME:-$HOME/.tarmac}"
QUEUE="$STATE_DIR/next-steps.jsonl"
SEEN="$STATE_DIR/next-steps.seen"
COUNTER="$STATE_DIR/next-steps.count"
MAX_DAY="${TARMAC_NEXTSTEP_MAX_DAY:-20}"
MODEL="${TARMAC_NEXTSTEP_MODEL:-claude-haiku-4-5-20251001}"

read_field() {
  printf '%s' "$INPUT" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('$1', '') or '')
except Exception:
    print('')
" 2>/dev/null
}

SESSION_ID=$(read_field session_id)
CWD=$(read_field cwd)
[ -n "$SESSION_ID" ] || exit 0

mkdir -p "$STATE_DIR" 2>/dev/null || exit 0

expand_home() {  # a deny prefix written as ~/foo was silently inert before
  case "$1" in "~/"*) printf '%s' "$HOME/${1#\~/}" ;; "~") printf '%s' "$HOME" ;;
                   *) printf '%s' "$1" ;; esac
}

# --- guard 4: cwd allow/deny (checked before taking the lock) ----------------
if [ -n "${TARMAC_NEXTSTEP_ONLY:-}" ]; then
  allowed=0
  IFS=':' read -ra ONLY <<< "$TARMAC_NEXTSTEP_ONLY"
  for prefix in "${ONLY[@]:-}"; do
    [ -n "$prefix" ] || continue
    prefix=$(expand_home "$prefix")
    case "$CWD" in "$prefix"*) allowed=1;; esac
  done
  [ "$allowed" = "1" ] || exit 0
fi
if [ -n "${TARMAC_NEXTSTEP_EXCLUDE:-}" ]; then
  # unknown cwd + a deny-list means we cannot prove this is allowed: DENY.
  # (payloads without `cwd` used to slip past every exclusion silently)
  [ -n "$CWD" ] || exit 0
  IFS=':' read -ra EXCLUDES <<< "$TARMAC_NEXTSTEP_EXCLUDE"
  for prefix in "${EXCLUDES[@]:-}"; do
    [ -n "$prefix" ] || continue
    prefix=$(expand_home "$prefix")
    case "$CWD" in "$prefix"*) exit 0;; esac
  done
fi

# --- lock: guards 2 and 3 are read-modify-write, so they need mutual ---------
# exclusion. `mkdir` is the portable atomic primitive (no flock on macOS).
# Concurrent session ends used to blow past the daily cap and re-summarise the
# same session several times.
LOCK="$STATE_DIR/next-steps.lock"
acquired=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if mkdir "$LOCK" 2>/dev/null; then acquired=1; break; fi
  # steal a lock left behind by a killed hook (older than a minute)
  if [ -d "$LOCK" ] && [ -z "$(find "$LOCK" -maxdepth 0 -mmin -1 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null
  fi
  sleep 0.2
done
# could not lock: skip this one rather than risk a double spend
[ "$acquired" = "1" ] || exit 0
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# --- guard 2: one summary per session, ever ---------------------------------
if [ -f "$SEEN" ] && grep -qxF "$SESSION_ID" "$SEEN" 2>/dev/null; then
  exit 0
fi

# --- guard 3: daily cap -----------------------------------------------------
TODAY=$(date +%Y-%m-%d)
COUNT=0
if [ -f "$COUNTER" ]; then
  saved_day=$(cut -d' ' -f1 "$COUNTER" 2>/dev/null)
  [ "$saved_day" = "$TODAY" ] && COUNT=$(cut -d' ' -f2 "$COUNTER" 2>/dev/null)
fi
case "$COUNT" in ''|*[!0-9]*) COUNT=0;; esac
if [ "$COUNT" -ge "$MAX_DAY" ]; then
  exit 0
fi

# Claim the slot BEFORE spending anything: crash-safe against retries.
printf '%s\n' "$SESSION_ID" >> "$SEEN"
printf '%s %s\n' "$TODAY" "$((COUNT + 1))" > "$COUNTER.tmp" && mv -f "$COUNTER.tmp" "$COUNTER"
# keep .seen bounded: it was append-only and grew forever
if [ "$(wc -l < "$SEEN" 2>/dev/null || echo 0)" -gt 2000 ]; then
  tail -n 1000 "$SEEN" > "$SEEN.tmp" && mv -f "$SEEN.tmp" "$SEEN"
fi
rmdir "$LOCK" 2>/dev/null; trap - EXIT

# The API call runs detached — the hook must never hold up shutdown (SPEC §10).
(
  NEXT=$(TARMAC_HOOK_GUARD=1 claude -p --resume "$SESSION_ID" \
    --model "$MODEL" --output-format json \
    "Em uma única frase curta, em português: o que ficou pendente nesta sessão?" \
    2>/dev/null | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('result', '').strip().replace(chr(10), ' '))
except Exception:
    print('')
" 2>/dev/null)
  if [ -n "$NEXT" ]; then
    printf '%s' "$NEXT" | python3 -c "
import json, sys, time
print(json.dumps({'session_id': '$SESSION_ID', 'next_step': sys.stdin.read(),
                  'at': int(time.time() * 1000)}, ensure_ascii=False))
" >> "$QUEUE"
  fi
) >/dev/null 2>&1 &
disown 2>/dev/null || true
exit 0
