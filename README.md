# tarmac

*A panel for the Claude Code sessions that are waiting on you.*

You run several Claude Code agents in parallel, across machines. One of them
stopped 40 minutes ago waiting for a single keystroke — and nobody saw it,
because its terminal is buried behind six others. The cost of parallel agents
isn't compute; it's the mental ledger of what exists, where it lives, and what
is blocked on *you*.

`tarmac` answers that question continuously: a TUI for a dedicated window (or
second monitor) and a macOS menu-bar badge (SwiftBar), aggregating every
machine you run agents on — with the **longest wait time**, not a session
count, as the headline.

## What's already native (read this first)

`claude agents` ships with Claude Code and solves most of this on one machine,
and it keeps growing into this space — as of 2.1.251 it lists sessions with
their state, shows `waiting 3m` in the peek panel, lets you reply without
attaching, counts the sessions waiting on you in the prompt footer (`← 2
agents`) and in the terminal tab title, and fires a desktop notification when a
background session starts needing you. If one machine is your whole world, use
it and skip this repo.

What `tarmac` still adds:

- **multiple machines/accounts** in one place (local + SSH targets) — the
  native view is per-machine, and background sessions are local by design
- **wait time as the headline**: the worst wait across every machine, in your
  menu bar, escalating with age
- **intent**: per-session reminders ("this matters again on Monday"),
  checklists, next steps
- **open-or-focus**: click a session, land in its terminal tab — never two
  tabs on the same session
- **alerts that survive a closed panel**: a `Notification` hook pushes into the
  panel the moment a session starts waiting
- **one account at a time, when you want it**: `A` leaves the personal
  account out of the list (badge included) without touching the work one

It does **not** orchestrate agents, render transcripts, manage worktrees, or
schedule execution (Claude Code has native scheduled tasks for that).

## Quickstart

```bash
uv sync                                  # or: pip install -e .
cp targets.example.yaml ~/.tarmac/targets.yaml   # edit to taste
uv run tarmac collect                    # first collection
uv run tarmac render --format tui       # the panel (60s refresh loop)
```

SwiftBar (menu-bar fallback for laptop mode): install
[SwiftBar](https://swiftbar.app), then create a plugin that execs the renderer:

```bash
cat > ~/SwiftBar/tarmac.30s.sh <<'EOF'
#!/bin/bash
exec /path/to/.venv/bin/tarmac render --format swiftbar
EOF
chmod +x ~/SwiftBar/tarmac.30s.sh
```

macOS note: opening tabs uses AppleScript against iTerm2 — the first use asks
for an Automation permission, once per host app (Terminal, SwiftBar, …).

## Design principles

- **Never parse the JSONL transcripts.** The only source of truth is
  `claude agents --json --all`. The on-disk session files are internal to
  Claude Code and change between releases; every third-party tool that reads
  them breaks on a silent update. This rule shapes the whole design and is
  enforced by a test. A hook payload is not a violation of it: what a hook
  receives on stdin is a documented interface Claude Code hands us, and even
  then a hook never decides state — the `Notification` hook only asks the
  panel to go read the JSON sooner.
- **Two storage layers.** A disposable mirror of the JSON (rebuildable at any
  time) and a small intent layer (aliases, reminders, checklists) that
  survives sessions vanishing from the listing.
- **"Can't see it" ≠ "it's broken."** A target behind a VPN goes offline for
  hours; that renders gray and calm, with the age of the last snapshot always
  visible. Only real errors (bad key, missing binary) are loud.
- **Honest numbers.** If a session blocked while its machine was unreachable,
  the wait shows as `≥` a bound — never a falsely precise figure.

## Commands

| Command | What it does |
|---|---|
| `tarmac collect [--force]` | poll all enabled targets (parallel, 15s timeout each) |
| `tarmac poke` | collect the **local** targets only, and alert — what the `Notification` hook runs |
| `tarmac render --format tui` | dedicated-window panel, refreshes every 60s |
| `tarmac render --format swiftbar` | menu-bar plugin output |
| `tarmac stats` | aggregate blocked time per day (the tool's own KPI) |
| `tarmac open <target> <session>` | open-or-focus the session's terminal tab |
| `tarmac remember <t> <s> 5h` / `defer` | reminder (visible chip / hidden until due) |
| `tarmac checklist <t> <s> add "…"` | per-session checklist |
| `tarmac logs / stop / rm / respawn / copy-resume / pin` | per-session actions |
| `tarmac daemon [target]` | `claude daemon status` per target — is the supervisor alive? |
| `tarmac gc-tabs` | close tabs of no-longer-blocked sessions (never automatic) |
| `tarmac notify [on\|off\|mute 1h\|test]` | macOS alert when a session starts needing you |
| `tarmac account [Personal\|default\|all\|next]` | leave one account out of the list (`A` in the panel) |
| `tarmac hook [status\|install\|uninstall]` | the two Claude Code hooks below (neither costs a token) |

## The two hooks

Both are off until you run `tarmac hook install`, and both are installed into
every local account's `settings.json` (one per `CLAUDE_CONFIG_DIR`):

- **`Stop` → next step.** The event hands the hook the text of the reply that
  just ended (`last_assistant_message`), so the panel can show where a session
  stopped without asking a model anything. The note is the session's own
  sentence, condensed and cut at 200 characters — not a summary of it.
- **`Notification` → needs you.** When a session starts waiting on a human,
  the hook runs `tarmac poke`. It decides nothing: the alert still comes from
  a normal collect, through the same claim, the same mute and the same
  filters — the hook only removes the wait for the next cycle.

`tarmac hook install` only reaches **local** targets — one `settings.json` per
`CLAUDE_CONFIG_DIR` on this machine. On an SSH host you install by hand, and
only the `Stop` hook is worth installing there: `needs-you` runs `tarmac poke`
on the machine that fired it, and a remote machine has no way to alert your Mac.

An earlier version summarised sessions with `claude -p --resume`, which cost a
call per session and, before the guards, burned a usage limit. `tarmac hook
install` removes that old `SessionEnd` entry wherever it finds it; `tarmac hook
status` shouts if one is still there (including one you installed by hand on a
remote host — that one you have to remove yourself).

## Limitations, plainly

- A macOS notification fires when a session *enters* NEEDS YOU — once per
  episode, never for `service` sessions or someone else's box, and never as a
  burst on a cold database. Silence it with `N` in the panel, the menu-bar
  item, or `tarmac notify mute 1h` (a timed mute switches itself back on, which
  is the one to use before a meeting). Muted panels show `🔕` in the badge.
- With the `Notification` hook installed, a **local** session alerts within
  seconds and with the panel closed. **SSH targets don't**: nothing on a remote
  host can reach your Mac, so those still surface on the 60s cycle, which needs
  the panel open. On a brand-new database the first collect of a target is
  deliberately silent, so install the hook, let one cycle run, and only then
  expect a push.
- `agent_needs_input` (the type that covers background sessions) only fires
  while `claude agents` is open in a terminal — that is Claude Code's rule, not
  ours. `permission_prompt` has no such condition.
- `A` omits exactly **one** account from the list at a time (`⊘ <account>` in
  the badge while it is on, and the badge stops counting what the list stops
  showing). An omitted account is also silent — a banner about a row the panel
  is hiding is an alert you cannot act on — so to hide nothing and still be
  quiet, use `N` instead. Actions still reach a hidden session by name:
  `tarmac open`, a SwiftBar click and scripts ignore the filter.
- Session aliases are local to the panel; `claude --resume <alias>` won't
  resolve them (resume always uses the UUID).
- The panel can't answer a blocked session: `claude agents` does that natively
  now (`Space` opens a peek panel with a reply box), but there is no scriptable
  path to it. The CLI still refuses to attach a second process to a running
  background agent (FINDINGS.md), and the cross-session messaging socket
  delivers *messages*, which is not the same as answering a permission prompt.
  Enter opens the session's terminal; you answer there.
- Terminal integration is iTerm2-only today; anything else degrades to
  copying the attach command to the clipboard.
- Requires a recent Claude Code (developed against 2.1.226, re-verified against
  2.1.251 — the `agents --json` schema is unchanged; agent view is a research
  preview and may still drift, so the parser is tolerant and the raw payload is
  stored for debugging).

## Development

```bash
uv run pytest        # fixtures come from real --json output, sanitized
```

`FINDINGS.md` documents the reconnaissance that grounded these decisions;
`DECISIONS.md` lists every judgment call made where the spec was open.

## License

MIT
