"""tarmac CLI.

Core commands: collect, render (swiftbar|tui), stats.
Action commands (used by the SwiftBar submenus): open, logs, stop, rm,
copy-resume, remember, defer, resolve, pin, next-step, checklist, gc-tabs.
"""

from __future__ import annotations

import argparse
import sys

from . import actions
from . import db as dbm
from .collect import collect, collect_if_stale
from .config import Config, load_config, tarmac_home
from .dates import DateParseError, human_confirmation, parse_with_fallback
from .derive import build_view
from .strings import set_locale, t


def _require_target(config: Config, target_id: str):
    t = config.target(target_id)
    if t is None:
        sys.exit(f"target desconhecido: {target_id}")
    return t


def _find_row(config: Config, conn, target_id: str, session_id: str):
    view = build_view(config, conn, mine_only=False)
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

    for name in ("open", "logs", "stop", "rm", "copy-resume", "resolve", "pin"):
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

    p = sub.add_parser("hook", help="hook de next_step (custa API — off por padrão)")
    p.add_argument("action", choices=["status", "install", "uninstall"])
    p.add_argument("--exclude", default="", help="prefixos de cwd a ignorar (a:b)")
    p.add_argument("--only", default="", help="rodar SÓ nestes prefixos de cwd (a:b)")
    p.add_argument("--max-day", type=int, default=20, help="teto de chamadas por dia")
    p.add_argument("--model", default="", help="modelo (default: haiku, barato)")
    p.add_argument("--config-dir", default="",
                   help="um CLAUDE_CONFIG_DIR só (default: todos os targets locais)")

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
        if args.action == "status":
            for cfg in config_dirs:
                installed, command = hookmgr.status(cfg)
                print(f"{cfg}: instalado: {installed}")
                if installed:
                    print(f"  comando: {command}")
            # dedupe/counter are machine-wide, shared by every config dir
            seen = tarmac_home() / "next-steps.seen"
            count = tarmac_home() / "next-steps.count"
            if seen.exists():
                print(f"sessões já resumidas: {len(seen.read_text().splitlines())}")
            if count.exists():
                print(f"contador do dia: {count.read_text().strip()}")
            return
        if args.action == "install":
            for cfg in config_dirs:
                command = hookmgr.install(args.exclude, args.only, args.max_day,
                                          args.model, config_dir=cfg)
                print(f"{cfg}: instalado: {command}")
            print("cada sessão encerrada gasta 1 chamada (transcript inteiro como "
                  "input). Teto diário e dedupe por sessão estão ativos.")
            return
        for cfg in config_dirs:
            print(f"{cfg}: " + ("removido" if hookmgr.uninstall(cfg)
                                else "não estava instalado"))
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
