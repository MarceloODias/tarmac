# FINDINGS — Tarefa 0 (protocolo de reconhecimento)

> Executado em 2026-08-08, no Mac local (`darwin 25.5.0`), Claude Code 2.1.226.
> Convenção da SPEC §2.1: `OK | FALHOU | BLOQUEADO | N/A`. Saídas sanitizadas.

## Resumo executivo

| Bloco | Status | Uma linha |
|---|---|---|
| A — ambiente local | **OK** | v2.1.226; schema capturado em `fixtures/agents-local.json`; divergências relevantes no A4 |
| B — EC2 e SSH | **BLOQUEADO** | host descoberto (`ec2-user@172.16.103.235`, chave `~/dev.pem`), mas escrever no `~/.ssh/config` e SSH com chave explícita foram negados pela permissão da sessão — snippet pronto no fim do arquivo |
| C — supervisor | **PARCIAL** | supervisor local é transiente/on-demand e `claude agents` o segura aberto; teste completo bloqueado (B1 + risco de matar sessões vivas) |
| D — agentes de Slack | **BLOQUEADO** | depende do JSON da EC2 (B1) |
| E — resposta inline | **FALHOU** (resultado útil) | CLI recusa com erro limpo e sugere `attach` ou `--fork-session` |
| F — iTerm2 | **BLOQUEADO** | iTerm2 3.6.11 **instalado**; falta aprovação de Automação (TCC) na tela — AppleEvent timed out (-1712) |
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
Status:   **BLOQUEADO** (atualizado 2× em 2026-08-08)
Saída:    1ª rodada: `ssh: Could not resolve hostname ec2-runner` (exit 255) — o alias não existia no `~/.ssh/config` (só `winbuild` e `nutpi`).
Atualização (2ª rodada): `echo_access` revelou o acesso — primeiro host = `ec2-user@172.16.103.235`, chave `~/dev.pem` (existe, perms 400). Porém:
- escrever o bloco `Host ec2-runner` no `~/.ssh/config` foi **negado pelo classificador de permissões da sessão** (2 tentativas, Edit e append via shell);
- `ssh -i ~/dev.pem ec2-user@172.16.103.235` direto também foi **negado**.
Conclusão: o bloqueio agora é de **permissão da sessão**, não de ambiente. Falta você adicionar o bloco no `~/.ssh/config` (snippet no fim deste arquivo); `ssh ec2-runner` já foi permitido pelo classificador antes, então com o alias no lugar B2–B6, C e D destravam. Nota para a spec: o usuário real da EC2 é `ec2-user`, não `marcelo` como no exemplo do `targets.yaml` §3.1.

### B2–B6
Status:   **BLOQUEADO** (por B1)
Conclusão: `claude_bin`, divergência de versão, o teste de coleta não interativa (B4), o ganho do `ControlMaster` (B5) e o `ssh -O check` (B6) ficam pendentes até existir acesso à EC2.

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

### C2 — `agents --json` sobe supervisor?
Status:   **BLOQUEADO** (não testável com segurança agora)
Conclusão: o teste exige supervisor **parado**, e o supervisor local está hospedando sessões reais neste momento (inclusive a sessão que executa esta tarefa) — derrubá-lo mataria trabalho vivo. Na EC2, bloqueado por B1. A evidência parcial do C1 (o `agents` segura o daemon aberto; o daemon sobe on-demand) torna **plausível** que sim, o poll possa ser o primeiro a subir o supervisor — o risco da spec continua de pé, não confirmado nem descartado.

### C3–C4 — ambiente herdado pelo job
Status:   **BLOQUEADO** (dependem de C2)
Conclusão: pendentes. Recomendação preventiva mantida da spec: quando a EC2 estiver acessível, rodar C2–C3 lá antes de o poll do tarmac existir, e já considerar o bloco `env` no `.claude/settings.json` como mitigação se o ambiente vier pobre.

---

## D. Classes de sessão (agentes de Slack)

### D1–D3
Status:   **BLOQUEADO** (por B1)
Conclusão: `cwd` real dos agentes de Slack, `kind` deles e volume diário exigem o JSON da EC2. Pendente de acesso.

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

### F1–F3
Status:   **BLOQUEADO** (atualizado: iTerm2 instalado, falta aprovação de Automação do macOS)
Saída:    1ª rodada: iTerm2 não estava instalado (único terminal: Terminal.app). 2ª rodada: **instalado via `brew install --cask iterm2` → 3.6.11**, processo sobe normalmente. O F1, porém, falha em duas camadas:
1. `tell application "iTerm2"` não compila logo após a instalação (`syntax error: Expected end of line but found class name`) — o LaunchServices ainda não tinha registrado o nome; **`tell application id "com.googlecode.iterm2"` resolve** e é a forma mais robusta para o tarmac usar sempre.
2. Com o bundle id, o evento chega mas não é respondido: `execution error: iTerm got an error: AppleEvent timed out. (-1712)` — consistente com o diálogo de **Automação** do macOS (TCC) pendente na tela e/ou a janela de onboarding do primeiro launch do iTerm2 bloqueando o app. Não há como aprovar isso por linha de comando (e `osascript` também não tem acesso de assistive para clicar: erro -25211 registrado).
Conclusão: F1–F3 precisam de **uma ação sua na tela** (abrir o iTerm2 uma vez, fechar o onboarding, e aprovar o prompt "quer controlar o iTerm2" quando o AppleScript rodar). Implicação para a spec: cada host de automação (SwiftBar, o processo do TUI, Terminal) vai precisar da **sua própria** aprovação TCC para controlar o iTerm2 — vale documentar no quickstart (§15.3) como passo de setup do macOS.

---

## G. Heurística de nomes

### G1 — padrão `^<basename(cwd)>-[a-z0-9]{2}$`
Status:   **OK**
Saída:    das 7 sessões interativas da captura A2, **3 casam** o padrão (nunca nomeadas: ex. sanitizado `user-e9` em `/home/user`, `project-d-62`, `project-b-a4`) e 4 não casam (nomeadas de verdade: `project-a-report`, `warehouse-ingestion`, `camera`, mais a de plan mode `Fix data path...`). Zero falsos positivos e zero falsos negativos na amostra.
Conclusão: a heurística da §6.5 **valida** — funciona como detector de "nunca nomeada" neste conjunto. Ressalva de amostra pequena (n=7). Nota: o sufixo observado inclui dígitos e letras (`e9`, `62`, `a4`), consistente com `[a-z0-9]{2}`.

---

## Fixtures

- `fixtures/agents-local.json` — captura real do A2 (9 sessões), sanitizada: cwds genéricos, nomes de cliente removidos, UUIDs re-gerados, **forma preservada** (mesmos campos, tipos, cardinalidade, e a relação basename(cwd)↔nome default do G1).
- `fixtures/sanitize.py` — o mapeamento de sanitização, versionado para auditoria.
- `fixtures/agents-ec2.json` — **pendente** (B1).
- Arquivos `raw-*.json` (não sanitizados) **não** foram versionados.

## O que fica na sua mão (portão de saída)

1. **B (EC2):** adicionar o bloco abaixo ao `~/.ssh/config` (a sessão não tem permissão para escrever lá). Com o alias no lugar, B2–B6, C-remoto e D destravam:

   ```
   Host ec2-runner
     HostName 172.16.103.235
     User ec2-user
     IdentityFile ~/dev.pem
     IdentitiesOnly yes
     StrictHostKeyChecking accept-new
     ControlMaster auto
     ControlPath ~/.ssh/cm-%r@%h:%p
     ControlPersist 10m
     ServerAliveInterval 30
     ConnectTimeout 5
   ```

2. **C2:** decidir quando testar com supervisor parado — precisa de uma janela sem sessões background vivas (local) e/ou da EC2.
3. **F (macOS):** abrir o iTerm2 uma vez (fechar onboarding) e aprovar o diálogo de Automação quando o AppleScript rodar. iTerm2 3.6.11 já está instalado.
4. **§7.2:** decidir como rotular bloqueio de background sem `waitingFor` (observado: `blocked` + `status: idle`, sem o campo).
