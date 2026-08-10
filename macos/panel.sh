#!/bin/bash
# Launcher used by the iTerm "tarmac" profile (Custom Command).
#
# Why a script instead of the binary directly: if the panel dies, the session
# would close instantly and the error would vanish with it. This keeps the
# window open so the failure is readable. It is NOT an interactive shell —
# no rc files, no functions, no keystroke race (that is what produced
# `i~/.local/bin/tarmac` and, most likely, the FUNCNEST error).
exec 2>&1
"$HOME/.local/bin/tarmac" render --format tui
status=$?
if [ "$status" -ne 0 ]; then
  printf '\n\033[1;31mtarmac saiu com erro (%s)\033[0m\n' "$status"
  printf 'Enter fecha esta janela.\n'
  read -r _
fi
