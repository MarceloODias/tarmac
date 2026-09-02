"""tarmac CLI.

Core commands: collect, render (swiftbar|tui), stats.
Action commands (used by the SwiftBar submenus): open, logs, stop, rm,
copy-resume, remember, defer, resolve, pin, next-step, checklist, gc-tabs.
"""

from __future__ import annotations

import argparse
import sys

from . import accounts as accts
from . import actions
from . import db as dbm
from .collect import collect, collect_if_stale
from .config import Config, load_config, tarmac_home
from .dates import DateParseError, human_confirmation, parse_with_fallback
from .derive import build_view
from .strings import set_locale, t


def _hook_keys() -> list[str]:
    from .hookmgr import SPECS
    return list(SPECS)


def _require_target(config: Config, target_id: str):
    t = config.target(target_id)
    if t is None:
        sys.exit(f"target desconhecido: {target_id}")
    return t


def _find_row(config: Config, conn, target_id: str, session_id: str):
    # account_filter=False: `A` hides an account from the LIST; a command that
    # already names its target and session must still reach it (accounts.py)
    view = build_view(config, conn, mine_only=False, account_filter=False)
    for bucket in (view.overdue, view.blocked, view.working, view.scheduled,
                   view.done, view.other):
        for row in bucket:
            if row.target_id == target_id and row.session_id == session_id:
                return row
    # fall back to a raw DB row (gone sessions still support copy-resume)
    r = conn.execute(
        "SELECT * FROM sessions WHERE target_id = ? AND session_id = ?",
        (target_id, session_id),
    ).fetchone()
    if r is None:
        sys.exit(f"sessão desconhecida: {target_id}/{session_id}")
    from .derive import Row
    return Row(
        target_id=target_id, target_label=target_id, session_id=session_id,
        display_name=r["name"] or session_id[:8], eff_state=r["eff_state"] or "unknown",
        kind=r["kind"] or "unknown", cwd=r["cwd"], short_id=r["short_id"],
        uuid=r["uuid"], waiting_for=r["waiting_for"], gone=bool(r["gone"]), stale=False,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tarmac")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect", help="coleta todos os targets habilitados")
    p.add_argument("--force", action="store_true", help="ignora cache e backoff")

    p = sub.add_parser("render", help="renderiza o painel")
    p.add_argument("--format", choices=["swiftbar", "tui"], required=True)
    p.add_argument("--width", type=int, default=None,
                   help="renderiza como se a janela tivesse N colunas "
                        "(reproduz o painel real sem abri-lo)")
    p.add_argument("--all-targets", action="store_true",
                   help="inclui targets com mine: false")
    p.add_argument("--once", action="store_true",
                   help="tui: renderiza uma vez e sai (para testes)")

    sub.add_parser("stats", help="tempo agregado em blocked por dia (owned)")

    sub.add_parser("poke", help="coleta só os targets locais e alerta agora "
                                "(o que o hook Notification chama)")

    p = sub.add_parser("daemon", help="estado do supervisor de sessões background")
    p.add_argument("target_id", nargs="?", help="um target só (default: todos)")

    for name in ("open", "logs", "stop", "rm", "copy-resume", "resolve", "pin",
                 "respawn"):
        p = sub.add_parser(name)
        p.add_argument("target_id")
        p.add_argument("session_id")

    p = sub.add_parser("remember", help="lembrar em… (visível, chip ⏱)")
    p.add_argument("target_id")
    p.add_argument("session_id")
    p.add_argument("when", nargs="+")

    p = sub.add_parser("defer", help="adiar até… (some da lista até vencer)")
    p.add_argument("target_id")
    p.add_argument("session_id")
    p.add_argument("when", nargs="+")

    p = sub.add_parser("next-step")
    p.add_argument("target_id")
    p.add_argument("session_id")
    p.add_argument("text", nargs="+")
    p.add_argument("--origin", choices=["auto", "manual"], default="manual")

    p = sub.add_parser("checklist")
    p.add_argument("target_id")
    p.add_argument("session_id")
    p.add_argument("action", choices=["add", "done", "list"])
    p.add_argument("value", nargs="*")

    p = sub.add_parser("gc-tabs", help="fecha abas resolvidas (SPEC §9.0.1)")
    p.add_argument("--idle-min", type=int, default=None)

    p = sub.add_parser("hook", help="hooks do Claude Code (grátis, off por padrão)")
    p.add_argument("action", choices=["status", "install", "uninstall"])
    p.add_argument("--which", action="append", default=[],
                   choices=sorted(_hook_keys()),
                   help="qual hook (repetível; default: todos)")
    p.add_argument("--exclude", default="", help="next-step: prefixos de cwd a ignorar (a:b)")
    p.add_argument("--only", default="", help="next-step: rodar SÓ nestes prefixos (a:b)")
    p.add_argument("--config-dir", default="",
                   help="um CLAUDE_CONFIG_DIR só (default: todos os targets locais)")

    p = sub.add_parser("notify", help="alerta do macOS em PRECISA DE VOCÊ (SPEC §7.3)")
    p.add_argument("action", nargs="?", default="status",
                   choices=["status", "on", "off", "toggle", "mute", "test"])
    p.add_argument("when", nargs="*",
                   help="mute: por quanto tempo (1h, 30min, 'fim do dia'); "
                        "vazio = até religar")

    p = sub.add_parser("account", help="omite uma conta da lista (`A` no painel)")
    p.add_argument("which", nargs="?",
                   help="conta a omitir (Personal, default…), 'all' para "
                        "mostrar todas, 'next' para avançar; vazio = status")

    p = sub.add_parser("task", help="tarefa avulsa: 'no benji-dp, preciso …'")
    p.add_argument("text", nargs="*", help="descrição; vazio lista as abertas")
    p.add_argument("--due", help="prazo em linguagem natural (amanhã, segunda…)")
    p.add_argument("--done", type=int, metavar="ID", help="resolve a tarefa")

    args = parser.parse_args(argv)
    config = load_config()
    set_locale(config.settings.locale)
    conn = dbm.connect()

    if args.cmd == "collect":
        results = collect(config, conn, force=args.force)
        for r in results:
            status = "ok" if r.sessions is not None else f"{r.error_kind}: {r.error}"
            print(f"{r.target.id}: {status}"
                  + (f" ({len(r.sessions)} sessões)" if r.sessions is not None else ""))
        return

    if args.cmd == "render":
        if args.format == "swiftbar":
            collect_if_stale(config, conn)
            view = build_view(config, conn, mine_only=not args.all_targets)
            from .render.swiftbar import render_swiftbar
            sys.stdout.write(render_swiftbar(config, view))
        else:
            if args.once:
                collect_if_stale(config, conn)
                view = build_view(config, conn, mine_only=not args.all_targets)
                from rich.console import Console

                from .render.tui import render_view
                console = Console(width=args.width) if args.width else Console()
                console.print(render_view(config, view, console.width))
            else:
                from .render.tui_app import run_tui
                run_tui(config)
        return

    if args.cmd == "poke":
        # A Notification hook fired on this machine: a session started waiting.
        # Read the LOCAL targets now (an ssh target's 15s timeout has no place
        # in a path meant to alert in seconds) and let the ordinary claim decide
        # what gets announced — the hook never decides anything (SPEC §7.3).
        for r in collect(config, conn, force=True, local_only=True):
            status = "ok" if r.sessions is not None else f"{r.error_kind}: {r.error}"
            print(f"{r.target.id}: {status}")
        return

    if args.cmd == "daemon":
        # `claude daemon status` tells apart "machine unreachable" from "the
        # supervisor died": in the second case `agents --json` still answers,
        # from state on disk, and every row looks fine while nothing responds.
        targets = ([_require_target(config, args.target_id)] if args.target_id
                   else config.enabled_targets())
        for tg in targets:
            try:
                proc = actions.remote_claude(tg, "daemon", "status", timeout=20)
                out = (proc.stdout or proc.stderr).strip() or f"exit {proc.returncode}"
            except Exception as e:
                out = f"{type(e).__name__}: {e}"
            print(f"── {tg.label or tg.id}")
            for line in out.splitlines():
                print(f"   {line}")
        return

    if args.cmd == "stats":
        rows = dbm.wasted_by_day(conn)
        if not rows:
            print("sem dados ainda")
            return
        for day, seconds in rows:
            h, m = divmod(seconds // 60, 60)
            print(f"{day}  {h:3d}h{m:02d}m em blocked (owned)")
        return

    if args.cmd == "gc-tabs":
        idle = args.idle_min or config.settings.idle_tab_min
        closed = actions.close_resolved_tabs(conn, idle)
        print(f"{len(closed)} aba(s) fechada(s)")
        return

    if args.cmd == "hook":
        from . import hookmgr
        # one settings.json per CLAUDE_CONFIG_DIR: a second account on this
        # machine never sees a hook installed in the other one
        config_dirs = ([args.config_dir] if args.config_dir
                       else hookmgr.local_config_dirs(config))
        which = args.which or None
        if args.action == "status":
            for cfg in config_dirs:
                found = hookmgr.status(cfg)
                for key in hookmgr.SPECS:
                    print(f"{cfg}: {key}: instalado: {key in found}")
                    if key in found:
                        print(f"  comando: {found[key]}")
                if "legacy" in found:
                    print(f"{cfg}: ⚠ hook ANTIGO de SessionEnd ainda instalado "
                          f"(gasta API a cada sessão encerrada): {found['legacy']}")
                    print("  remova com: tarmac hook install  (ou hook uninstall)")
            queue = tarmac_home() / "next-steps.jsonl"
            if queue.exists() and queue.read_text().strip():
                print(f"fila por drenar: {len(queue.read_text().splitlines())} linha(s)")
            return
        if args.action == "install":
            for cfg in config_dirs:
                for key, command in hookmgr.install(
                        which, config_dir=cfg,
                        exclude=args.exclude, only=args.only).items():
                    print(f"{cfg}: {key}: {command}")
            print("nenhum dos dois gasta API: o next-step vem do texto que a "
                  "sessão já respondeu, e o needs-you só manda o painel coletar.")
            if not hookmgr.tarmac_bin():
                print("⚠ não achei o binário `tarmac` para gravar no hook — o "
                      "needs-you vai depender do PATH da sessão.")
            return
        for cfg in config_dirs:
            gone = hookmgr.uninstall(which, config_dir=cfg)
            print(f"{cfg}: " + (", ".join(f"{k} removido" for k in gone)
                                if gone else "não estava instalado"))
        return

    if args.cmd == "notify":
        from . import notify as notifier
        if args.action == "on":
            notifier.unmute(conn)
        elif args.action == "off":
            notifier.mute(conn)
        elif args.action == "toggle":
            notifier.toggle(conn)
        elif args.action == "mute":
            if args.when:
                try:
                    until = parse_with_fallback(
                        " ".join(args.when),
                        default_hour=config.settings.default_hour,
                        end_of_day_hour=config.settings.end_of_day_hour,
                    )
                except DateParseError as e:
                    sys.exit(str(e))
                notifier.mute(conn, int(until.timestamp() * 1000))
            else:
                notifier.mute(conn)
        elif args.action == "test":
            # also the way to make macOS show the permission prompt the first
            # time: nothing is delivered until osascript is allowed to notify
            ok = notifier.send(t("notify_title"), t("notify_body_default"),
                               subtitle="tarmac", sound=config.settings.notify_sound)
            print("enviada" if ok else "falhou (System Settings > Notifications)")
        muted = notifier.is_muted(conn)
        raw = notifier.mute_until(conn)
        until = None if (raw is None or raw == notifier.FOREVER) else int(raw)
        print(notifier.status_line(config.settings.notify, muted, until,
                                   config.settings.locale))
        return

    if args.cmd == "account":
        locale = config.settings.locale
        if args.which == "next":
            if not accts.can_omit(config):
                sys.exit(t("account_only_one"))
            accts.cycle(conn, config)   # cycle() stores the new state itself
        elif args.which:
            try:
                accts.set_omitted(conn, accts.resolve(config, args.which, locale))
            except ValueError as e:
                sys.exit(str(e))
        omit = accts.effective(conn, config)
        print(t("account_showing_all") if omit is None
              else t("account_omitting", account=accts.label(omit, locale)))
        for account in accts.names(config):
            mark = "⊘" if account == omit else " "
            print(f" {mark} {accts.label(account, locale)}")
        return

    if args.cmd == "task":
        from .tasks import add_task, cwd_candidates, infer_folder, open_tasks, resolve_task, set_task_folder
        if args.done is not None:
            resolve_task(conn, args.done)
            print(f"tarefa {args.done} resolvida")
            return
        if not args.text:
            for tk in open_tasks(conn):
                due = f"  ⏱ {tk['due_label']}" if tk["due_label"] else ""
                folder = f"  → {tk['cwd']}" if tk["cwd"] else ""
                print(f"[{tk['id']}] {tk['text']}{due}{folder}")
            return
        text = " ".join(args.text)
        due_at = due_label = None
        if args.due:
            try:
                due = parse_with_fallback(
                    args.due,
                    default_hour=config.settings.default_hour,
                    end_of_day_hour=config.settings.end_of_day_hour,
                )
            except DateParseError as e:
                sys.exit(str(e))
            due_at, due_label = int(due.timestamp() * 1000), args.due
            print(human_confirmation(due, locale=config.settings.locale))
        task_id = add_task(conn, text, due_at, due_label)
        guess = infer_folder(text, cwd_candidates(conn))
        if guess:
            set_task_folder(conn, task_id, guess.target_id, guess.cwd)
            print(f"tarefa {task_id} criada → {guess.cwd} ({guess.target_id})")
        else:
            print(f"tarefa {task_id} criada (pasta será perguntada ao abrir)")
        return

    target = _require_target(config, args.target_id)
    row = _find_row(config, conn, args.target_id, args.session_id)

    if args.cmd == "open":
        print(actions.open_or_focus(conn, target, row))
        collect(config, conn, force=True)  # refresh after state-changing action
    elif args.cmd == "logs":
        if not row.short_id:
            sys.exit("sessão sem short_id — logs indisponíveis (interactive)")
        proc = actions.remote_claude(target, "logs", row.short_id)
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
    elif args.cmd == "stop":
        if not row.short_id:
            sys.exit("sessão sem short_id — stop indisponível (interactive)")
        proc = actions.remote_claude(target, "stop", row.short_id)
        print(proc.stdout.strip() or proc.stderr.strip())
        collect(config, conn, force=True)
    elif args.cmd == "respawn":
        # restarts a session's process, running or stopped: the answer to a row
        # that stopped responding, and the way to move one onto a new binary
        if not row.short_id:
            sys.exit("sessão sem short_id — respawn indisponível (interactive)")
        proc = actions.remote_claude(target, "respawn", row.short_id)
        print(proc.stdout.strip() or proc.stderr.strip())
        collect(config, conn, force=True)
    elif args.cmd == "rm":
        # double confirmation (SPEC §9.2): rm may delete the session worktree
        if not row.short_id:
            sys.exit("sessão sem short_id — rm indisponível")
        answer = input(
            f"Remover {row.display_name}? Pode apagar o worktree da sessão, "
            "incluindo alterações não commitadas. Digite 'remover' para confirmar: "
        )
        if answer.strip().lower() != "remover":
            print("cancelado")
            return
        proc = actions.remote_claude(target, "rm", row.short_id)
        print(proc.stdout.strip() or proc.stderr.strip())
        collect(config, conn, force=True)
    elif args.cmd == "copy-resume":
        cmd = actions.resume_command(target, row)
        if actions.copy_to_clipboard(cmd):
            print(t("copied", cmd=cmd))
        else:
            print(cmd)
    elif args.cmd in ("remember", "defer"):
        text = " ".join(args.when)
        try:
            due = parse_with_fallback(
                text,
                default_hour=config.settings.default_hour,
                end_of_day_hour=config.settings.end_of_day_hour,
            )
        except DateParseError as e:
            sys.exit(str(e))  # never fail silently (SPEC §6.2)
        dbm.upsert_meta(
            conn, args.target_id, args.session_id,
            due_at=int(due.timestamp() * 1000),
            due_label=text,
            hide_until_due=1 if args.cmd == "defer" else 0,
            resolved_at=None,
        )
        conn.commit()
        print(human_confirmation(due, locale=config.settings.locale))
    elif args.cmd == "resolve":
        dbm.upsert_meta(conn, args.target_id, args.session_id,
                        resolved_at=dbm.now_ms())
        conn.commit()
        print("resolvido")
    elif args.cmd == "pin":
        meta = dbm.get_meta(conn, args.target_id, args.session_id)
        new = 0 if (meta and meta["pinned"]) else 1
        dbm.upsert_meta(conn, args.target_id, args.session_id, pinned=new)
        conn.commit()
        print("fixado" if new else "desafixado")
    elif args.cmd == "next-step":
        text = " ".join(args.text)
        meta = dbm.get_meta(conn, args.target_id, args.session_id)
        # an auto hook must never overwrite what I typed by hand (SPEC §10)
        if (args.origin == "auto" and meta and meta["next_step"]
                and meta["next_step_origin"] == "manual"):
            print("mantido next_step manual")
            return
        dbm.upsert_meta(conn, args.target_id, args.session_id,
                        next_step=text, next_step_origin=args.origin)
        conn.commit()
        print(f"→ {text}")
    elif args.cmd == "checklist":
        if args.action == "add":
            pos = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM session_checklist "
                "WHERE target_id = ? AND session_id = ?",
                (args.target_id, args.session_id),
            ).fetchone()["p"]
            conn.execute(
                "INSERT INTO session_checklist (target_id, session_id, position, text, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (args.target_id, args.session_id, pos, " ".join(args.value), dbm.now_ms()),
            )
            conn.commit()
            print(f"item {pos} adicionado")
        elif args.action == "done":
            conn.execute(
                "UPDATE session_checklist SET done = 1, done_at = ? "
                "WHERE target_id = ? AND session_id = ? AND position = ?",
                (dbm.now_ms(), args.target_id, args.session_id, int(args.value[0])),
            )
            conn.commit()
            print("feito")
        else:
            for r in conn.execute(
                "SELECT position, text, done FROM session_checklist "
                "WHERE target_id = ? AND session_id = ? ORDER BY position",
                (args.target_id, args.session_id),
            ):
                print(f"[{'x' if r['done'] else ' '}] {r['position']}. {r['text']}")


if __name__ == "__main__":
    main()
