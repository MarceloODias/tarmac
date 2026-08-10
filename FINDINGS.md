# FINDINGS — Tarefa 0 (protocolo de reconhecimento)

> Executado em 2026-08-08, no Mac local (`darwin 25.5.0`), Claude Code 2.1.226.
> Convenção da SPEC §2.1: `OK | FALHOU | BLOQUEADO | N/A`. Saídas sanitizadas.

## Resumo executivo

| Bloco | Status | Uma linha |
|---|---|---|
| A — ambiente local | **OK** | v2.1.226; schema capturado em `fixtures/agents-local.json`; divergências relevantes no A4 |
| B — EC2 e SSH | **OK** | `ec2-user@172.16.103.235` via alias `ec2-runner`; mesma versão 2.1.226; coleta não interativa funciona; ControlMaster dá ganho de 3× |
| C — supervisor | **OK** | **`agents --json` NÃO sobe o supervisor** (testado com daemon parado na EC2) — o poll é seguro; C3/C4 viram N/A |
| D — agentes de Slack | **OK** | gatilho real observado: `kind: interactive` (cwd é o único discriminador), cwd sob `~/ai-agent-skills/**`, sessão some do JSON ao concluir |
| E — resposta inline | **FALHOU** (resultado útil) | CLI recusa com erro limpo e sugere `attach` ou `--fork-session` |
| F — iTerm2 | **OK** | F1/F2/F3 confirmados após aprovação de Automação: handle é UUID, "abrir ou focar" viável, aba fechada → `missing` limpo |
| G — heurística de nomes | **OK** | 3/7 interativas casam o padrão; zero falsos positivos na amostra |

---

## A. Ambiente local (Mac)

### A1 — versão
Comando:  `claude --version`
Status:   **OK**
Saída:    `2.1.226 (Claude Code)`
Conclusão: todos os recursos exigidos pela spec estão disponíveis:

| Recurso | Requer | Disponível? |
|---|---|---|
| agent view | ≥ 2.1.139 | sim |
| `/resume` em agent view | ≥ 2.1.212 | sim |
| rename propagando ao `agents` | ≥ 2.1.221 | sim |
| `--resume <id>` de qualquer diretório | ≥ 2.1.223 | sim |

### A2 — schema do `agents --json`
Comando:  `claude agents --json --all`
Status:   **OK**
Saída:    9 sessões (7 interativas, 2 background no momento da captura, mais as de teste). Sanitizada em **`fixtures/agents-local.json`** (gerada por `fixtures/sanitize.py`, que documenta o mapeamento).
Conclusão: schema real capturado. JSON é um array plano de objetos; ver A4 para as diferenças contra a §2.2.

### A3 — semântica do `--all`
Comando:  `claude agents --json` vs `claude agents --json --all`
Status:   **OK** (com nuance importante)
Saída:    `claude agents --help`: `--all  With --json: also include completed background sessions`.
Teste empírico: despachei uma sessão trivial (`tarmac-a3-done-test`) e comparei as duas saídas após ela chegar a `state: done`.
Conclusão:
- A semântica documentada confirma: `--all` = incluir background **concluídas**.
- **Nuance:** logo após concluir, a sessão `done` apareceu **também sem `--all`** — enquanto o worker (pid) ainda estava vivo no supervisor. `pid`/`status` presentes mesmo com `state: done`. Ou seja, a fronteira do `--all` é "worker já assentou", não "state == done". O coletor deve usar sempre `--all` (como a spec já manda) e não assumir que a ausência de `--all` filtra `done` de forma imediata.
- **Confirmação empírica no fim do ciclo:** o worker da sessão `done` demorou **mais de 8 minutos** para assentar sozinho; forcei com `claude stop <id>`. Depois de colhido o worker, a sessão sumiu de `claude agents --json` (0 ocorrências) e passou a aparecer **somente com `--all`**, sem `pid`/`status`. Semântica confirmada de ponta a ponta.
- Bônus: `claude stop` numa sessão já concluída mantém `state: "done"` (não vira `stopped`).

### A4 — schema real × tabela da §2.2
Status:   **OK**
Conclusão — campos previstos e confirmados: `cwd`, `kind`, `startedAt` (epoch ms), `id` (só background), `state` (só background), `pid`, `status`, `waitingFor`, `sessionId`, `name`. Nenhum campo **não previsto** apareceu na amostra. Divergências e fatos novos:

1. **`waitingFor` não apareceu em background bloqueada por pergunta.** A sessão de teste do bloco E ficou `state: blocked` com `status: idle` e **sem `waitingFor`**. O único `waitingFor` observado foi em sessão *interativa* (`status: waiting`, `waitingFor: "permission prompt"`). Impacto na spec: a §7.2 assume que `waitingFor` distingue o tipo de bloqueio; para sessões background pode não haver o campo — o rótulo do tipo de bloqueio precisa tolerar ausência (exibir só "blocked").
2. **`pid`/`status` indicam worker vivo, não sessão ativa.** Uma background `blocked` antiga (dias) veio **sem** `pid` e **sem** `status`; uma `done` recém-concluída veio **com** ambos. Consistente com a §2.2 ("processo vivo"), mas o corolário importa: `status` pode faltar em sessões `blocked` — o estado canônico para o painel é `state`, com `status` como complemento opcional.
3. **Invariante útil: `id` (short) = 8 primeiros hex do `sessionId`.** Verdadeiro em todas as background observadas (4/4). Útil como fallback, mas tratar como não-contratual (research preview).
4. **`name` veio presente em 100% da amostra** (defaults incluídos). A spec diz "quando existe" — manter o parser tolerante mesmo assim.
5. `state` observados: `working`, `blocked`, `done`. `failed`/`stopped` não observados (a sessão de teste parada foi removida antes da captura); manter os cinco valores previstos no schema do banco.
6. `status` observados: `idle`, `busy`, `waiting`.

---

## B. EC2 e SSH

### B1 — SSH sem senha
Comando:  `ssh -o BatchMode=yes ec2-runner true`
Status:   **OK** (3ª rodada; histórico: 1ª — alias inexistente; 2ª — escrita no `~/.ssh/config` negada pela permissão da sessão, você adicionou o bloco manualmente)
Saída:    exit 0, sem prompt.
Conclusão: acesso descoberto via `echo_access` → `ec2-user@172.16.103.235`, chave `~/dev.pem`. O bloco `Host ec2-runner` (com o `ControlMaster` da §4.3 já embutido) está no `~/.ssh/config`. Nota para a spec: o usuário real é **`ec2-user`**, não `marcelo` como no exemplo do `targets.yaml` §3.1.

### B2 — caminho do binário
Comando:  `ssh ec2-runner 'which claude'`
Status:   **OK**
Saída:    `/home/ec2-user/.local/bin/claude` — e, surpresa boa, resolvível **até em shell não interativo** (o PATH da EC2 já o inclui).
Conclusão: `claude_bin: /home/ec2-user/.local/bin/claude` no `targets.yaml`. Manter o caminho absoluto mesmo assim (a spec está certa: não depender do PATH remoto).

### B3 — versão na EC2
Comando:  `ssh ec2-runner '<bin> --version'`
Status:   **OK**
Saída:    `2.1.226 (Claude Code)`
Conclusão: **idêntica à do Mac.** Zero divergência hoje; o registro por target continua valendo para o futuro.

### B4 — coleta não interativa (o teste que mais importa)
Comando:  `ssh -o BatchMode=yes ec2-runner '<bin> agents --json --all'`
Status:   **OK**
Saída:    exit 0; JSON válido com 4 sessões (3 background: `done`, `failed`, `blocked`; 1 interativa `idle`). Sanitizada em **`fixtures/agents-ec2.json`**.
Conclusão: o comando do coletor funciona exatamente como o tarmac vai rodá-lo. Dois bônus: (1) capturado o shape de **`state: failed`** que faltava na amostra local; (2) a listagem funcionou **com o supervisor parado** — ela vem dos arquivos de estado, não exige daemon vivo (ver C2).

### B5 — ganho do ControlMaster
Comando:  3× `time ssh ... agents --json --all`, sem e com multiplexação
Status:   **OK**
Saída:    sem mux: 2.35 / 2.34 / 2.33 s. Com mux: 0.74 / 0.75 / 0.75 s.
Conclusão: ganho real de **~3× (economiza ~1,6s por poll)**. Relevante tanto para o poll de 60s quanto — principalmente — para o refresh ao abrir o menu (§4.1). Manter o `ControlMaster` como a §4.3 exige.

### B6 — detectar socket obsoleto
Comando:  `ssh -O check ec2-runner`
Status:   **OK**
Saída:    `Master running (pid=29455)`, exit 0.
Conclusão: funciona como sonda. Para a §4.4: exit ≠ 0 (ou mensagem de erro) indica socket morto → remover o `ControlPath` e reconectar.

---

## C. Supervisor e ambiente herdado

### C1 — estado do supervisor
Comando:  `claude daemon status` (executado **no Mac local**; na EC2 está bloqueado por B1)
Status:   **OK** (local, informativo)
Saída (sanitizada):
```
pid:     <pid>
version: 2.1.226
uptime:  111s
origin:  transient — started on-demand by `claude --bg` (pid <pid>) in <projeto>
bg workers: 2 running
holding this daemon open:
  2 bg workers running (daemon waits for them to settle)
  `claude agents` (pid <pid>) in <projeto>
```
Conclusão: dois fatos direto da fonte:
- O supervisor é **transiente e sobe sob demanda** ("started on-demand by `claude --bg`").
- **O próprio `claude agents` aparece como processo que "segura o daemon aberto"** — ou seja, o comando de coleta do tarmac no mínimo se conecta ao supervisor e o mantém vivo.

### C1b — estado inicial na EC2 (3ª rodada)
Comando:  `ssh ec2-runner '<bin> daemon status'`
Status:   **OK**
Saída:    `not running` — control.sock inexistente, 0 workers, roster atualizado há ~4,2 dias.
Conclusão: cenário perfeito para o C2 de verdade: supervisor comprovadamente parado **antes** de qualquer `agents --json` meu.

### C2 — `agents --json` sobe supervisor?
Status:   **OK — resposta: NÃO**
Saída:    rodei o B4 (`agents --json --all`) com o supervisor parado; `daemon status` imediatamente depois: **`not running`**, mesmo socket inexistente, mesmo roster antigo.
Conclusão: **o poll do tarmac é seguro.** `agents --json` lê os arquivos de estado sem subir daemon. O risco mais sutil da spec (§2.1-C, §11) **não se materializa**: o poll nunca será "o primeiro shell que inicia o supervisor" — quem sobe o supervisor é `claude --bg` (confirmado no C1 local: "started on-demand by `claude --bg`"). Nota fina do C1 local: com o daemon **já de pé**, um `claude agents` interativo o segura aberto — irrelevante para o poll (`--json` sai na hora), mas explica o que se viu na 1ª rodada.

### C3–C4 — ambiente herdado pelo job
Status:   **N/A** (condicionais a C2 = sim, que não ocorreu)
Conclusão: sem risco via poll, não há o que mitigar por causa do tarmac. O ambiente herdado pelo supervisor continua sendo função de *onde* o `claude --bg` é disparado — comportamento nativo do Claude Code, fora do escopo do painel. O bloco `env` no `.claude/settings.json` fica como ferramenta opcional, não obrigatória.

---

## D. Classes de sessão (agentes de Slack)

*(4ª rodada: você disparou uma mensagem real no Slack e eu observei a sessão nascer e morrer no `agents --json` da EC2, com um monitor de 10s.)*

### D1 — `cwd` real
Status:   **OK**
Saída:    a sessão do gatilho nasceu com `cwd: /home/ec2-user/ai-agent-skills/ClaudeCode/Monitoring/BackendHealthMonitor`.
Conclusão: os agentes vivem sob **`~/ai-agent-skills/**`**, não `~/slack-agents/**` como a §3.3 supunha. Glob para o `targets.yaml`:
`match_cwd: "/home/ec2-user/ai-agent-skills/**"`.

### D2 — `kind`
Status:   **OK — e derruba uma alternativa**
Saída:    `kind: "interactive"` (com `pid`), nome default `backendhealthmonitor-51`.
Conclusão: sessões de Slack são **interactive**, não background — `kind` **não** distingue chatops de sessão minha, então a classificação por `cwd` da §3.3 não é só suficiente: é **necessária**. Dois achados laterais:
- O nome default veio do basename do cwd **em minúsculas** (`BackendHealthMonitor` → `backendhealthmonitor-51`): a regex do G1/§6.5 precisa comparar com `basename(cwd)` minusculizado (ou casar case-insensitive).
- A entrada veio **sem `status`** apesar de ter `pid` (capturada segundos após nascer) — mais um campo que o parser não pode exigir (soma-se ao A4).

### D3 — volume e retenção
Status:   **OK (parcial — 1 gatilho observado)**
Saída:    1 mensagem no Slack = 1 sessão, que viveu ~2 minutos e então **desapareceu por completo da listagem — inclusive com `--all`**. A listagem voltou à baseline de 4 sessões.
Conclusão: o `--all` retém só background concluídas; **interactive concluída some sozinha do JSON**. Isso muda a natureza do risco "banco vira log de chat" (§3.3): o JSON se auto-limpa — quem acumula é o **banco do tarmac** (cada sessão efêmera vira uma linha `gone = 1`). A retenção de 24h para `service` continua certa, mas o alvo dela é o SQLite, não a coleta. Volume diário real: ainda sem número (1 gatilho); medir depois de o coletor existir, olhando `first_seen_at` por dia.

---

## E. Resposta inline (experimento §9.0.2)

### E1 — `claude -p --resume` em sessão background bloqueada
Comando:  criada sessão descartável `tarmac-e1-descartavel` (`claude --bg` pedindo que fizesse uma pergunta e aguardasse). Ela chegou a `state: blocked`. Então:
`claude -p --resume <session-id> --output-format json "azul"`
Status:   **FALHOU** (hipótese da spec confirmada — e com resultado melhor que o esperado)
Saída:    exit 1, mensagem exata:
```
Error: Session <uuid> is currently running as a background agent (bg).
Use `claude agents` to find and attach to it, or add --fork-session to branch off a copy.
```
Conclusão: responder inline **não funciona** para sessões background — o caminho é a aba, como a spec previa. Três dados úteis:
1. O erro é **limpo, específico e detectável** — não é corrupção de transcript. O CLI guarda a sessão bg e recusa antes de tocar em qualquer coisa.
2. A sessão de teste **permaneceu intacta** (`blocked`) após a tentativa — sem efeito colateral.
3. O erro sugere `--fork-session` como alternativa oficial (ramifica uma cópia). Não serve para *responder* à sessão original, então não muda a decisão — mas fica registrado.

### E2 — mensagem de erro exata
Status:   **OK**
Conclusão: registrada acima. Caso claro de "não suportado por design", não de conflito acidental. Se o painel um dia quiser distinguir, basta casar o texto `is currently running as a background agent`.

*(Sessão de teste parada e removida ao final: `claude stop` + `claude rm`.)*

---

## F. iTerm2 e AppleScript

### F1 — criar aba e retornar handle
Status:   **OK** (3ª rodada, após você aprovar a Automação do macOS)
Saída:    `create window with default profile` + `id of current session` → `FEB44ED3-53E0-4C15-B81E-CB8AA7DCF694`
Conclusão: o handle da §9.0 é um **UUID em maiúsculas**, retornado direto na criação. Histórico do caminho até aqui (importa para o quickstart):
1. iTerm2 não estava instalado (1ª rodada) → instalado via `brew install --cask iterm2` (3.6.11).
2. `tell application "iTerm2"` **não compila** logo após a instalação (LaunchServices sem o registro) — usar sempre **`tell application id "com.googlecode.iterm2"`**, que é robusto desde o primeiro segundo.
3. Antes da aprovação de Automação (TCC): `AppleEvent timed out (-1712)` com o diálogo pendente na tela. Não é aprovável por CLI. **Cada host de automação (SwiftBar, processo do TUI, Terminal) precisará da própria aprovação TCC** — documentar como passo de setup no quickstart (§15.3).

### F2 — focar por id
Status:   **OK**
Saída:    o script da §9.0 (loop janelas→abas→sessões, `select`+`activate`) retornou `found` e trouxe a janela à frente.
Conclusão: "abrir ou focar" é viável exatamente como especificado.

### F3 — aba fechada
Status:   **OK**
Saída:    após `close t` na aba criada, o mesmo script de busca retornou `missing` — sem erro, sem exceção.
Conclusão: o caminho `missing` da §9.0 confirma: handle morto é detectável de forma limpa; basta apagar o registro e abrir aba nova.

---

## G. Heurística de nomes

### G1 — padrão `^<basename(cwd)>-[a-z0-9]{2}$`
Status:   **OK**
Saída:    das 7 sessões interativas da captura A2, **3 casam** o padrão (nunca nomeadas: ex. sanitizado `user-e9` em `/home/user`, `project-d-62`, `project-b-a4`) e 4 não casam (nomeadas de verdade: `project-a-report`, `warehouse-ingestion`, `camera`, mais a de plan mode `Fix data path...`). Zero falsos positivos e zero falsos negativos na amostra.
Conclusão: a heurística da §6.5 **valida** — funciona como detector de "nunca nomeada" neste conjunto. Ressalva de amostra pequena (n=7). Nota: o sufixo observado inclui dígitos e letras (`e9`, `62`, `a4`), consistente com `[a-z0-9]{2}`.

---

## Fixtures

- `fixtures/agents-local.json` — captura real do A2 (9 sessões), sanitizada: cwds genéricos, nomes de cliente removidos, UUIDs re-gerados, **forma preservada** (mesmos campos, tipos, cardinalidade, e a relação basename(cwd)↔nome default do G1).
- `fixtures/agents-ec2.json` — captura real do B4 (4 sessões: `done`, `failed`, `blocked`, interativa `idle`), sanitizada com os mesmos critérios.
- `fixtures/sanitize.py` — o mapeamento de sanitização local, versionado para auditoria.
- Arquivos `raw-*.json` (não sanitizados) **não** foram versionados.

## O que fica na sua mão (portão de saída)

Tudo executável foi executado — B, C e F fecharam na 3ª rodada. Restam decisões de spec, não verificações:

1. ~~**B (EC2)**~~ — resolvido: você adicionou o bloco `ec2-runner` ao `~/.ssh/config`; B1–B6 OK.
2. ~~**C2**~~ — resolvido na EC2 com o supervisor comprovadamente parado: `agents --json` **não** o sobe; C3/C4 N/A.
3. ~~**F (macOS)**~~ — resolvido: Automação aprovada, F1–F3 OK.
4. **§7.2:** decidir como rotular bloqueio de background sem `waitingFor` (observado: `blocked` + `status: idle`, sem o campo).
5. **§3.1 (`targets.yaml`):** usuário real da EC2 é `ec2-user` (não `marcelo`); `claude_bin: /home/ec2-user/.local/bin/claude`.
6. **§3.3 (D):** atualizar o exemplo de `session_classes` para `match_cwd: "/home/ec2-user/ai-agent-skills/**"` (confirmado com gatilho real; `~/slack-agents/**` não existe). E absorver: chatops é `interactive`, some do JSON ao concluir — a retenção de 24h mira o SQLite.
7. **§6.5/G1:** a regex de nome default precisa minusculizar o `basename(cwd)` antes de comparar (observado: `BackendHealthMonitor` → `backendhealthmonitor-51`).
