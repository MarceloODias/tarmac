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

`claude agents` ships with Claude Code and solves most of this on one machine:
live session list, states, attach. If that's your whole world, use it and skip
this repo. `tarmac` adds what the native view doesn't have:

- **multiple machines/accounts** in one place (local + SSH targets)
- **wait time**: how long each blocked session has been waiting, and the worst
  one in your menu bar
- **intent**: per-session reminders ("this matters again on Monday"),
  checklists, next steps
- **open-or-focus**: click a session, land in its terminal tab — never two
  tabs on the same session

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
  enforced by a test.
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
| `tarmac render --format tui` | dedicated-window panel, refreshes every 60s |
| `tarmac render --format swiftbar` | menu-bar plugin output |
| `tarmac stats` | aggregate blocked time per day (the tool's own KPI) |
| `tarmac open <target> <session>` | open-or-focus the session's terminal tab |
| `tarmac remember <t> <s> 5h` / `defer` | reminder (visible chip / hidden until due) |
| `tarmac checklist <t> <s> add "…"` | per-session checklist |
| `tarmac logs / stop / rm / copy-resume / pin` | per-session actions |
| `tarmac gc-tabs` | close tabs of no-longer-blocked sessions (never automatic) |

## Limitations, plainly

- No notifications, by design: the badge/panel *is* the alert.
- Session aliases are local to the panel; `claude --resume <alias>` won't
  resolve them (resume always uses the UUID).
- Inline replies to blocked sessions don't work — the CLI refuses to attach a
  second process to a running background agent (verified; see FINDINGS.md).
- Terminal integration is iTerm2-only today; anything else degrades to
  copying the attach command to the clipboard.
- Requires a recent Claude Code (developed against 2.1.226; agent view is a
  research preview and its schema may drift — the parser is tolerant and the
  raw payload is stored for debugging).

## Development

```bash
uv run pytest        # fixtures come from real --json output, sanitized
```

`FINDINGS.md` documents the reconnaissance that grounded these decisions;
`DECISIONS.md` lists every judgment call made where the spec was open.

## License

MIT
