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
        "last_read": "last read {when}",
        "active": "active",
        "stuck": "stuck",
        "quiet": "all quiet",
        "was": "was {state}",
        "rename_hint": "Name…",
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
        "last_read": "última leitura {when}",
        "active": "ativas",
        "stuck": "travada",
        "quiet": "tudo quieto",
        "was": "estava {state}",
        "rename_hint": "Nomear…",
    },
}


def tr(locale: str, key: str, **kwargs) -> str:
    table = STRINGS.get(locale) or STRINGS["en"]
    text = table.get(key) or STRINGS["en"].get(key, key)
    return text.format(**kwargs) if kwargs else text
