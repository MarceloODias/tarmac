"""Minimal i18n (SPEC §15.2): pt and en dictionaries, en is the repo default.

The user's locale comes from settings.locale in targets.yaml.
"""

from __future__ import annotations

STRINGS = {
    "en": {
        "for_today": "FOR TODAY",
        "needs_you": "NEEDS YOU",
        "working": "WORKING",
        "scheduled": "SCHEDULED",
        "services": "SERVICES",
        "done": "DONE",
        "idle": "IDLE",
        "targets": "Targets",
        "mine": "Mine",
        "all": "All",
        "updated_ago": "Updated {ago} ago",
        "refresh": "Refresh now",
        "open": "Open",
        "logs": "View logs",
        "remind_in": "Remind me in…",
        "defer_until": "Defer until…",
        "checklist": "Checklist…",
        "set_next_step": "Set next step…",
        "pin": "Pin",
        "unpin": "Unpin",
        "stop": "Stop session",
        "copy_resume": "Copy resume command",
        "resolve": "Resolve",
        "reschedule": "Reschedule…",
        "overdue_ago": "overdue {ago}",
        "offline_for": "offline for {ago}",
        "stale_for": "not read for {ago}",
        "last_read": "last read {when}",
        "active": "active",
        "stuck": "stuck",
        "quiet": "all quiet",
        "was": "was {state}",
        "rename_hint": "Name…",
        # footer key bindings (TUI)
        "bind_open": "open",
        "bind_task": "task",
        "bind_resume_tab": "resume in tab",
        "bind_copy_resume": "copy resume",
        "bind_pin": "pin",
        "bind_remember": "remind",
        "bind_defer": "defer",
        "bind_next_step": "next step",
        "bind_logs": "logs",
        "bind_resolve": "resolve",
        "bind_stop": "stop",
        "bind_remove": "remove from list",
        "bind_refresh": "refresh",
        "bind_quit": "quit",
        # TUI messages and modals
        "no_process": "no process",
        "confirm_hint": "[b]enter[/b] confirms · [b]esc[/b] cancels",
        "collect_failed": "collect failed: {err}",
        "config_reloaded": "targets.yaml reloaded",
        "config_reload_failed": "targets.yaml is invalid, keeping the old one: {err}",
        "new_task": "New task",
        "new_task_hint": "in benji-dp, I need to split the rampids across ssps…",
        "task_created_folder": "task created → {cwd}",
        "task_created_ask": "task created (folder asked when you open it)",
        "unknown_target": "unknown target: {target}",
        "no_folder_history": "no folder history yet — run some sessions first",
        "folder_pick_title": "Which folder does this task start in?",
        "attaching_live": "live session — attaching (the CLI refuses resume on an active session)",
        "copied": "copied: {cmd}",
        "due_hint": "5h · tomorrow · monday · 15/09 …",
        "due_shortcuts": "2h|tomorrow|monday",
        "really_blocked": "this session really is blocked: answer it with Enter, "
                          "or defer with 'a'. 'x' only clears an overdue reminder.",
        "nothing_to_resolve": "nothing to resolve here — 'x' is for an overdue reminder",
        "no_short_id_stop": "session without short_id — stop unavailable",
        "confirm_stop": "Stop {name}?",
        "stopped": "stopped",
        "task_use_x": "task: press 'x' to resolve",
        "interactive_no_remove": "an interactive session cannot be removed from the list",
        "confirm_remove": "Remove {name} from the list?\n"
                          "This may delete the worktree the session created, including "
                          "uncommitted changes.\nTo restart it from scratch: "
                          "claude respawn {short_id}",
        "removed": "removed",
        # notifications (SPEC §7.3)
        "notify_title": "Needs you",
        "notify_title_many": "{n} sessions need you",
        "notify_body_default": "waiting for you",
        "notify_state_on": "Notifications: on",
        "notify_state_off": "Notifications: off",
        "notify_state_until": "Notifications: muted until {when}",
        "notify_muted_badge": "🔕",
        "notify_mute": "Mute notifications",
        "notify_unmute": "Unmute notifications",
        "notify_mute_for": "Mute for {when}",
        "notify_mute_eod": "Mute until end of day",
        "notify_unsupported": "notifications need macOS — nothing will be sent here",
        "bind_notify": "mute alerts",
        # actions
        "no_attach_id": "session without short_id and without uuid — nothing to attach",
        "no_resume_id": "session without uuid — no resume command",
        "no_logs_id": "session without short_id — logs only exist for background sessions",
        "opened": "opened",
        "focused": "focused",
        "iterm_copied": "iTerm2 unavailable ({msg}). Command copied to the clipboard: {cmd}",
        "iterm_manual": "iTerm2 unavailable ({msg}). Command to paste manually: {cmd}",
        "confirm_respawn": "Restart the process of {name}?\n"
                           "It picks the conversation up where it stopped.",
        "respawned": "restarted",
        "no_short_id_respawn": "session without short_id — respawn only exists "
                               "for background sessions",
        "bind_respawn": "respawn",
        # `waitingFor`, as documented in the agent view reference. Unknown values
        # render as they arrive: the field is the CLI's, not ours, and a value we
        # have never seen is still worth showing.
        "blocked_generic": "blocked",
        "wf_permission prompt": "permission prompt",
        "wf_input needed": "waiting for an answer",
        "wf_sandbox request": "sandbox request",
        "wf_worker request": "worker request",
        "wf_dialog open": "dialog open",
    },
    "pt": {
        "for_today": "PRA HOJE",
        "needs_you": "PRECISA DE VOCÊ",
        "working": "TRABALHANDO",
        "scheduled": "AGENDADO",
        "services": "SERVIÇOS",
        "done": "CONCLUÍDO",
        "idle": "OCIOSO",
        "targets": "Targets",
        "mine": "Meus",
        "all": "Todos",
        "updated_ago": "Atualizado há {ago}",
        "refresh": "Atualizar agora",
        "open": "Abrir",
        "logs": "Ver logs",
        "remind_in": "Lembrar em…",
        "defer_until": "Adiar até…",
        "checklist": "Checklist…",
        "set_next_step": "Definir próximo passo…",
        "pin": "Fixar",
        "unpin": "Desafixar",
        "stop": "Parar sessão",
        "copy_resume": "Copiar comando de resume",
        "resolve": "Resolver",
        "reschedule": "Reagendar…",
        "overdue_ago": "venceu há {ago}",
        "offline_for": "offline há {ago}",
        "stale_for": "sem leitura há {ago}",
        "last_read": "última leitura {when}",
        "active": "ativas",
        "stuck": "travada",
        "quiet": "tudo quieto",
        "was": "estava {state}",
        "rename_hint": "Nomear…",
        # footer key bindings (TUI)
        "bind_open": "abrir",
        "bind_task": "tarefa",
        "bind_resume_tab": "resume em aba",
        "bind_copy_resume": "copiar resume",
        "bind_pin": "fixar",
        "bind_remember": "lembrar",
        "bind_defer": "adiar",
        "bind_next_step": "próx. passo",
        "bind_logs": "logs",
        "bind_resolve": "resolver",
        "bind_stop": "parar",
        "bind_remove": "remover da lista",
        "bind_refresh": "atualizar",
        "bind_quit": "sair",
        # TUI messages and modals
        "no_process": "sem processo",
        "confirm_hint": "[b]enter[/b] confirma · [b]esc[/b] cancela",
        "collect_failed": "coleta falhou: {err}",
        "config_reloaded": "targets.yaml recarregado",
        "config_reload_failed": "targets.yaml inválido, mantendo o anterior: {err}",
        "new_task": "Nova tarefa",
        "new_task_hint": "no benji-dp, preciso dividir os rampids em ssps…",
        "task_created_folder": "tarefa criada → {cwd}",
        "task_created_ask": "tarefa criada (pasta será perguntada ao abrir)",
        "unknown_target": "target desconhecido: {target}",
        "no_folder_history": "sem histórico de pastas ainda — rode sessões primeiro",
        "folder_pick_title": "Em qual pasta esta tarefa começa?",
        "attaching_live": "sessão viva — anexando (o CLI recusa resume em sessão ativa)",
        "copied": "copiado: {cmd}",
        "due_hint": "5h · amanhã · segunda · 15/09 …",
        "due_shortcuts": "2h|amanhã|segunda",
        "really_blocked": "sessão bloqueada de verdade: responda com Enter, "
                          "ou adie com 'a'. 'x' só resolve lembrete vencido.",
        "nothing_to_resolve": "nada a resolver aqui — 'x' vale para lembrete vencido",
        "no_short_id_stop": "sessão sem short_id — stop indisponível",
        "confirm_stop": "Parar {name}?",
        "stopped": "parado",
        "task_use_x": "tarefa: use 'x' para resolver",
        "interactive_no_remove": "sessão interativa não pode ser removida da lista",
        "confirm_remove": "Remover {name} da lista?\n"
                          "Pode apagar o worktree criado pela sessão, incluindo "
                          "alterações não commitadas.\nPara reiniciá-la do zero: "
                          "claude respawn {short_id}",
        "removed": "removido",
        # notifications (SPEC §7.3)
        "notify_title": "Precisa de você",
        "notify_title_many": "{n} sessões precisam de você",
        "notify_body_default": "esperando por você",
        "notify_state_on": "Notificações: ligadas",
        "notify_state_off": "Notificações: desligadas",
        "notify_state_until": "Notificações: silenciadas até {when}",
        "notify_muted_badge": "🔕",
        "notify_mute": "Silenciar notificações",
        "notify_unmute": "Religar notificações",
        "notify_mute_for": "Silenciar por {when}",
        "notify_mute_eod": "Silenciar até o fim do dia",
        "notify_unsupported": "notificações exigem macOS — nada será enviado aqui",
        "bind_notify": "silenciar",
        # actions
        "no_attach_id": "sessão sem short_id e sem uuid — nada para anexar",
        "no_resume_id": "sessão sem uuid — não há comando de resume",
        "no_logs_id": "sessão sem short_id — logs só existem para background",
        "opened": "aberto",
        "focused": "focado",
        "iterm_copied": "iTerm2 indisponível ({msg}). Comando copiado para o clipboard: {cmd}",
        "iterm_manual": "iTerm2 indisponível ({msg}). Comando para colar manualmente: {cmd}",
        "confirm_respawn": "Reiniciar o processo de {name}?\n"
                           "A conversa continua de onde parou.",
        "respawned": "reiniciada",
        "no_short_id_respawn": "sessão sem short_id — respawn só existe para "
                               "sessões background",
        "bind_respawn": "respawn",
        "blocked_generic": "bloqueada",
        "wf_permission prompt": "pedido de permissão",
        "wf_input needed": "esperando resposta",
        "wf_sandbox request": "pedido do sandbox",
        "wf_worker request": "pedido do worker",
        "wf_dialog open": "diálogo aberto",
    },
}


def waiting_label(locale: str, raw: str | None) -> str:
    """`waitingFor` in the panel's language.

    The five values the agent view documents get a translation; anything else
    is shown verbatim. The JSON is a research preview and this field is its
    vocabulary — a value we do not know is news, not an error, and hiding it
    behind a generic 'blocked' would throw away the only thing the row says
    about what it is waiting for.
    """
    if not raw:
        return tr(locale, "blocked_generic")
    return tr(locale, f"wf_{raw}") if f"wf_{raw}" in STRINGS["en"] else raw

_locale = "en"


def set_locale(locale: str) -> None:
    """Process-wide locale, set once at startup from settings.locale.

    Modules that raise or return user-facing text without a Config in hand
    (actions.py) read it through `t()`; renderers that already have the config
    keep passing the locale explicitly to `tr()`.
    """
    global _locale
    _locale = locale if locale in STRINGS else "en"


def t(key: str, **kwargs) -> str:
    return tr(_locale, key, **kwargs)


def tr(locale: str, key: str, **kwargs) -> str:
    table = STRINGS.get(locale) or STRINGS["en"]
    text = table.get(key) or STRINGS["en"].get(key, key)
    return text.format(**kwargs) if kwargs else text
