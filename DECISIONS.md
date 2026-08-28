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

### O painel congelado (24/08/2026)

Você viu duas sessões em TRABALHANDO: uma estava ociosa e a outra esperando por
você. O painel não estava errado, estava **parado** — mostrando o frame das
22:46 do dia anterior, com os cinco targets marcados `ok`.

29. **Migrações versionadas** (`db.SCHEMA_VERSION` + `db.MIGRATIONS`). A causa
    raiz: a `notifications` da §7.3 nasceu com `CREATE TABLE IF NOT EXISTS`, e o
    banco do Marcelo já tinha uma tabela com esse nome, de um rascunho da mesma
    feature chaveado em `blocked_at` (nunca commitado). O `IF NOT EXISTS` viu o
    nome e não fez nada; todo collect a partir dali morria em `no column named
    transition_id`. `IF NOT EXISTS` constrói banco novo e é **cego para tabela
    existente com forma errada** — então drift agora é consertado explicitamente,
    e a etapa 1 só dropa a tabela se ela estiver drifted: uma correta guarda
    claims vivos, e jogá-los fora re-anunciaria sessões já anunciadas.
30. **Um passo que estoura não derruba o ciclo** (`collect._guarded`). O ciclo
    escreve várias coisas independentes sob uma transação só; qualquer uma
    estourando descartava tudo e escapava do processo. Agora cada passo tem seu
    savepoint. E `collect_target` não levanta mais: `pool.map` reergue no
    chamador, então uma surpresa em um target levava os outros junto.
31. **Falha interna é falha do target** (`collect.record_failure`). O crash
    acontecia *antes* do código que registra erro, então `error_kind` ficava
    NULL para sempre — foi isso que fez um painel quebrado parecer saudável.
    Passos sem target a quem atribuir (mute, next_step, prune) gravam o erro em
    `kv:last_error:<passo>`: não param o ciclo, mas também não somem.
32. **Dado tem idade, independente de erro** (`settings.stale_data_after_s`,
    estado `stale`). Esta é a defesa que **não depende do caminho de falha
    funcionar**: passou de 5 min sem leitura, o target vira ⏳ e o badge também.
    "Nada espera por você" e "nada esperava da última vez que consegui olhar"
    não podem mais renderizar igual.

Os quatro consertos passaram por teste de mutação: 11 mutantes, 11 mortos.

## Hook de `next_step` invadia a sessão que resumia (2026-08-25)

33. **`--fork-session` no resume do hook.** `claude -p --resume <id>` sem fork
    grava no transcript da própria sessão do usuário. Três sintomas, um só
    causa, todos vistos nos transcripts: (a) a pergunta aparece como prompt do
    usuário — em sessão viva ela entra como `queue-operation`, parecendo que o
    Claude não respondeu; (b) o último turno de assistente fica com o modelo
    barato, então reabrir a sessão volta em **Haiku**; (c) o turno extra faz o
    coletor ver a sessão em `working` e `clear_auto_next_step` **apaga a nota
    recém-gravada** (foi o que aconteceu com `29de4467` e `607e51cc`). Com
    `--fork-session` a resposta sai de um id descartável, o arquivo original
    não é tocado e o fork não aparece em `claude agents --json` (verificado),
    logo não polui o painel.
34. **A pergunta segue `settings.locale`.** Estava fixa em português dentro do
    shell, num painel em inglês. As duas versões passaram para
    `tarmac/strings.py` (`next_step_prompt`, como todo texto novo) e viajam em
    `TARMAC_NEXTSTEP_PROMPT`; o default do script, para hooks instalados à mão
    em host remoto, é o inglês.
35. **`tarmac hook install` só funcionava dentro do checkout.** O script era
    procurado em `../hooks/` relativo ao pacote, que não existe no wheel: pelo
    binário instalado o comando morria com `FileNotFoundError` — ou seja, o
    painel nunca conseguia atualizar o próprio hook. O `pyproject` agora
    `force-include`-a o script como `tarmac/hooks/`, e `hook_source()` aceita
    as duas origens.

    Teste de mutação: tirar `--fork-session` e voltar a pergunta para português
    mata os testes novos (2 mutantes, 2 mortos).

## O changelog do Claude Code alcançou o painel (2026-08-28)

Releitura do changelog (v2.1.226 → v2.1.251, a versão instalada) procurando
código nosso que só era complicado porque o CLI não dava suporte. O schema do
`agents --json` **não mudou** (conferido rodando o comando: mesmos campos que
`model.py` parseia). O que mudou foi o que dá para deletar.

36. **`next_step` sai do texto que a sessão já respondeu — o hook não gasta
    mais nada.** O evento `Stop` entrega `last_assistant_message` (o texto da
    resposta que acabou) no stdin do hook. Não existe mais motivo para
    `claude -p --resume`, que era o que continuava a sessão e reacendia o
    próprio hook (#26). Com a chamada de API foram embora as três travas que
    existiam só por causa dela: `TARMAC_HOOK_GUARD`, o arquivo de dedupe e o
    teto diário — mais o lock que só existia porque dedupe e teto são
    read-modify-write. O script caiu de 190 para ~120 linhas, das quais metade
    é comentário, e `hooks/session-end-next-step.sh` foi apagado.

    O que se perde, dito na cara: a nota deixou de ser um resumo escrito por um
    modelo e passou a ser **a própria frase da sessão**, condensada (markdown e
    blocos de código fora) e cortada em 200 caracteres. Não escolhi manter as
    duas versões. O resumo pago era mais bem-acabado, mas podia estar errado —
    e esta coluna é lida como fato ("next_step que não mente"). Uma citação
    truncada erra menos que um resumo alucinado, e agora a nota é reescrita a
    cada turno em vez de só no fim da sessão. Se a qualidade incomodar, o
    caminho de volta é um hook de `SessionEnd` alimentado por essa mesma frase
    (sem `--resume`, input de uma linha) — não o que estava lá.

    As duas listas de cwd (`TARMAC_NEXTSTEP_ONLY` / `_EXCLUDE`) ficaram. Não
    são mais controle de custo: são ruído e privacidade — o texto da nota vai
    parar no banco do painel.

37. **O alerta virou push (hook `Notification`), e o hook não decide nada.**
    Era o buraco admitido no README: "só dispara enquanto o painel está
    rodando", e mesmo rodando podia demorar até 60s. O Claude Code dispara
    `Notification` quando uma sessão precisa de gente — `permission_prompt` uns
    6s depois do prompt aparecer, **adiado a cada tecla digitada**, ou seja
    exatamente quando você não está lá.

    A decisão de projeto: o hook **não** manda notificar. Ele chama
    `tarmac poke`, que é um `collect` só dos targets locais. Quem decide
    continua sendo o ciclo normal — o claim por linha de `transitions`, o mute,
    o filtro de `service`/`mine`. Consequência: um hook que dispara duas vezes,
    ou dispara para algo que o painel não anunciaria, produz exatamente os
    alertas que o ciclo de 60s produziria. A alternativa (o hook lendo o
    payload e mandando o alerta) teria uma segunda fonte de verdade sobre o
    estado da sessão, contra a §2.

    Detalhes que valem a leitura:
    - `local_only`: um target ssh tem 15s de timeout e não pode entrar num
      caminho que precisa alertar em segundos. Targets remotos seguem no ciclo.
    - dois pokes, 3s de intervalo: `agent_needs_input` dispara no instante em
      que a sessão passa a esperar e pode chegar antes de a listagem mudar. O
      segundo poke fecha essa janela; o claim faz ele ser mudo quando o
      primeiro já avisou.
    - `idle_prompt` fica de fora do matcher de propósito: dispara 60s depois de
      **todo** turno de **toda** sessão, e sessão ociosa não está bloqueada.
    - o hook roda destacado (`&`) e sai em 0 — hook nenhum segura a sessão que
      o disparou.
    - `TARMAC_BIN` absoluto no comando instalado: hook não roda com PATH de
      shell de login, e um binário perdido no PATH já custou uma vez o painel
      inteiro em `(stale)`.
    - primeira coleta de um target continua muda (`cold_start`): logo depois de
      instalar, num banco novo, o primeiro poke não alerta. É a mesma regra que
      impede rajada em instalação nova.

38. **`claude respawn` virou ação do painel (tecla `r`).** A linha que motiva:
    sessão background que parou de responder (host morreu, máquina dormiu no
    meio da resposta). Antes só existiam `S` (parar) e `R` (tirar da lista) —
    as duas jogam o trabalho fora. `r` reinicia o processo e a conversa
    continua de onde parou. Pede confirmação: mora ao lado de `R`, e só uma das
    duas é reversível.

39. **`tarmac daemon`**: `claude daemon status` por target. Existe para separar
    "não enxergo a máquina" de "o supervisor morreu" — no segundo caso o
    `agents --json` continua respondendo, a partir do estado em disco, e todas
    as linhas parecem saudáveis enquanto nada responde.

40. **`waitingFor` agora tem vocabulário documentado** (`permission prompt`,
    `input needed`, `sandbox request`, `worker request`, `dialog open`). Os
    cinco ganharam tradução em `strings.py`; **valor desconhecido continua
    aparecendo cru**, e não virou `blocked` genérico: o campo é vocabulário do
    CLI, não nosso, e um valor novo é notícia — apagá-lo jogaria fora a única
    coisa que a linha diz sobre o que ela espera.

41. **Duas defesas nossas ficaram menos críticas (v2.1.248), e nada foi
    removido por causa disso.** (a) Sessão background morta há mais de 48h
    agora aparece como `stopped` em vez de ficar eternamente `blocked` —
    aquelas eram as linhas que inflavam o "maior tempo de espera", que é a
    manchete do painel. (b) Abrir uma sessão já aberta em outro terminal passou
    a ser recusado pelo próprio CLI, então um `terminal_handles` obsoleto não
    consegue mais criar dois processos na mesma conversa. As duas continuam
    valendo como ergonomia; o que mudou é que deixaram de ser a única barreira.

    Teste de mutação (6 mutantes, 6 mortos): varrer target ssh no poke, hook
    aceitar qualquer tipo de notificação, hook parar de rodar destacado, poke
    ignorar o mute, `install` deixar o hook antigo de `SessionEnd` para trás, e
    o hook de `next_step` voltar a chamar `claude`.

## Fora do escopo desta entrega (deliberado)

- Resposta inline (§9.0.2): morta pelo FINDINGS E — não implementada.
- `Fechar abas resolvidas` existe como `tarmac gc-tabs` (nunca automático).
- Notificações: a §7 as proibia; Marcelo reabriu em 23/08/2026 e a §7.3 é o
  resultado — alerta do macOS **na transição** para `blocked`, com claim por
  linha de `transitions` (nunca duas vezes), sem rajada em banco novo, e mute
  com prazo para reunião. O hook `Notification` entrou em 28/08/2026 (#37); o
  que continua fora é re-alerta por escalada (>30min parado não avisa de novo).
- Adaptadores de terminal além do iTerm2: degradação para clipboard já existe;
  Terminal.app/Ghostty/etc. ficam para a generalização pública (§15.2).
