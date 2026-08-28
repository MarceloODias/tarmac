# tarmac — SPEC

> *Um painel para as sessões do Claude Code que estão esperando por você.*

> Documento de contexto permanente. Leia antes de escrever código.
> Decisões aqui são **fechadas**: não reabra sem discussão explícita.

---

## 1. Problema

Rodo muitas sessões do Claude Code em paralelo, em duas máquinas (Mac local e uma
EC2 via SSH). O custo não é computacional, é mental: esqueço onde iniciei cada
agente, em qual pasta, e o que ficou pendente de mim. O `claude agents` nativo
resolve a visão por máquina, mas não agrega hosts, não guarda intenção
("retomo terça"), e não vive no canto da tela.

**O que esta ferramenta faz:** responde, sem eu pedir, à pergunta
*"o que existe, onde está, e o que espera por mim?"* — agregando todos os
targets numa linha da menu bar do macOS.

**O que ela não faz:** não substitui o `claude agents`. Não renderiza
transcripts. Não gerencia worktrees. Não orquestra agentes.

---

## 2. Regra de ouro sobre a fonte de dados

A **única** fonte de verdade sobre sessões é:

```bash
claude agents --json --all
```

**Nunca** parsear os arquivos JSONL em `~/.claude/projects/`. A documentação
oficial é explícita: o formato das entradas é interno ao Claude Code e muda
entre releases. Qualquer parser direto quebra numa atualização silenciosa.

Corolários:

- Ler `~/.claude/jobs/<id>/state.json` também está fora. Mesmo motivo.
- Se o `--json` não expõe um dado que queremos, a resposta é viver sem ele
  ou derivá-lo de outra forma — não é ir no disco.
- O agent view está em *research preview*. O schema pode mudar. O parser deve
  ser tolerante: campos desconhecidos são ignorados, campos ausentes não
  derrubam a coleta.

### 2.1 Tarefa 0 — protocolo de reconhecimento

**Esta é a primeira tarefa e é bloqueante.** Nenhuma linha de código de produção
antes de ela terminar.

Várias decisões desta spec dependem de fatos que eu não verifiquei: o schema real
do `--json` nesta versão, se o SSH não interativo funciona, se o supervisor herda
um ambiente pobre, se dá para responder inline. Codar antes de saber é construir
sobre suposição.

**Como executar:** rodar cada verificação abaixo, gravar o resultado em
`FINDINGS.md` na raiz do repo, e **parar e me reportar** ao terminar. Nunca
inventar um resultado nem seguir adiante com um item falhando: um `BLOQUEADO` é
um resultado válido e útil.

Formato de cada entrada em `FINDINGS.md`:

```markdown
### A2 — schema do `agents --json`
Comando:  claude agents --json --all
Status:   OK | FALHOU | BLOQUEADO | N/A
Saída:    <trecho relevante, sanitizado>
Conclusão: <o que isso decide na spec>
```

---

#### A. Ambiente local (Mac)

| # | Comando | O que decide |
|---|---|---|
| A1 | `claude --version` | Versão mínima. A spec depende de: agent view ≥ 2.1.139, `/resume` em agent view ≥ 2.1.212, rename propagando ao `agents` ≥ 2.1.221, `--resume <id>` de qualquer diretório ≥ 2.1.223. Listar quais recursos estão disponíveis e quais não |
| A2 | `claude agents --json --all` | **O schema do banco.** Salvar sanitizado em `fixtures/agents-local.json` |
| A3 | `claude agents --json` (sem `--all`) | Confirmar que `--all` é o que traz concluídas |
| A4 | comparar A2 com a tabela da seção 2.2 | Listar: campos previstos que **não** existem, e campos existentes **não** previstos |

#### B. EC2 e SSH

| # | Comando | O que decide |
|---|---|---|
| B1 | `ssh -o BatchMode=yes ec2-runner true` | Chave sem senha. Falhou → `BLOQUEADO`, nada mais funciona |
| B2 | `ssh ec2-runner 'which claude'` | Valor de `claude_bin` no `targets.yaml` |
| B3 | `ssh ec2-runner '<bin> --version'` | Divergência de versão entre Mac e EC2 |
| B4 | `ssh -o BatchMode=yes ec2-runner '<bin> agents --json --all'` | **O teste que mais importa.** É exatamente como o coletor roda. Se falhar aqui e funcionar em SSH interativo, o problema é ambiente |
| B5 | mesmo comando 3× com `time`, antes e depois de configurar `ControlMaster` | Ganho real da multiplexação. Se for irrelevante, simplificar |
| B6 | `ssh -O check ec2-runner` | Como detectar socket obsoleto após queda de VPN (seção 4.4) |

#### C. Supervisor e ambiente herdado

O risco mais sutil da spec. O supervisor captura o ambiente do primeiro shell que
o inicia; se for o poll, sessões despachadas depois herdam um ambiente pobre.

| # | Verificação | O que decide |
|---|---|---|
| C1 | `ssh ec2-runner '<bin> daemon status'` | Estado inicial: supervisor de pé ou não, versão, nº de workers |
| C2 | Com o supervisor **parado**: rodar B4 e repetir C1 | **`agents --json` sobe supervisor?** Se sim, o poll é um risco real |
| C3 | Se C2 for sim: despachar um job de shell e ler o ambiente que ele herdou — `<bin> --bg --exec` com um comando que imprima `PATH` e resolva `which git python3`, depois `<bin> logs <id>` | O ambiente é utilizável ou está pobre? |
| C4 | conclusão de C3 | Se pobre: documentar o bloco `env` no `.claude/settings.json` do projeto como mitigação obrigatória, e considerar nunca deixar o poll ser o primeiro a tocar o supervisor |

#### D. Classes de sessão (agentes de Slack)

| # | Verificação | O que decide |
|---|---|---|
| D1 | `cwd` real das sessões dos agentes de Slack no JSON da EC2 | O glob de `match_cwd` na seção 3.3 |
| D2 | elas aparecem como `kind: background` ou `interactive`? | Se a classificação por `cwd` basta ou precisa de reforço |
| D3 | quantas sessões elas geram por dia | Dimensiona a retenção de 24h |

#### E. Resposta inline (experimento da seção 9.0.2)

| # | Verificação | O que decide |
|---|---|---|
| E1 | Com uma sessão de background **bloqueada**, tentar `claude -p --resume <session-id> "<resposta>"` | Se funcionar, metade das aberturas de aba desaparece |
| E2 | Registrar a mensagem de erro exata, se houver | Distinguir "não suportado" de "conflito de transcript" |

⚠️ Fazer em uma sessão de teste descartável, nunca numa sessão de trabalho real —
a hipótese é que dois processos no mesmo transcript causem problema.

#### F. iTerm2 e AppleScript

| # | Verificação | O que decide |
|---|---|---|
| F1 | AppleScript que cria aba e retorna `id of current session` | Formato do handle da seção 9.0 |
| F2 | AppleScript que percorre janelas/abas e foca por esse id | Se "abrir ou focar" é viável como especificado |
| F3 | comportamento quando a aba foi fechada | Confirma o caminho `missing` |

#### G. Heurística de nomes

| # | Verificação | O que decide |
|---|---|---|
| G1 | Quantas sessões atuais casam com `^<basename(cwd)>-[a-z0-9]{2}$` | Valida a detecção de "nunca nomeada" da seção 6.5 |

---

#### Sanitização das fixtures

As fixtures vão para um repositório público. Antes de versionar qualquer saída
de `--json`:

- substituir `cwd` reais por caminhos genéricos (`/home/user/project-a`)
- remover hostnames, usuários e nomes de cliente
- preservar **a forma** dos dados: mesmos campos, mesmos tipos, mesma cardinalidade

#### Portão de saída

Ao terminar, apresentar `FINDINGS.md` e **parar**. Eu reviso e decido o que muda
na spec antes de qualquer código de produção. Itens `FALHOU` ou `BLOQUEADO` viram
decisão minha, não improviso do agente.

### 2.2 Campos esperados (referência, confirmar na prática)

| Campo | Presença | Descrição |
|---|---|---|
| `cwd` | sempre | diretório de trabalho |
| `kind` | sempre | `interactive` ou `background` |
| `startedAt` | sempre | epoch em **milissegundos** |
| `id` | sessões background | short ID; usável em `attach`/`logs`/`stop` |
| `state` | sessões background | `working` \| `blocked` \| `done` \| `failed` \| `stopped` |
| `pid`, `status` | processo vivo | PID e status atual |
| `waitingFor` | quando `status = waiting` | `permission prompt`, `input needed`, `sandbox request`, `worker request`, `dialog open` |
| `sessionId` | quando existe | UUID completo, usável com `claude --resume` |
| `name` | quando existe | nome da sessão |

Sessões `interactive` que não foram nomeadas recebem um nome default derivado
do diretório mais sufixo de dois caracteres (ex.: `my-app-3f`). Esse default
**não** é handle de resume — só nomes definidos explicitamente resolvem em
`claude --resume <name>`.

---

## 3. Modelo conceitual: target

A unidade não é "host". É **target** = identidade + máquina + config dir.

Justificativa: quando `CLAUDE_CONFIG_DIR` está definido, o supervisor usa aquele
diretório e roda como **instância separada, com suas próprias sessões**. Logo,
mesma máquina + mesmo usuário + config dirs diferentes = dois universos
independentes de sessões. Tratar isso como "host" perde informação.

### 3.1 `targets.yaml`

```yaml
targets:
  - id: mac-marcelo
    label: "Mac"
    owner: marcelo
    mine: true
    transport: local
    config_dir: ~/.claude

  - id: mac-marcelo-alt
    label: "Mac (conta alt)"
    owner: marcelo
    mine: true
    transport: local
    config_dir: ~/.claude-alt

  - id: ec2-marcelo
    label: "EC2"
    owner: marcelo
    mine: true
    transport: ssh
    ssh_host: ec2-runner
    ssh_user: marcelo
    claude_bin: /home/marcelo/.local/bin/claude
    config_dir: ~/.claude

  - id: ec2-frank
    label: "EC2 (frank)"
    owner: frank
    mine: false
    enabled: false
    transport: ssh
    ssh_host: ec2-runner
    ssh_user: frank
    claude_bin: /home/frank/.local/bin/claude
```

Campos: `id` (único, imutável), `label` (exibição), `owner`, `mine`
(bool, controla o filtro default), `enabled` (default `true`),
`transport` (`local` | `ssh`), `ssh_host`, `ssh_user`, `claude_bin`,
`config_dir`.

### 3.2 Multi-usuário — regras não negociáveis

1. **Chave primária é `(target_id, session_id)`.** Nunca `session_id` sozinho.
   IDs não são globais entre máquinas nem entre config dirs.
2. **Nunca ler o `~/.claude` de outro usuário via `sudo`.** A coleta acontece
   sempre *como* aquele usuário, por SSH, com a chave dele. Isso deixa a
   fronteira de permissão do SO fazendo o trabalho: se o acesso for revogado,
   o painel simplesmente para de enxergar, sem vazamento e sem código especial.
3. **Filtro por target existe desde o começo**, com `mine: true` como default.
   Ver sessões de terceiros misturadas com as minhas reintroduz exatamente o
   fardo mental que a ferramenta existe para remover.
4. `config_dir`, quando presente e diferente do default, é exportado como
   `CLAUDE_CONFIG_DIR` no comando de coleta e em **todas** as ações
   (`attach`, `logs`, `resume`). Um attach no config dir errado não acha a
   sessão.

### 3.3 Classes de sessão: `owned` vs `service`

Nem toda sessão é minha para conduzir. Rodam na EC2 alguns agentes ligados ao
Slack por websocket: **cada mensagem no Slack inicia uma sessão nova**. O fluxo
inteiro deles vive no Slack — eu nunca vou anexar um terminal, nunca vou
agendar, nunca vou escrever checklist.

Se elas entrarem no fluxo principal, quebram tudo: inflam o badge, poluem o
tempo de espera, e enterram as minhas cinco sessões reais debaixo de quarenta
sessões de chatops.

Duas classes, então:

- **`owned`** — sessões que eu conduzo. Todo o comportamento das seções 6 a 10
  se aplica.
- **`service`** — sessões dirigidas por automação. Só existem como contagem.

#### Classificação

O `--json` não expõe o agente nem o modo, então o discriminador confiável é o
`cwd`, com o nome como reforço:

```yaml
  - id: ec2-marcelo
    transport: ssh
    session_classes:
      - class: service
        label: "slack-agents"
        match_cwd: "/home/marcelo/slack-agents/**"
      - class: service
        label: "chatops"
        match_name: "^slack-"
```

Sem regra que case, a sessão é `owned`. O default é o comportamento completo —
ignorar exige declaração explícita.

#### O que muda para `service`

| | `owned` | `service` |
|---|---|---|
| Aparece na lista principal | sim | **não** |
| Conta no badge | sim | **não** |
| Entra em `wasted_total` (seção 5.3) | sim | **não** |
| Ganha `session_meta` / checklist / agendamento | sim | **não** |
| Hook de `next_step` (seção 10) | sim | **não** |
| Retenção após concluir | indefinida | 24h, depois some |

A retenção curta importa: um agente de Slack movimentado gera dezenas de sessões
por dia, e `--all` traz as concluídas. Sem poda, o banco vira log de chat.

#### A exceção que vale um sinal

`service` não significa invisível. Uma sessão de serviço **travada** é um
serviço quebrado: ninguém vai responder pelo Slack a um `input needed` que o
fluxo não previa. Ela fica presa até alguém notar.

Então a linha de serviços mostra a contagem de ativas e, só quando existir, um
alerta de travada além de um limite (default 30 min). Nada mais. É o único caso
em que o silêncio custaria caro.

---

## 4. Coleta

### 4.1 Cadência

- **Poll de fundo: 60 segundos.**
- **Refresh imediato ao abrir o menu** — isso torna a latência do poll
  irrelevante na prática. Nunca olho para dado velho.
- Refresh imediato após qualquer ação que muda estado (`stop`, `attach`).
- Timeout por target: 15s. Um target lento nunca bloqueia os outros.
- Coleta de targets em paralelo (thread pool). Falha de um target é isolada:
  registra `last_error` e `last_seen_at`, mantém os dados anteriores marcados
  como stale, e segue.

### 4.2 Comando por transport

**local:**
```bash
CLAUDE_CONFIG_DIR=<config_dir> claude agents --json --all
```

**ssh:**
```bash
ssh -o BatchMode=yes <ssh_user>@<ssh_host> \
  'CLAUDE_CONFIG_DIR=<config_dir> <claude_bin> agents --json --all'
```

### 4.3 Armadilhas conhecidas do SSH

- **PATH:** shell não-interativo tipicamente não encontra `claude`.
  Por isso `claude_bin` é caminho absoluto e obrigatório em targets SSH.
- **ControlMaster:** sem multiplexação, cada poll faz handshake completo.
  Exigir no `~/.ssh/config`:

  ```
  Host ec2-runner
      ControlMaster auto
      ControlPath ~/.ssh/cm-%r@%h:%p
      ControlPersist 10m
      ServerAliveInterval 30
  ```

  O `%r` no `ControlPath` inclui o usuário remoto — logo já dá socket separado
  por target, que é o comportamento correto para multi-usuário.
- **`BatchMode=yes`** para que um problema de auth falhe rápido em vez de
  pendurar esperando senha.

### 4.4 Targets intermitentes — a EC2 está atrás de VPN

A EC2 fica inalcançável por horas quando a VPN expira e eu não estou na máquina.
Isso é **normal, não é falha**, e a distinção é crítica: sem notificações
(seção 7), o badge é o único alerta que existe. Um `⚠` aceso todas as noites me
treina a ignorar o badge, e aí a ferramenta inteira morre.

**Princípio:** *"não consigo ver" ≠ "está com problema"*. As sessões na EC2
continuam rodando normalmente com a VPN caída — o supervisor não depende de mim
estar conectado. O que se perde é observabilidade, não trabalho.

#### Configuração

```yaml
  - id: ec2-marcelo
    transport: ssh
    expect_intermittent: true    # VPN; ficar offline é esperado
    offline_after: 3             # ciclos falhados antes de marcar offline
```

#### Classificar a falha, não só registrá-la

O `stderr` do SSH distingue os casos, e eles merecem tratamentos opostos:

| Sintoma | Estado | UI |
|---|---|---|
| timeout, `No route to host`, `Network is unreachable` | `offline` | cinza, calmo, sem `⚠` |
| `Permission denied`, host key, binário não encontrado | `error` | `⚠`, ruidoso |

Com `expect_intermittent: true`, apenas o segundo grupo acende `⚠`. O primeiro
é rotina.

#### Mecânica

- `-o ConnectTimeout=5` — falhar rápido em vez de pendurar 15s por ciclo.
- Socket do `ControlMaster` fica obsoleto quando a VPN cai: verificar com
  `ssh -O check` e remover o `ControlPath` morto antes de tentar de novo.
- **Backoff**: 60s → 2min → 5min (teto). Reset imediato no primeiro sucesso.
  Não faz sentido tentar 180 vezes durante uma noite.

#### Preservar o último retrato, com idade explícita

Nunca apagar os dados de um target offline, e nunca exibi-los como se fossem
atuais. A linha do target mostra sempre a idade da leitura:

```
EC2  ·  offline há 6h  ·  última leitura 03:12
  ▫ nightly-backfill        estava working        (há 6h)
```

#### Ao reconectar: tempos de espera são limites inferiores

Se uma sessão estava `working` na última leitura e aparece `blocked` na
reconexão, **não sabemos quando ela bloqueou**. Registrar `blocked_since` como
desconhecido e exibir `≥ 6h`, nunca um número falso e preciso. Dado honesto vale
mais que dado bonito.

---

## 5. Armazenamento

SQLite em `~/.tarmac/tarmac.db`. Duas camadas, separadas de propósito.

### 5.1 Camada derivada (espelho do `--json`, descartável)

```sql
CREATE TABLE sessions (
  target_id     TEXT NOT NULL,
  session_id    TEXT NOT NULL,   -- 'id' (background) ou 'sessionId' (interactive)
  short_id      TEXT,            -- 'id', para attach/logs/stop
  uuid          TEXT,            -- 'sessionId', para claude --resume
  name          TEXT,
  kind          TEXT,            -- interactive | background
  state         TEXT,            -- working | blocked | done | failed | stopped
  status        TEXT,
  waiting_for   TEXT,
  cwd           TEXT,
  pid           INTEGER,
  started_at    INTEGER,         -- epoch ms, como vem
  first_seen_at INTEGER,
  last_seen_at  INTEGER,
  gone          INTEGER DEFAULT 0,
  raw_json      TEXT,            -- payload íntegro, para debug de mudança de schema
  PRIMARY KEY (target_id, session_id)
);
```

Esta tabela pode ser apagada e reconstruída a qualquer momento sem perda.
Guardar `raw_json` é barato e salva a vida quando o schema mudar.

### 5.2 Camada de intenção (minha, preservada)

```sql
CREATE TABLE session_meta (
  target_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  alias           TEXT,     -- meu nome para a sessão; tem precedência na exibição
  next_step       TEXT,     -- "o que falta", 1 linha
  next_step_origin TEXT,    -- 'auto' | 'manual'
  due_at          INTEGER,  -- epoch ms UTC; quando isto volta a importar
  due_label       TEXT,     -- entrada original: "na segunda", "5h"
  hide_until_due  INTEGER DEFAULT 0,  -- 1 = some da lista até vencer
  tags            TEXT,     -- CSV simples
  pinned          INTEGER DEFAULT 0,
  notes           TEXT,
  updated_at      INTEGER,
  PRIMARY KEY (target_id, session_id)
);
```

**Só isto, mais o checklist da seção 6.4.** Qualquer outro atributo é derivado
do JSON. A tentação de cachear mais coisa aqui vira dado divergente em duas
semanas.

Sessões que somem do `--json` são marcadas `gone = 1`, não deletadas — o
`session_meta` sobrevive, e o transcript continua acessível por
`claude --resume` mesmo depois de removida da lista do agent view.

### 5.3 Transições

A cada ciclo, comparar `state` anterior × atual e registrar:

```sql
CREATE TABLE transitions (
  id INTEGER PRIMARY KEY,
  target_id TEXT, session_id TEXT,
  from_state TEXT, to_state TEXT, at INTEGER
);
```

Esta tabela é a fonte de **`blocked_since`** — o instante da última entrada em
`blocked`. Sem notificações (seção 7), `blocked_since` é o dado mais importante
do painel inteiro: é ele que responde *"há quanto tempo esta sessão está parada
me esperando?"*, que é a pergunta que custa dinheiro.

Derivar em cada ciclo:

- `blocked_since` — última transição `* → blocked` ainda vigente
- `wait_seconds` — `now - blocked_since`
- `wasted_total` — soma de todos os intervalos em `blocked` por sessão e por dia

Contar **apenas** sessões `owned` (seção 3.3): tempo de agente de chatops
esperando não é tempo meu desperdiçado, e misturar os dois arruína a métrica.

`wasted_total` é o KPI da própria ferramenta. Se o tempo agregado de espera cair
semana a semana, o painel está funcionando. Se não cair, ele é decoração.

---

## 6. Agendamento, checklist e nomes

O painel **não executa nada por agendamento**: não inicia sessões, não dispara
prompts, não roda tarefas. Agendamento aqui é só memória — "isso volta a importar
em X". Se algum dia eu quiser execução agendada de verdade, o Claude Code já tem
tarefas agendadas nativas (`/loop`, scheduled tasks) e o painel **não** deve
reimplementar isso.

### 6.1 Um campo, três comportamentos

`snooze_until` foi substituído por `due_at` + `hide_until_due`. As três situações
que eu quero são combinações dos mesmos dois campos:

| Intenção | `due_at` | `hide_until_due` | Efeito |
|---|---|---|---|
| "some até segunda" | futuro | `1` | sai da lista principal, volta em `due_at` |
| "me lembra em 5h" | futuro | `0` | fica visível com chip `⏱ 5h` |
| vencido | passado | qualquer | sobe para **PRA HOJE** e conta no badge |

Vencido e não reconhecido **não some sozinho**. Fica no topo até eu abrir a
sessão, reagendar, ou marcar como resolvido. Lembrete que se apaga sozinho não é
lembrete.

### 6.2 Entrada em linguagem natural

A ação `Lembrar em…` abre um campo de texto livre. Formas que precisam funcionar:

```
5h · 30min · 2d · 3 dias · amanhã · amanhã cedo
segunda · na segunda · próxima terça
sexta 14h · dia 15 · 15/09
fim do dia · fim da semana · próximo mês
```

Parsing nesta ordem:

1. **Regex determinístico** para as formas acima. Cobre a esmagadora maioria dos
   casos, custo zero, resultado previsível.
2. **Fallback para `claude -p`** só quando o regex não casa:

   ```bash
   claude -p --output-format json \
     "Hoje é <ISO local>. Converta para timestamp ISO 8601: '<entrada>'.
      Responda apenas o timestamp." | jq -r '.result'
   ```

Nunca falhar em silêncio: entrada não interpretada mostra erro e mantém o campo
aberto.

### 6.3 Âncoras e fuso

- **Hora default para datas sem hora:** 09:00. `na segunda` = segunda 09:00.
- **`fim do dia`** = 18:00 do dia corrente.
- **Durações relativas** (`5h`, `30min`) são exatas a partir de agora, sem
  arredondamento.
- **Dia da semana** sempre aponta para a próxima ocorrência futura: `segunda`
  dito numa segunda significa a segunda seguinte, não hoje.
- **Fuso é sempre o do Mac**, nunca o do target. O agendamento é sobre mim, não
  sobre a máquina onde a sessão roda — a EC2 em UTC é irrelevante aqui.
- Armazenar sempre epoch ms UTC; converter só na exibição.
- Exibir a forma humana ao lado da absoluta: `em 3d (seg, 11/08 09:00)`.
  Confirmar o que foi entendido é o que evita o lembrete cair no dia errado.

### 6.4 Checklist por sessão

```sql
CREATE TABLE session_checklist (
  id         INTEGER PRIMARY KEY,
  target_id  TEXT NOT NULL,
  session_id TEXT NOT NULL,
  position   INTEGER NOT NULL,
  text       TEXT NOT NULL,
  done       INTEGER DEFAULT 0,
  created_at INTEGER,
  done_at    INTEGER
);
```

- O checklist pertence à **sessão**, não ao lembrete. O lembrete traz a sessão de
  volta; o checklist diz o que fazer quando ela voltar.
- Itens **não** têm prazo próprio. Se um item precisa de prazo próprio, ele é uma
  sessão, não um item.
- A linha do menu mostra progresso quando há checklist: `[2/5]`.
- `next_step` (gerado automaticamente, seção 10) e checklist (escrito por mim)
  coexistem e nunca se sobrescrevem: um é o que a sessão disse, o outro é o que
  eu decidi.

### 6.5 Nomes

Nomes automáticos não me ajudam a lembrar de nada. Três camadas, da melhor para
a pior:

**1. Plan mode nomeia sozinho, e nomeia bem.** Aceitar um plano em plan mode
nomeia a sessão a partir do conteúdo do plano, a menos que já exista um nome.
Isso é muito melhor que o título derivado do primeiro prompt, e não custa nada —
é o caminho que eu já uso.

**2. `--name` no dispatch** (convenção da seção 11), para sessões longas.

**3. `alias` local do painel**, para tudo que escapou das duas anteriores.

#### Detectar sessões sem nome de verdade

Sessões nunca nomeadas recebem um nome default que é o nome do diretório mais
um sufixo de dois caracteres (ex.: `my-app-3f`). O padrão é reconhecível:

```
^<basename(cwd)>-[a-z0-9]{2}$
```

Casou → marcar a linha com `✎` e oferecer `Nomear…`. É um cutucão, não um
bloqueio.

#### Sugerir o nome em vez de me fazer inventar

`Nomear…` oferece três candidatos gerados a partir da própria conversa:

```bash
claude -p --resume <session-id> --output-format json \
  "Sugira 3 nomes curtos em kebab-case para esta sessão, um por linha." \
  | jq -r '.result'
```

Escolho um ou digito o meu. **Ressalva:** o `alias` é local ao painel e não
propaga para o Claude Code, então `claude --resume <alias>` não resolve. Não é
problema — a ação `Copiar comando de resume` usa o UUID de qualquer forma, e o
ponto do painel é justamente eu não precisar decorar nome nenhum. Se eu quiser o
nome real, `Ctrl+R` no `claude agents`.

---

## 7. O painel fica sempre aberto — e avisa ao entrar em PRECISA DE VOCÊ

**Decisão original:** não há notificações. Nem macOS, nem push, nem hook
`Notification`. O painel fica aberto na menu bar o tempo todo, e o badge **é** o
alerta.

Isso removia: idempotência de notificação, `due_notified_at`, o dilema do
`launchd`, e a categoria inteira de bugs "notificou duas vezes / não notificou".

> **Revisada em 23/08/2026 (ver §7.3).** O badge só alerta enquanto eu estou
> olhando para a tela, e uma sessão bloqueada é justamente o caso em que eu não
> estou. Uma notificação **de transição** foi adicionada. O que a decisão
> original comprava — idempotência — não foi devolvido: continua sendo um
> requisito, agora pago com a tabela `notifications` em vez de com a ausência
> do recurso.

**Em troca, o badge passa a ser load-bearing.** Duas consequências que a
implementação precisa respeitar:

1. **Nunca pode estar escondido.** Isto sela a escolha de menu bar sobre janela:
   uma janela vai para trás de outra janela. A menu bar, não.
2. **Tem que comunicar urgência sem clique.** Contagem não basta — ver `⏸ 2` não
   diz se são 2 minutos ou 2 horas de sessão parada. Ver `⏸ 45m` diz.

O push mobile do Remote Control continua existindo e é ortogonal a isto: serve
para quando eu **não** estou na frente do Mac. O painel serve para quando estou.

---

### 7.1 Tempo de espera é o dado central

O problema real não é "não sei que sessões existem". É *"o Claude parou há 40
minutos esperando eu apertar uma tecla e eu não vi"*. Um agente bloqueado é pior
que um agente lento: ele custou o tempo de setup e não está produzindo nada.

Por isso toda linha `blocked` exibe `wait_seconds` em destaque, e o badge mostra
**a maior espera**, não a contagem:

| Espera | Badge | Tratamento |
|---|---|---|
| < 5 min | `⏸ 2 · 3m` | normal |
| 5–30 min | `⏸ 2 · 12m` | cor de destaque |
| > 30 min | `⏸ 2 · 45m` | cor de alarme + linha piscando no menu |

### 7.2 Tipos de bloqueio

O campo `waitingFor` do `--json` distingue o que a sessão está esperando:

- **`permission prompt`** — aprovar um comando. Decisão de 2 segundos.
- **`input needed`** — o Claude fez uma pergunta de verdade. Exige pensar.
- **`sandbox request` / `worker request` / `dialog open`** — tratar como
  `input needed`.

**Uso auto mode em tudo**, então `permission prompt` deve ser raro e
`input needed` é o caso dominante. Por isso o tipo é um **rótulo na linha**, não
um agrupamento separado — não faz sentido reservar um bloco da UI para uma
categoria que fica vazia.

Uma exceção que vale vigiar: `auto` só é restaurado ao retomar uma sessão
enquanto a conta continua atendendo aos requisitos do modo. Uma sessão retomada
pode sair de auto mode silenciosamente. O `--json` não expõe o modo de permissão,
então não dá para mostrá-lo direto — mas **uma sessão que de repente começa a
gerar `permission prompt` é o sintoma disso**, e vale destacar no painel quando
acontecer.

### 7.3 Notificação ao entrar em `blocked` (revisão da §7)

**O que dispara:** a transição de uma sessão para `blocked`. Não o estado
`blocked` — a transição. Uma sessão parada há duas horas já avisou uma vez e não
avisa de novo; a mesma sessão bloqueada outra vez amanhã é outro episódio e
avisa.

**Como a idempotência é paga.** Cada linha de `transitions` tem um id, e a
tabela `notifications` guarda um id por alerta assumido. O claim é um
`INSERT OR IGNORE` **dentro da transação do collect**, antes de qualquer envio:
quem ganha o insert envia. Isso resolve os três casos de uma vez — o mesmo
`blocked` relido a cada 60s, dois renderizadores coletando no mesmo segundo, e
um `collect --force` manual.

Ordem deliberada: o claim é feito **antes** do envio, e o envio acontece **fora**
da transação. Um claim que não vira envio perde um alerta; um envio sem claim
gera duplicata. Duplicata é a falha pior — notificação que mente vira ruído, e
ruído desliga o recurso.

**Push (hook `Notification`, 28/08/2026 — DECISIONS #37).** O alerta acima
depende do ciclo: até 60s de espera, e nada com o painel fechado. O hook
`Notification` fecha essa distância — mas **não decide nada**. Ele roda
`tarmac poke`, que é um `collect` só dos targets locais; o claim, o mute e os
filtros de `service`/`mine` continuam sendo quem decide, então o push produz
exatamente os alertas que o ciclo produziria, mais cedo.

- Matcher: `permission_prompt`, `agent_needs_input`, `elicitation_dialog`,
  `elicitation_url_dialog`. `idle_prompt` fica de fora: dispara 60s depois de
  todo turno de toda sessão, e ocioso não é bloqueado.
- Só targets locais (`local_only`): 15s de timeout de ssh não cabe num caminho
  que precisa alertar em segundos. Target remoto segue no ciclo.
- O hook roda destacado e sai em 0; poka duas vezes com 3s de intervalo, porque
  `agent_needs_input` pode chegar antes de a listagem mudar.

**Quem nunca notifica:**

- `class: service` — automação que eu não conduzo (§3.3), nunca espera por mim.
- targets com `mine: false` — a máquina de outra pessoa.
- **cold start**: um target visto pela primeira vez (banco novo, target recém
  adicionado) pode ter várias sessões já bloqueadas. Isso é backlog, não
  novidade — notificar seria receber uma instalação nova com uma rajada.

**Silenciar, dois níveis:**

| Nível | Onde | Para quê |
|---|---|---|
| `settings.notify: false` | `targets.yaml` | desligado de vez, não há o que alternar |
| mute no banco | `tarmac notify off` · `N` no TUI · item da menu bar | o toggle do dia a dia |
| mute com prazo | `tarmac notify mute 1h` · submenu | **reunião** — volta sozinho |

O prazo é o que interessa para reunião: não dá para esquecer de religar. E o
badge mostra `🔕` enquanto está mudo — **um painel silenciado nunca pode ser
indistinguível de um painel quieto.**

**Limite honesto:** quem coleta é o painel. Com o painel fechado não há ciclo,
logo não há alerta — o que é coerente com a §7 (o painel é a ferramenta; a
notificação é um reforço dele, não um substituto). Foco/Não Perturbe do macOS
silencia o alerta sem que a gente precise saber disso.

---

## 8. Interface — SwiftBar

### 8.0 Dois renderizadores sobre o mesmo núcleo

**Revisão de uma decisão anterior.** A spec dizia "menu bar e não TUI", com o
argumento de que um TUI é mais uma janela que preciso lembrar de abrir. Esse
argumento **não se aplica ao meu caso real**: eu tenho um segundo monitor
dedicado e deixo as coisas abertas nele o tempo todo. A falha que eu temia
(esquecer de abrir) não existe aqui.

Então o núcleo expõe um comando de render e os dois formatos são camadas finas:

```bash
tarmac render --format swiftbar   # plugin da menu bar
tarmac render --format tui        # janela dedicada, loop de 60s
```

Ambos leem o mesmo SQLite e chamam `collect_if_stale()` — coleta se o último
ciclo tem mais de 60s, senão usa o cache. Sem daemon, sem coordenação, e nada
quebra se os dois rodarem ao mesmo tempo.

| Renderizador | Quando |
|---|---|
| TUI em janela | **primário** — segundo monitor, sempre aberto, mostra tudo sem clique |
| SwiftBar | fallback para modo laptop, sem monitor externo |

O TUI, tendo espaço de sobra, mostra o que o menu esconde: tempo de espera,
`next_step`, progresso do checklist e `cwd` de todas as linhas simultaneamente.

### 8.0.1 O que isto substitui

Hoje eu empurro para o segundo monitor os terminais que "botei pra rodar", para
olhar depois. Esse monitor é uma fila improvisada, e cada terminal ali é um
processo que eu tenho medo de fechar.

Com `claude --bg` (seção 11) **não há o que estacionar**: a sessão vive no
supervisor, não no terminal. Os terminais podem ser fechados. O segundo monitor
passa a ter uma coisa só — este painel — que é a mesma fila, mas em uma linha por
sessão em vez de uma janela por sessão.

### 8.1 Badge

Título da barra. A regra: **mostrar a maior dor, não o maior número.**

- `⏸ 2 · 45m` — 2 bloqueadas, a mais antiga esperando há 45 min. Cor pela tabela
  de escalada da seção 7.1. Este é o estado que mais importa.
- `⏱ 1` somado quando há vencidos: `⏸ 2 · 45m  ⏱ 1`
- `▶ 5` quando não há bloqueadas nem vencidos mas há 5 `working`
- `✓` quando está tudo quieto
- `⚠` quando algum target está com `last_error`

O tempo no badge é o gatilho visual. Ele cresce sozinho no canto da tela até eu
olhar — que é exatamente a função que a notificação teria.

### 8.2 Menu

```
⏸ 2 · 45m   ⏱ 1
─────────────────────────────
PRA HOJE
  ⏱ dentsu-rfi-review       Mac    venceu há 2h        [2/5]
─────────────────────────────
PRECISA DE VOCÊ
  ⏸ qps-cache-refactor      EC2    qual índice usar?   45m ▲
  ⏸ acervo-lambda-iam       Mac    ⚠ permission         3m
─────────────────────────────
TRABALHANDO
  ▶ nightly-backfill        EC2    rodando testes       4h
  ▶ captura-ffmpeg-probe    Mac    editando arquivos   22m
─────────────────────────────
AGENDADO
  ⏱ liveramp-rampid-merge   Mac    seg, 11/08 09:00
  ⏱ pubmatic-poss-check     EC2    em 5h               [0/3]
─────────────────────────────
SERVIÇOS
  ⚙ slack-agents  EC2       3 ativas · 1 travada 2h  ⚠
─────────────────────────────
CONCLUÍDO (4)                                          ▸
─────────────────────────────
Targets: Meus ▾ | Todos
Atualizado há 12s · Atualizar agora
```

Cada linha, ao clicar, abre a sessão. Submenu por linha:
`Abrir` · `Ver logs` · `Lembrar em…` · `Adiar até…` · `Checklist…` ·
`Definir próximo passo…` · `Fixar` · `Parar sessão` · `Copiar comando de resume`.

`Lembrar em…` e `Adiar até…` gravam o mesmo `due_at` e diferem apenas no
`hide_until_due`. Cada um oferece atalhos (`2h` · `amanhã` · `segunda`) mais um
campo livre para o resto. Uma linha vencida ganha ainda `Resolver` e
`Reagendar…`.

Ordenação: vencidos primeiro (o mais antigo no topo), depois `blocked` com
**maior espera no topo**, depois `pinned`, `working`, e o resto. O marcador `▲`
sinaliza espera acima de 30 min; o `⚠` marca `permission prompt`, que sob auto
mode é anômalo e merece olhar.
Agrupamento secundário por target quando há mais de um target ativo.
Sessões com `due_at` futuro vão para "Agendado"; as com `hide_until_due = 1`
aparecem só lá, colapsado.

O filtro `Meus | Todos` respeita `mine` no `targets.yaml`.

---

## 9. Ações

### 9.0 Ciclo de vida da aba: abrir **ou** focar

Anexar não é dono da sessão. Detach nunca para uma sessão de background: `←`,
`Ctrl+Z`, `/exit` e duplo `Ctrl+C` todos a deixam rodando. Logo, fechar a aba
depois de dar o input é **opcional** — a sessão continua no supervisor.

Mas o painel não pode ignorar a aba, por dois motivos: abrir uma segunda aba na
mesma sessão é conflito (dois processos não podem escrever no mesmo transcript),
e abas órfãs acumulando recriam exatamente a fila de janelas que a ferramenta
existe para eliminar.

Por isso a ação de clique é **abrir ou focar**, nunca só abrir:

```
clique na linha
  ├─ existe handle registrado E a aba ainda existe? → foca a aba
  └─ senão → cria aba nova, registra o handle
```

```sql
CREATE TABLE terminal_handles (
  target_id  TEXT NOT NULL,
  session_id TEXT NOT NULL,
  handle     TEXT,      -- UUID da session do iTerm2
  opened_at  INTEGER,
  PRIMARY KEY (target_id, session_id)
);
```

O handle vem do próprio AppleScript na criação (`id of current session`). Para
focar:

```applescript
tell application "iTerm2"
  repeat with w in windows
    repeat with t in tabs of w
      repeat with sess in sessions of t
        if id of sess is "<handle>" then
          select w
          select t
          select sess
          activate
          return "found"
        end if
      end repeat
    end repeat
  end repeat
end tell
return "missing"
```

`missing` → o handle é lixo, apagar e abrir aba nova.

### 9.0.1 Coleta de abas ociosas

Ação `Fechar abas resolvidas`: fecha toda aba cuja sessão não está mais
`blocked` e cujo handle não é tocado há mais de N minutos (default 30). É a
faxina do segundo monitor, agora em um clique em vez de uma decisão por janela.

Não fechar automaticamente sem pedir. Uma aba pode ter output que eu ainda
quero ler.

### 9.0.2 Experimental — responder sem abrir terminal

`claude -p --resume <session-id> "<resposta>"` envia um prompt de follow-up a uma
sessão existente e devolve JSON estruturado. Se isso funcionar numa sessão de
background bloqueada em `input needed`, é o maior ganho ergonômico possível
aqui: responder direto da linha do painel, sem terminal nenhum.

**Provavelmente não funciona**, porque o processo do supervisor já mantém aquele
transcript aberto e dois processos não podem escrever nele. **Testar na tarefa
2.1** antes de assumir qualquer coisa. Se falhar, o caminho é a aba mesmo.

### 9.1 Abrir sessão

**Target local:**
```applescript
tell application "iTerm2"
    create window with default profile
    tell current session of current window
        write text "CLAUDE_CONFIG_DIR=<config_dir> claude attach <short_id>"
    end tell
end tell
```

**Target SSH:**
```bash
ssh -t <ssh_user>@<ssh_host> \
  'CLAUDE_CONFIG_DIR=<config_dir> <claude_bin> attach <short_id>'
```

O `-t` (força TTY) é **obrigatório**. Sem ele o attach não renderiza.

**Fallback** — sessão sem `short_id` (interactive, ou já removida do agent view),
usar o UUID:
```bash
claude --resume <uuid>
```
Isso funciona de qualquer diretório: desde a v2.1.223 o Claude Code procura o ID
no projeto atual e seus worktrees primeiro, depois em todos os outros projetos da
máquina. Ou seja, **não precisamos rastrear a pasta para conseguir retomar** —
o `cwd` no painel é informação para mim, não requisito técnico.

Ao anexar numa sessão de background, ela renderiza sempre em fullscreen (não há
scrollback de terminal para preencher). Navegação: `PgUp`/`PgDn`, roda do mouse,
`Ctrl+O` para modo transcript. Isso é comportamento nativo, não um bug nosso —
e é a razão de o scrollback bugado do tmux deixar de importar.

### 9.2 Outras ações

| Ação | Comando |
|---|---|
| Logs | `<claude_bin> logs <short_id>` |
| Parar | `<claude_bin> stop <short_id>` |
| Remover da lista | `<claude_bin> rm <short_id>` |
| Reiniciar o processo | `<claude_bin> respawn <short_id>` (tecla `r`, com confirmação) |
| Supervisor vivo? | `<claude_bin> daemon status` (`tarmac daemon`) |
| Copiar resume | copia `claude --resume <uuid>` para o clipboard |

`rm` pede confirmação dupla na UI: remover a sessão pode remover o worktree que
o Claude criou para ela, incluindo alterações não commitadas.

---

## 10. `next_step` automático

O campo mais valioso do painel — "o que falta aqui?" — não deve depender de eu
lembrar de preencher.

Hook `Stop`, instalado em cada `CLAUDE_CONFIG_DIR` local, recebe no stdin o
campo `last_assistant_message` — o texto da resposta que acabou de terminar — e
grava esse texto, condensado, em `~/.tarmac/next-steps.jsonl`. Sem chamada de
API, sem processo filho.

**Revisão de 28/08/2026** (DECISIONS #36). A versão anterior perguntava a um
modelo, via `claude -p --resume <id> --fork-session`. Aquilo continuava a
sessão, reacendia o próprio hook e precisava de quatro travas para não virar
laço de realimentação; queimou um limite de uso uma vez. O evento `Stop` entrega
o texto de graça, então o hook não spawna nada e não há laço a conter.

O que a nota é, exatamente: a frase da própria sessão — markdown e blocos de
código removidos, espaços colapsados, cortada em 200 caracteres numa fronteira
de palavra. **Não** é um resumo do que ficou pendente. A troca é deliberada:
uma citação truncada erra menos que um resumo alucinado, e esta coluna é lida
como fato.

Restrições:
- **Nunca sobrescrever um `next_step` que eu escrevi à mão** (origem `auto` vs
  `manual`).
- `Stop` dispara ao fim de **todo** turno, então a nota é reescrita conforme a
  sessão anda; o coletor a apaga quando a sessão volta a `working`.
- As listas `TARMAC_NEXTSTEP_ONLY` / `TARMAC_NEXTSTEP_EXCLUDE` (prefixos de
  cwd) continuam existindo — não mais como controle de custo, e sim como ruído
  e privacidade: o texto vai parar no banco do painel. Sem `cwd` no payload e
  com deny-list configurada, nega.
- Uma linha da fila carrega o `CLAUDE_CONFIG_DIR` que a escreveu: duas contas
  na mesma máquina compartilham `~/.tarmac/next-steps.jsonl` e o coletor precisa
  saber a quem entregar cada entrada.
- Custo real: zero.

---

### 10.1 Prompt automático no início da sessão — **não**

Existe o hook `SessionStart`, que injeta `additionalContext` na conversa e roda
de novo no resume. Tecnicamente daria para usá-lo, ou para pedir às sessões que
escrevam arquivos de status que o painel leria.

**Decisão: não fazer nenhum dos dois.** Dois motivos:

1. **Viola a seção 2.** O `claude agents --json` é autoritativo e não exige
   cooperação de ninguém. Um canal paralelo de arquivos cria uma segunda fonte
   de verdade que vai divergir — sessão que esquece de escrever, arquivo velho,
   sessão morta com arquivo vivo. O painel passaria a debugar a si mesmo.
2. **A documentação desaconselha para este uso.** Para instruções que não mudam,
   o próprio guia de hooks recomenda CLAUDE.md, que carrega sem rodar script e é
   o lugar padrão de convenções estáticas. `additionalContext` é para estado
   dinâmico do ambiente (branch atual, CI, feature flags), não para convenção.

Se em algum momento eu quiser uma instrução válida para toda sessão, ela vai no
CLAUDE.md. Sem hook, sem arquivo de status, sem canal lateral.

---

## 11. Operação no EC2 — convenção

Rotinas longas no EC2 usam **`claude --bg`, não tmux, não `remote-control`**:

```bash
claude --bg --name nightly-backfill "roda o backfill e reporta"
```

Razão: sessões de background são hospedadas pelo processo supervisor, separado do
terminal, e continuam rodando sem terminal anexado. Já o Remote Control roda como
processo local — se o terminal fecha ou o processo `claude` morre, a sessão acaba;
a doc oficial recomenda `tmux`/`screen` justamente para contornar isso. `--bg`
elimina a necessidade.

Se em algum momento eu quiser o **server mode** do Remote Control na EC2 (para
despachar do celular direto naquela máquina), rodar como unit
`systemd --user` + `loginctl enable-linger`, não em tmux.

**Convenção obrigatória:** toda sessão longa recebe `--name`. Nome é o handle de
resume e é o que aparece no painel. Renomear pelo claude.ai ou pelo app mobile
propaga para a listagem do `claude agents` (v2.1.221+), então o vocabulário é um
só entre celular, terminal e painel.

### 11.1 O que precisa existir na EC2

**Nada é instalado.** O `tarmac` roda inteiramente no Mac e só executa comandos
`claude` por SSH. Não há agente, daemon, serviço nem código do projeto na EC2.
Isso é intencional: qualquer coisa instalada lá vira mais uma peça para manter
viva através de quedas de VPN.

A verificação é executada pelo agente no **protocolo da seção 2.1**, blocos B e
C. Não repetir os comandos aqui — a seção 2.1 é a fonte única, e o resultado vive
em `FINDINGS.md`.

O `ControlMaster` (seção 4.3) é configuração do **Mac**, não da EC2.

#### Hook de `next_step` (opcional)

Único arquivo que se toca na EC2, e só se eu quiser a seção 10: o `SessionEnd`
em `~/.claude/settings.json`. **Não** colocar no `.claude/settings.json` do
diretório dos agentes de Slack (seção 3.3).

#### O risco do ambiente herdado

O supervisor captura o ambiente do primeiro shell que o inicia. Se o poll do
`tarmac` for o primeiro a tocá-lo, sessões despachadas depois podem herdar um
`PATH` pobre. É o que o bloco C da seção 2.1 investiga; a mitigação, se
confirmado, é o bloco `env` no `.claude/settings.json` do projeto.

#### Consequência para o meu uso atual de `/rc`

Hoje eu rodo Remote Control na EC2 por SSH. Isso é frágil justamente por causa
da VPN: o Remote Control é um processo local e morre com o terminal, e uma
sessão sem rede por mais de ~10 minutos expira e o processo sai. VPN caindo à
noite mata a sessão.

`claude --bg` não tem esse problema — o supervisor é independente do terminal.
Migrar as rotinas longas para `--bg` conserta isso **antes** de o `tarmac`
existir, e é a mudança de maior retorno desta spec inteira.

---

## 12. Riscos conhecidos

| Risco | Mitigação |
|---|---|
| Schema do `--json` muda (research preview) | `raw_json` guardado; parser tolerante; testes com fixture da saída real |
| SSH cai ou EC2 inacessível | falha isolada por target, dados marcados stale, badge `⚠` |
| Sessão desaparece do `--json` | `gone = 1`, nunca delete; `session_meta` preservado |
| Badge virando ruído de fundo | só `blocked`, vencidos e erros reais o alteram; `done` nunca |
| Sessões de chatops enterrando as minhas | classe `service`: fora da lista, do badge e da métrica |
| Banco crescendo com sessões de Slack | retenção de 24h para `service` concluídas |
| Versões diferentes do Claude Code entre Mac e EC2 | registrar `claude --version` por target; campos ausentes não quebram |
| Permissões macOS para processos background lendo Desktop/Documents | fora do escopo do painel, mas documentar se aparecer |
| Lembrete vence com o Mac dormindo | sem notificação isso é irrelevante: ao acordar, o badge já mostra o vencido |
| Badge da menu bar escondido (fullscreen) | mitigado: o TUI no segundo monitor é o primário, o badge é fallback |
| Sessão sai de auto mode ao ser retomada | detectar pelo aparecimento de `permission prompt` e destacar a linha |
| Abas do iTerm acumulando | registro de handles + ação `Fechar abas resolvidas` |
| VPN caída acendendo `⚠` toda noite | `expect_intermittent` + classificação por stderr: offline é cinza, não alarme |
| Dado velho da EC2 lido como atual | idade da leitura sempre visível; esperas pós-reconexão exibidas como `≥` |
| Duas abas anexadas à mesma sessão | prevenido pelo "abrir ou focar"; nunca criar aba se o handle está vivo |
| Data natural interpretada errada | sempre exibir a data absoluta resolvida ao confirmar |
| Vencidos acumulando e virando ruído | não somem sozinhos, mas exigem `Resolver`/`Reagendar` explícito — o atrito é intencional |

---

## 13. Critérios de aceite

**Coleta e multi-target**

1. `tarmac collect` popula o banco a partir de todos os targets habilitados, local e
   SSH, sem erro.
2. Nenhum arquivo em `~/.claude/projects/` é lido em nenhum caminho de código.
3. Adicionar um segundo `owner` no `targets.yaml` funciona sem tocar em código.
4. Credencial SSH inválida acende `⚠` naquele target, sem afetar os demais.
5. VPN caída por 6h deixa o target cinza com "offline há 6h", **sem** `⚠`, e o
   badge não muda de cor por causa disso.
6. Sessão que bloqueou durante o offline exibe `≥ 6h`, nunca um valor preciso.

**Classes de sessão**

6b. Uma sessão iniciada em `~/slack-agents/**` não aparece na lista principal,
    não altera o badge e não entra em `wasted_total`.
6c. Três sessões de serviço ativas aparecem como uma única linha `3 ativas`.
6d. Uma sessão de serviço bloqueada há mais de 30 min acrescenta `1 travada` com
    `⚠` naquela linha — e nada mais.
6e. Sessões `service` concluídas somem do banco após 24h.

**Tempo de espera**

7. Uma sessão que entra em `blocked` aparece no painel em até 60s, com o
   contador de espera correndo.
8. Uma sessão bloqueada há 45 min aparece no badge como `⏸ 1 · 45m` em cor de
   alarme, sem eu abrir o menu.
9. `permission prompt` aparece marcado com `⚠` na linha, por ser anômalo sob
   auto mode.
10. `tarmac stats` reporta o tempo agregado em `blocked` por dia.

**Abas**

11. Clicar na linha abre uma aba do iTerm já dentro daquela sessão — local ou
    remota — sem eu digitar nada.
12. Clicar duas vezes na mesma sessão traz a aba existente para frente, sem
    criar uma segunda.
13. Fechar a aba manualmente e clicar de novo abre uma aba nova, sem erro.

**Agendamento, checklist e nomes**

14. `Lembrar em…` aceitando `5h`, `na segunda` e `3 dias` grava o `due_at`
    correto e mostra a data absoluta resolvida antes de confirmar.
15. Um lembrete vencido sobe para **PRA HOJE** e permanece lá até eu resolver ou
    reagendar.
16. Adiar uma sessão para amanhã a remove da lista principal e a traz de volta
    no dia seguinte com o `next_step` intacto.
17. Um checklist de 5 itens com 2 marcados exibe `[2/5]` na linha e sobrevive ao
    desaparecimento da sessão do `--json`.
18. Sessão com nome no padrão `<dir>-xx` aparece marcada com `✎`.

---

## 14. Stack


- Python 3.12+, stdlib sempre que possível
- `sqlite3` (stdlib), `PyYAML`, `subprocess` para ssh/AppleScript
- Núcleo com `tarmac render --format {swiftbar,tui}`; renderizadores são camadas
  finas sobre o mesmo SQLite
- TUI: `textual` ou `rich` — janela dedicada no segundo monitor, loop de 60s
- SwiftBar: plugin executável que imprime o menu (fallback modo laptop)
- Sem daemon próprio: ambos chamam `collect_if_stale()` (coleta se o cache tem
  mais de 60s). Rodar os dois ao mesmo tempo é seguro
- Testes com fixtures da saída real do `--json`, capturadas na tarefa 2.1

---

## 15. README e distribuição pública

O repositório é público. Isso **não** é uma tarefa de documentação — é uma
restrição de design que volta para o código.

### 15.1 A tensão a resolver

Esta spec é escrita em primeira pessoa e assume o meu mundo: iTerm2, uma EC2
atrás de VPN, auto mode, português, dois monitores. Nada disso é verdade para
quem vai clonar o repo.

O README não pode ser esta spec traduzida. Ele fala com alguém que tem o mesmo
problema e nenhum do meu contexto.

### 15.2 Checklist de generalização

Cada item abaixo é hoje uma suposição minha embutida. Todos viram configuração
antes do primeiro push público:

| Hoje | Precisa virar |
|---|---|
| iTerm2 no AppleScript | adaptador de terminal: iTerm2, Terminal.app, Ghostty, WezTerm, Kitty, e fallback `tmux` |
| Parser de data em português | `locale` em config; PT e EN no mínimo |
| Hora âncora 09:00 | `default_hour` em config |
| macOS assumido | o TUI é multiplataforma; só SwiftBar e AppleScript não são. Linux deve rodar TUI + `tmux` sem alterações |
| `targets.yaml` com meus hosts | `targets.example.yaml` versionado; o real no `.gitignore` |
| Auto mode assumido | detectar, não exigir; o rótulo `⚠` em `permission prompt` vira configurável |
| `session_classes` com meus caminhos | documentar como categoria geral: qualquer sessão iniciada por automação (chatops, CI, cron) que o usuário não conduz |
| Strings de UI em português | i18n simples, EN como default do repo |

**Regra:** se um adaptador de terminal não existir, o `tarmac` degrada para
imprimir o comando e copiá-lo para o clipboard. Nunca falhar em silêncio por
causa de terminal não suportado.

### 15.3 Estrutura do README

1. **Uma frase e uma imagem.** O que é, com um GIF do TUI mostrando uma sessão
   bloqueada há 45 min. A imagem faz o argumento sozinha.
2. **O problema, não a feature.** Abrir com a dor: agentes em paralelo, um
   parou esperando você e ninguém viu, o terminal está enterrado atrás de outros
   seis. Quem reconhece a cena continua lendo.
3. **O que já é nativo.** Seção honesta e cedo: `claude agents` resolve boa parte
   disso sozinho, e muita gente que chegar aqui não sabe que existe. Dizer o que
   o `tarmac` acrescenta — múltiplas máquinas, tempo de espera, agendamento,
   abrir/focar aba — e o que ele **não** acrescenta. Mandar as pessoas para a
   ferramenta nativa quando ela basta ganha mais confiança do que reter usuário.
4. **Quickstart real**, com `targets.example.yaml` completo e comentado.
5. **Princípios de design.** Curto, e o que mais vale compartilhar: *nunca
   parsear os JSONL*. Muita ferramenta de terceiros faz isso e quebra a cada
   release. Explicar por quê é o conteúdo mais útil do repo inteiro.
6. **Limitações, sem maquiagem.** Sem notificações por decisão. Alias é local e
   não propaga. Responder inline pode não funcionar. Requer Claude Code recente
   (fixar a versão mínima testada).
7. **Não-objetivos.** Não orquestra agentes, não renderiza transcripts, não
   gerencia worktrees, não agenda execução — para isso existem as tarefas
   agendadas nativas.

### 15.4 Higiene do repositório

- `targets.yaml` **no `.gitignore`** desde o primeiro commit. Ele carrega
  hostnames, usuários e caminhos internos. Vazar isso num repo público é o erro
  mais provável deste projeto.
- Nenhuma saída real de `claude agents --json` versionada sem anonimizar:
  `cwd` expõe estrutura de projeto e nomes de cliente. As fixtures de teste
  (seção 2.1) vão sanitizadas.
- Licença MIT. `CONTRIBUTING.md` de dez linhas: como rodar os testes, e o pedido
  de que novos adaptadores de terminal venham com fixture.
- README em inglês. Se eu quiser, `README.pt-BR.md` ao lado.
