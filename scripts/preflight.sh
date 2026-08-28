#!/bin/bash
# Gate before saying "está pronto". Runs the suite, then exercises the REAL
# installed binary — the layer where every bug that reached Marcelo lived.
#
#   ./scripts/preflight.sh          # tests + installed-binary smoke
#   ./scripts/preflight.sh --install  # also refresh the uv tool install
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
step() { printf '\n\033[1m▍%s\033[0m\n' "$1"; }
check() {
  if "$@" >/tmp/tarmac-preflight.$$ 2>&1; then
    printf '  ✓ %s\n' "$*"
  else
    printf '  ✗ %s\n' "$*"; sed 's/^/      /' /tmp/tarmac-preflight.$$ | tail -15; fail=1
  fi
  rm -f /tmp/tarmac-preflight.$$
}

step "suíte completa"
if uv run pytest -q; then printf '  ✓ pytest\n'; else printf '  ✗ pytest\n'; fail=1; fi

if [ "${1:-}" = "--install" ]; then
  step "reinstalando o binário"
  check uv tool install --force --from . tarmac
fi

step "binário instalado responde"
BIN="$HOME/.local/bin/tarmac"
if [ ! -x "$BIN" ]; then
  printf '  ✗ %s não existe (rode com --install)\n' "$BIN"; fail=1
else
  check "$BIN" --help
  check "$BIN" collect --force
  check "$BIN" poke
  check "$BIN" render --format swiftbar
  check "$BIN" render --format tui --once
  check "$BIN" stats
  check "$BIN" task
  check "$BIN" hook status
  check "$BIN" notify status
fi

step "notificação (SPEC §7.3) — o alerta chega mesmo?"
if [ -x "$BIN" ]; then
  before=$("$BIN" notify status)
  if "$BIN" notify test 2>&1 | grep -q enviada; then
    printf '  ✓ osascript aceitou o alerta de teste\n'
  else
    printf '  ! osascript recusou — System Settings > Notifications > Script Editor\n'
  fi
  # o teste não pode deixar o painel mudo por acidente
  case "$before" in *": on"|*": ligadas") "$BIN" notify on >/dev/null ;; esac
fi

step "hooks (nenhum gasta API desde a v0.2)"
"$BIN" hook status 2>/dev/null | grep "instalado" | sed 's/^/  /'
if "$BIN" hook status 2>/dev/null | grep -q "hook ANTIGO"; then
  printf '  ✗ hook SessionEnd antigo ainda instalado — ele GASTA a cada sessão\n'
  printf '    remova com: %s hook install\n' "$BIN"; fail=1
else
  printf '  ✓ nenhum hook antigo de SessionEnd\n'
fi

step "coleta funciona no ambiente pobre em que o painel roda"
# iTerm's Custom Command gives launchd's minimal PATH; the collector shells out
# to `claude`. This is the check that catches "sh: claude: command not found",
# which silently turned every row into (stale).
if env -i HOME="$HOME" TERM=dumb "$BIN" collect --force >/tmp/tarmac-env.$$ 2>&1 \
   && ! grep -qi "command not found\|error" /tmp/tarmac-env.$$; then
  printf '  ✓ coleta OK com PATH mínimo\n'
else
  printf '  ✗ coleta falha com PATH mínimo (use claude_bin absoluto):\n'
  sed 's/^/      /' /tmp/tarmac-env.$$ | head -5; fail=1
fi
rm -f /tmp/tarmac-env.$$

step "abertura do painel não depende de shell interativo"
PROFILE="$HOME/Library/Application Support/iTerm2/DynamicProfiles/tarmac.json"
if [ -f "$PROFILE" ]; then
  if grep -q '"Initial Text"' "$PROFILE"; then
    printf '  ✗ profile usa Initial Text (digita no shell: corrida e funções do zsh)\n'; fail=1
  elif grep -q '"Custom Command": "Yes"' "$PROFILE"; then
    launcher=$(python3 -c "import json,os;print(os.path.expandvars(json.load(open('$PROFILE'))['Profiles'][0].get('Command','')))" 2>/dev/null)
    if [ -x "$launcher" ]; then printf '  ✓ Custom Command → %s\n' "$launcher"
    else printf '  ✗ Command não executável: %s\n' "$launcher"; fail=1; fi
  else
    printf '  ✗ profile sem Custom Command\n'; fail=1
  fi
else
  printf '  · profile do iTerm não instalado (ok fora do Mac do Marcelo)\n'
fi

step "regra de ouro (§2): nada lê os arquivos internos do CLI"
if grep -rn --include='*.py' -e '\.claude/projects' -e 'state\.json' tarmac/ >/dev/null 2>&1; then
  printf '  ✗ código lendo fonte proibida\n'; fail=1
else
  printf '  ✓ só claude agents --json\n'
fi

printf '\n'
if [ "$fail" = "0" ]; then printf '\033[32mpreflight OK\033[0m\n'; else printf '\033[31mpreflight FALHOU\033[0m\n'; fi
exit "$fail"
