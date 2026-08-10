# Como o tarmac foi construído

> Registro honesto do processo — incluindo o que deu errado. Escrito para quem
> for retomar este repo (inclusive eu daqui a três meses) e para quem quiser
> copiar o método de construir uma ferramenta *com* um agente.

O `tarmac` foi especificado por um humano (`SPEC.md`) e implementado pelo Claude
Code em uma sessão de background, ao longo de dois dias. Três coisas definiram a
qualidade do resultado, e nenhuma delas foi "escrever código":

1. **Verificar as suposições da spec antes de codar** (Tarefa 0).
2. **Documentar toda decisão tomada sem o dono do projeto** (`DECISIONS.md`).
3. **Testar na camada onde os bugs realmente moram** — o que só ficou claro
   depois de errar feio.

---

## 1. Tarefa 0 — o protocolo de reconhecimento

A spec abria com uma regra incomum: *nenhuma linha de código de produção antes
de verificar os fatos*. Sete blocos (A–G) de verificações, cada um gravando
`OK | FALHOU | BLOQUEADO | N/A` em `FINDINGS.md`, com a instrução explícita de
**nunca inventar um resultado** e de que um `BLOQUEADO` é resultado válido.

Isso pagou imediatamente. Do que a spec supunha, a realidade derrubou:

| Suposição da spec | Realidade medida |
|---|---|
| `waitingFor` diz o que a sessão espera | Sessão *background* bloqueada vem **sem** o campo (`status: idle`, sem `waitingFor`) |
| Responder inline com `claude -p --resume` talvez funcione | **Não funciona**: o CLI recusa com erro limpo em sessão viva |
| Agentes de Slack vivem em `~/slack-agents/**` | Vivem em `~/ai-agent-skills/**`, e são `interactive` — o `cwd` é o **único** discriminador |
| `--all` traz as concluídas | Verdade, mas só depois que o worker assenta (levou 8+ min); e sessão *interactive* concluída **some sozinha** do JSON |
| Nome default = `basename(cwd)-xx` | Verdade, mas **minusculizado**: `BackendHealthMonitor` → `backendhealthmonitor-51` |
| O poll pode ser o primeiro a subir o supervisor (risco) | **Não sobe**: `agents --json` lê estado sem daemon. O risco não existia |

Duas verificações precisaram de intervenção humana e viraram `BLOQUEADO` até o
dono agir: o alias SSH da EC2 e a permissão de Automação do macOS para o iTerm2.
Isso é o protocolo funcionando — o agente parou e reportou em vez de improvisar.

**Lição transferível:** um bloco de reconhecimento no começo custa uma sessão e
evita construir sobre suposição. Metade das decisões de design mudou por causa
dele.

---

## 2. A regra de ouro que moldou o design

`claude agents --json --all` é a **única** fonte de verdade. Nunca parsear os
arquivos internos do CLI (JSONL de transcript, `state.json` de jobs): o formato
é interno e muda entre releases, e toda ferramenta de terceiros que os lê quebra
numa atualização silenciosa.

A regra não ficou só na prosa — virou **teste**: um caso de teste falha se
qualquer `.py` do pacote mencionar `.claude/projects` ou `state.json`, e o
`preflight.sh` repete a checagem. Regra que não é executável é decoração.

Corolário que apareceu na prática: como o JSON é a única fonte, o parser precisa
ser tolerante (campo desconhecido é ignorado, campo ausente não derruba), e o
payload íntegro vai para `raw_json` no banco — quando o schema mudar, o debug
começa com o dado real em mãos.

---

## 3. Arquitetura em uma página

```
claude agents --json --all   (local  |  ssh)
            │
     collect.py      falha isolada por target; offline × erro pelo stderr;
            │        backoff 60s → 2min → 5min
            ▼
     SQLite (~/.tarmac/tarmac.db)
       ├── camada derivada   espelho descartável do JSON (+ raw_json)
       │                     transitions → blocked_since → wasted_total
       └── camada de intenção  alias, lembrete, checklist, next_step, tarefas
            │                  (sobrevive à sessão sumir da listagem)
            ▼
     derive.py     view única: badge, seções, ordenação, escalada de cor
            │
      ┌─────┴─────┐
   TUI (textual)  SwiftBar
   primário       fallback laptop
```

Decisões que sustentam o resto:

- **Chave é `(target_id, session_id)`**, nunca o id sozinho — ids não são
  globais entre máquinas nem entre `CLAUDE_CONFIG_DIR`.
- **Sessão que some não é deletada** (`gone = 1`): a intenção sobrevive.
- **"Não consigo ver" ≠ "está com problema"**: alvo atrás de VPN fica cinza e
  calmo; só erro real (chave, binário) acende `⚠`.
- **Número honesto**: se a sessão bloqueou durante a janela cega, exibe `≥`,
  nunca um valor falsamente preciso.

---

## 4. Os incidentes — e o que cada um ensinou

### 4.1 O hook que se auto-alimentava (queimou limite de uso)

O recurso mais valioso da spec (§10) era gerar sozinho o "o que ficou pendente"
ao fim de cada sessão, via `claude -p --resume <id>`. Instalado nas duas
máquinas, o consumo do limite disparou.

**Causa:** `claude -p --resume` **continua** a sessão. Quando termina, dispara
`SessionEnd` de novo, com o mesmo id → o hook roda outra vez → laço. E cada
volta reenvia o **transcript inteiro** como input.

**Evidência que fechou o diagnóstico:** a fila tinha 35 entradas para apenas
**5 sessões distintas** — ~7 voltas cada.

**Correção:** hook removido das duas máquinas; reescrito com quatro travas
independentes (env `TARMAC_HOOK_GUARD` exportado ao processo filho, dedupe por
sessão, teto diário, allow/deny por `cwd`), modelo barato por padrão, e
**desligado por padrão** (`tarmac hook install` é opt-in explícito). Sete testes
com um `claude` falso que **reencena a recursão** provam que o laço morre.

**Lição:** um hook que chama a própria ferramenta que o dispara é um laço até
prova em contrário. E "custa uma chamada por sessão" merece a pergunta seguinte:
*qual o tamanho do input dessa chamada?*

### 4.2 O crash do `logs` e o perigo de consertar por hipótese

`l` (ver logs) derrubava o app. A primeira hipótese — conteúdo com `[colchetes]`
sendo interpretado como markup — levou a um "fix" plausível.

O que salvou: **teste de mutação**. Reintroduzi o bug de propósito e **a suíte
passou** — ou seja, o fix não estava corrigindo nada. Investigando com dado
real, a causa era outra: `claude logs` não devolve texto, devolve um **replay de
tela inteira** (74KB de escapes de cursor). Renderizar isso num widget é
ilegível *e* quebra. A correção certa foi **abrir os logs num terminal**.

**Lição:** "os testes passam" não prova que o fix funciona. Quebrar o fix de
propósito e exigir que a suíte falhe é barato e pega a hipótese errada.

### 4.3 Bugs que só aparecem no app real

Uma sequência deles, todos invisíveis para testes unitários com mock:

- **SQLite cross-thread**: as ações rodam em worker thread e reusavam a conexão
  da thread principal → toda ação de abrir aba explodia.
- **Refresh cancelando ações**: o poll de 60s rodava como worker `exclusive` no
  grupo default e **matava ações em voo** (abrir, parar, remover) sem erro
  visível. Achado por um teste E2E novo, não por inspeção.
- **Sessão sumida renderizando como viva**: `gone = 1` só era respeitado para
  `done`/`idle`; quem sumia `working`/`blocked` ficava para sempre — e uma
  bloqueada fantasma **inflava o badge**, que é o único alerta que existe.
- **Glob que não cobria o próprio diretório**: `~/ai-agent-skills/**` não casava
  `~/ai-agent-skills`, então um agente de chatops vazou para a lista principal.
- **Janela do painel sendo sequestrada**: o AppleScript lia `current window` e,
  numa corrida, escrevia o comando da sessão **dentro da janela do painel**.
- **Painel aberto por `Initial Text`**: o profile do iTerm *digitava* o comando
  num zsh interativo; um caractere perdido na corrida virou
  `i~/.local/bin/tarmac`. Trocado por `Custom Command` + launcher.
- **E a correção acima criou o bug seguinte**: sem shell interativo, o `PATH`
  ficou mínimo e o coletor não achou `claude` → todas as linhas viraram
  `(stale)`. O painel estava **certo**; o ambiente é que estava quebrado.

---

## 5. Como este projeto passou a ser testado

O ponto de virada foi uma cobrança direta do dono: *"melhore seus testes para eu
não cair em bug toda vez que uso"*. O diagnóstico honesto: os testes eram
unitários com mocks, e **todos** os bugs que chegaram no usuário viviam fora
dessa camada — o app real, o binário instalado, o processo filho, o ambiente.

Quatro camadas hoje, a última sendo um portão:

| Camada | O que só ela pega |
|---|---|
| `test_model/dates/collect_derive` | lógica pura, contra fixtures reais do `--json` |
| `test_cli_smoke.py` | roda **o CLI como subprocesso**, um teste por subcomando: import quebrado, argparse errado, crash na invocação real |
| `test_e2e_panel.py` | **varre todas as teclas em todos os tipos de linha** do app real, com `osascript`/`ssh`/`claude` falsos no PATH |
| `test_hook.py` | hook de ponta a ponta, com um `claude` falso que reencena a recursão |
| `scripts/preflight.sh` | suíte + **binário instalado** respondendo + hook off + coleta com **PATH mínimo** + profile sem `Initial Text` + regra de ouro |

Mais duas práticas:

- **Teste de mutação antes de declarar corrigido** (seção 4.2).
- **`tests/conftest.py` isola o `~/.tarmac` real** e faz explodir qualquer teste
  que tente abrir o banco de verdade — porque um teste vazou uma tarefa para o
  banco do dono, e auditar fixture por fixture não escala.

Regra de processo: **não dizer "está pronto" sem o preflight verde.**

---

## 6. O que o agente decidiu sozinho

A instrução foi *"não fique travado por uma decisão minha; documente o que você
decidiu"*. `DECISIONS.md` tem 28 itens; os que mais mudam o produto:

- TUI display-only → **interativo** (revertido a pedido do dono, na hora)
- Bloqueio sem `waitingFor` → rótulo neutro `blocked`, sem adivinhar
- Lembrete **não** tira uma sessão bloqueada de PRECISA DE VOCÊ
- Sempre **janela nova** por sessão (o painel virou a fila; terminal é
  descartável)
- Retenção de 24h de sessões de serviço mira o **SQLite**, não a coleta

E uma feature que não estava na spec, pedida no meio do caminho: **tarefas
avulsas**. Escrevo *"no benji-dp, preciso dividir os rampids em ssps"*, ela vive
em AGENDADO, e ao abrir o painel **infere a pasta** pelo histórico de `cwd`s
onde eu realmente trabalho (worktrees dobrados na raiz do repo); empate só
resolve sozinho com dominância 3:1, senão pergunta com as pastas mais usadas.
Sem varrer filesystem — o histórico do próprio espelho é a fonte, coerente com a
regra de ouro.

---

## 7. Se você for repetir este método

1. Escreva a spec como documento de contexto permanente, com decisões marcadas
   como fechadas — e um bloco de reconhecimento bloqueante no início.
2. Exija `BLOQUEADO` como resultado válido. Agente que improvisa em cima de
   suposição custa mais caro que agente que para e pergunta.
3. Peça o registro das decisões tomadas sem você. É onde o desalinhamento
   aparece cedo, barato.
4. Cobre teste na camada do usuário: o binário instalado, o app real, o
   ambiente real. Mock testa o que você imaginou; subprocesso testa o que
   acontece.
5. Antes de aceitar um "corrigido", peça a mutação: quebre o fix e veja a suíte
   falhar.
6. Desconfie de qualquer coisa que chame o modelo dentro de um hook, timer ou
   retry. Pergunte o tamanho do input, e o que dispara a próxima volta.
