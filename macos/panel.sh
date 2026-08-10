#!/bin/bash -l
# Launcher used by the iTerm "tarmac" profile (Custom Command).
#
# `-l` (login shell) on purpose: iTerm's Custom Command runs the program with
# launchd's minimal PATH, and the collector shells out to `claude`. Without a
# login shell the local target failed with "sh: claude: command not found" and
# every row rendered as (stale) — the panel was honest, the environment was
# broken. A login shell is NOT an interactive one: no keystroke race, which is
# what produced `i~/.local/bin/tarmac` and, most likely, the FUNCNEST error.
#
# Belt and braces: targets.yaml should still use absolute claude_bin paths.
exec 2>&1
"$HOME/.local/bin/tarmac" render --format tui
status=$?
if [ "$status" -ne 0 ]; then
  printf '\n\033[1;31mtarmac saiu com erro (%s)\033[0m\n' "$status"
  printf 'Enter fecha esta janela.\n'
  read -r _
fi
