# DECISIONS — decisões tomadas sem você, para sua revisão

> Instrução recebida: "não fique travado por uma decisão minha; documente o que
> você decidiu". Cada item abaixo é uma decisão que a SPEC deixava aberta (ou
> que os FINDINGS invalidaram). Reverta o que discordar — nada aqui é caro de
> mudar.

## Da spec × achados da Tarefa 0

1. **§7.2 — bloqueio sem `waitingFor`** (FINDINGS A4): sessão background
   bloqueada por pergunta vem sem o campo. Decisão: a linha mostra o
   `waitingFor` quando existe, e o rótulo neutro `blocked` quando não existe.
   `permission prompt` continua com `⚠` (anômalo sob auto mode). Nenhuma
   tentativa de adivinhar "input needed".
2. **§3.1 — `targets.yaml` de exemplo** atualizado para a realidade: usuário
   `ec2-user`, `claude_bin /home/ec2-user/.local/bin/claude`, e o
   `session_classes` com `match_cwd: "/home/ec2-user/ai-agent-skills/**"`
   (FINDINGS D1). Criei também o **seu** `~/.tarmac/targets.yaml` real (fora do
   repo) com Mac + EC2 — revise os labels.
3. **§3.3 — retenção de 24h** aplicada ao **SQLite do tarmac**: o FINDINGS D3
   mostrou que sessões interactive somem sozinhas do `--json` ao concluir, então
   quem acumularia é o nosso banco. `prune_service_sessions()` roda a cada
   coleta.
4. **§6.5/G1 — nome default** compara com o `basename(cwd)` **minusculizado**
   (FINDINGS D2: `BackendHealthMonitor` → `backendhealthmonitor-51`).
5. **§4.4 — espera incerta pós-reconexão**: segui a spec literalmente — a
   transição é carimbada no **início da janela cega** com `uncertain = 1` e
   exibida como `≥`. Nota de revisão: a rigor esse valor é o *teto* da espera
   (ela bloqueou em algum ponto da janela), não o piso; mantive o `≥` porque a
   spec o fixa e o efeito prático (não mostrar número falso e pequeno) é o
   desejado. Se preferir `≤` ou outro glifo, é uma linha.
6. **§4.4 — socket morto do ControlMaster**: em falha de coleta classificada
   como offline, rodo `ssh -O check` e, se o master responde mas a conexão
   falha, `ssh -O exit` derruba o socket para a próxima tentativa nascer limpa.

## De engenharia

7. **TUI v1 é display-only** (rich.Live, loop de 60s). Ações via CLI ou menu
   SwiftBar. Um TUI interativo (textual, com teclas por linha) é v2 — o
   primário da §8.0 é *ver tudo sem clique*, e isso o rich entrega hoje com um
   décimo da complexidade.
8. **Dependências: PyYAML + rich, gerenciadas com uv** (`uv sync`; lockfile
   versionado). A spec pedia "stdlib sempre que possível" — YAML e TUI são as
   duas exceções que ela mesma prevê (§14).
9. **Config em `~/.tarmac/targets.yaml`** com bloco `settings:` (locale,
   default_hour, etc.). Sem arquivo → target local implícito, para o
   quickstart funcionar sem setup. Overrides por env: `TARMAC_HOME`,
   `TARMAC_TARGETS`.
10. **Chave da sessão** (§5.1, literal): `id` (short) para background,
    `sessionId` (uuid) para interactive. Sempre composta com `target_id`.
11. **Estado unificado (`eff_state`)**: `state` (bg) vence; `status: waiting` →
    `blocked`; `busy` → `working`; `idle` → `idle`. Interativas em `waiting`
    contam no badge como bloqueadas (é o caso do permission prompt real que o
    seu Mac tinha durante o desenvolvimento).
12. **Backoff offline** (60s → 2min → 5min) persistido em `target_status`;
    reset no primeiro sucesso. Target **não** intermitente que fica
    inalcançável acende `⚠` (offline inesperado é problema, não rotina).
13. **Ociosas**: o TUI mostra seção OCIOSO (espaço sobra num monitor dedicado);
    o menu SwiftBar as omite (espaço é escasso e elas não pedem nada).
14. **`rm` pede confirmação digitada** (`remover`) em vez de dois cliques — é a
    "confirmação dupla" da §9.2 em CLI.
15. **`Lembrar em…` no SwiftBar** oferece os atalhos `2h · amanhã · segunda`;
    texto livre vai pelo CLI (`tarmac remember <target> <sessão> <texto>`), já
    que SwiftBar não tem campo de texto nativo. O parser cobre todas as formas
    da §6.2 com regex e cai para `claude -p` no resto.
15b. **Onde vive uma sessão com lembrete (§6.1 × §8.2)**: `blocked` com
    `due_at` futuro **fica em PRECISA DE VOCÊ** (ela precisa de mim agora; o
    lembrete não a esconde); qualquer outro estado com `due_at` futuro vai para
    AGENDADO; `hide_until_due = 1` vai para AGENDADO sempre. Vencido sobe para
    PRA HOJE em todos os casos.
16. **Hook de `next_step`**: `hooks/session-end-next-step.sh` versionado como
    exemplo, **não** instalado automaticamente. Ele respeita origem
    (`auto` nunca sobrescreve `manual`).
17. **i18n mínimo** (§15.2): dicionários `pt`/`en` em `strings.py`; `locale`
    no settings. Seu config está em `pt`; o default do repo é `en`.
18. **`wasted_total`** soma intervalos em blocked por dia local, só `owned`,
    incluindo intervalos ainda abertos (até agora). `tarmac stats` imprime.
19. **SwiftBar plugin**: o repositório não versiona o plugin em si; a linha de
    instalação está no README (um symlink que chama `tarmac render --format
    swiftbar`).

## Fora do escopo desta entrega (deliberado)

- Resposta inline (§9.0.2): morta pelo FINDINGS E — não implementada.
- `Fechar abas resolvidas` existe como `tarmac gc-tabs` (nunca automático).
- Notificações: nenhuma, por decisão fechada da §7.
- Adaptadores de terminal além do iTerm2: degradação para clipboard já existe;
  Terminal.app/Ghostty/etc. ficam para a generalização pública (§15.2).
