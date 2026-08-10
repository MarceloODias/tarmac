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

## Confirmadas pelo Marcelo (2026-08-08, rodada de perguntas no painel)

20. **Rótulo de bloqueio sem `waitingFor` = "blocked" genérico** (§7.2) — confirmado.
21. **Seção OCIOSO visível no fim do TUI** — confirmado.
22. **Lembrete não tira bloqueada de PRECISA DE VOCÊ; `≥` literal; TUI
    interativo; fullscreen como caso padrão** — confirmados nas rodadas
    anteriores.
23. **Hook de `next_step` instalado no Mac e na EC2** (user-level
    `~/.claude/settings.json`, backup da EC2 em `settings.json.bak-tarmac`).
    Redesenhado sem dependência do tarmac na EC2: o hook grava
    `~/.tarmac/next-steps.jsonl` na máquina local e o coletor drena a fila a
    cada ciclo (local ou por ssh). Exclusão de chatops por env
    `TARMAC_NEXTSTEP_EXCLUDE=/home/ec2-user/ai-agent-skills` (o hook sai antes
    do `claude -p` nesses cwds — instalar user-level não fura a regra da §10).
24. **Instalação durável**: binário via `uv tool install` →
    `~/.local/bin/tarmac` (o worktree pode sumir; a instalação não). Profile
    dinâmico do iTerm2 "tarmac" (Menlo 16pt, roda o painel ao abrir) e
    LaunchAgent `com.diastech.tarmac` abre a janela no login.

## Feature nova pedida pelo Marcelo (fora da spec original)

25. **Tarefas avulsas** (`tarmac task "…"` / tecla `t` no painel): intenção
    sem sessão. Vivem em AGENDADO (☐), sobem para PRA HOJE quando vencem,
    `x` resolve, `m`/`a` dão prazo. Ao abrir (`Enter`): a pasta é inferida do
    texto contra o histórico de cwds das sessões `owned` (worktrees dobrados
    na raiz do repo; basename como palavra no texto, ex. "no benji-dp…" →
    `~/projects/inpowered/benji-dp`); empate só resolve sozinho com dominância
    3:1 de uso, senão um modal pergunta com as pastas mais usadas. A abertura
    roda `cd <pasta> && claude "<texto da tarefa>"` numa aba do iTerm
    (abrir-ou-focar, como sessões). Sem varredura de filesystem: o histórico
    do espelho é a única fonte de candidatos, coerente com a §2.

## Incidente de custo (2026-08-09) e o que mudou no processo

26. **O hook de `next_step` se auto-alimentava e queimou limite de uso.**
    `claude -p --resume <id>` **continua** aquela sessão; ao terminar, ela
    dispara `SessionEnd` de novo com o mesmo id → o hook rodava outra vez, em
    laço, e **cada volta reenvia o transcript inteiro como input**. Evidência:
    35 chamadas na fila da EC2 para apenas **5 sessões distintas** (~7 voltas
    cada). Ações: hook **removido** do Mac e da EC2; reescrito com quatro
    travas independentes (env `TARMAC_HOOK_GUARD` exportado ao processo filho,
    dedupe por sessão, teto diário, allow/deny por cwd) e modelo barato (Haiku)
    por padrão; **desligado por padrão**, instalável por `tarmac hook install`.
    Sete testes com um `claude` falso que **reencena a recursão** provam que o
    laço morre.
27. **`logs` abre num terminal, não num widget.** `claude logs` devolve um
    *replay de tela inteira* (74KB de escapes de cursor numa sessão real), não
    texto: renderizar isso num widget é ilegível e derrubava o app. Agora `l`
    abre uma janela do terminal com o comando.
28. **Sempre uma janela nova** (decisão sua): o painel é a fila, então o
    terminal virou descartável. Também corrige o bug em que o AppleScript lia
    `current window` e, numa corrida, escrevia o comando **dentro da janela do
    painel**, matando-o — agora usa a referência devolvida por `create window`.

### Como eu passei a testar (a causa de você cair em bugs)

Meus testes eram unitários com mocks, e **todo** bug que te atingiu vivia fora
deles: o app real, o binário instalado, o processo filho. Agora são quatro
camadas, e a última é um portão:

- `tests/test_cli_smoke.py` — roda **o CLI como subprocesso**, um teste por
  subcomando, com um `claude` falso. Pega import quebrado, argparse errado,
  crash na invocação real.
- `tests/test_e2e_panel.py` — **varre todas as teclas em todos os tipos de
  linha** do painel real, com `osascript`/`ssh`/`claude` falsos no PATH. Pega
  crash de worker, sqlite cross-thread, ação que não faz nada.
- `tests/test_hook.py` — hook de ponta a ponta com o falso que reencena a
  recursão.
- `scripts/preflight.sh` — suíte + **o binário instalado respondendo** a cada
  subcomando + hook off + regra de ouro da §2. **Não digo "está pronto" sem
  esse portão verde.**

E uma prática nova: **teste de mutação antes de declarar corrigido** — quebro o
fix de propósito e confirmo que a suíte falha. Foi assim que descobri que minha
primeira hipótese sobre o crash do `logs` estava errada (a suíte passava com o
bug reintroduzido), o que me levou à causa real.

## Auditoria independente de custo (2026-08-10)

29. Um agente auditor varreu o projeto atrás de gasto desnecessário. **Veredito:
    nenhum caminho ativo gasta tokens sem o Marcelo pedir** — o hook está
    desinstalado nas duas máquinas e as filas estão vazias. Achados corrigidos:
    - **Fallback de data rodava no modelo default (Opus 1M) e o TUI re-abria o
      campo em laço** a cada falha de parsing: cada tentativa era outra chamada.
      Agora usa Haiku explicitamente e **não** re-tenta sozinho — mostra os
      formatos aceitos.
    - **Travas 2 e 3 do hook eram fúráveis por corrida** (o auditor mediu 13, 5,
      6, 4 e 9 chamadas onde devia ser 1; e o teto de 3 estourando até 6).
      Agora há lock atômico por `mkdir` (não há `flock` no macOS), contador
      escrito via `tmp + mv`, e `.seen` podado.
    - **Trava 4 tinha dois furos silenciosos**: payload sem `cwd` ignorava a
      deny-list, e prefixo com `~` nunca era expandido. Agora `cwd` ausente com
      deny-list **nega**, e o `~` é expandido.
    - **1440 conexões SSH/dia inúteis**: o drain da fila abria uma segunda
      conexão por ciclo mesmo com a fila vazia. Agora a fila volta **na mesma
      viagem** da coleta (sentinela na saída) — o que também tirou I/O de rede
      de dentro da transação SQLite, que segurava lock do banco por até 10s.
    - Registrado como informação, fora do escopo do tarmac: **três cron jobs de
      terceiros na EC2** (`lld-agent`, `benji-dp-agent`, `ssp-model-integration`)
      rodam `claude --print` headless todo dia e gastam tokens — decisão do
      Marcelo o que fazer com eles.

## Fora do escopo desta entrega (deliberado)

- Resposta inline (§9.0.2): morta pelo FINDINGS E — não implementada.
- `Fechar abas resolvidas` existe como `tarmac gc-tabs` (nunca automático).
- Notificações: nenhuma, por decisão fechada da §7.
- Adaptadores de terminal além do iTerm2: degradação para clipboard já existe;
  Terminal.app/Ghostty/etc. ficam para a generalização pública (§15.2).
