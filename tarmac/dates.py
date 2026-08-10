"""Natural-language date input (SPEC §6.2, §6.3).

Order: deterministic regex first (covers the spec's list at zero cost), then a
`claude -p` fallback only when the regex misses. Never fails silently: raises
DateParseError so the UI can keep the field open.

Anchors: dates without a time land at settings.default_hour (09:00); "fim do
dia" is 18:00; weekday names always mean the next FUTURE occurrence; relative
durations are exact from now. Timezone is always the Mac's (SPEC §6.3);
storage is epoch ms UTC.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta


class DateParseError(ValueError):
    pass


# The fallback exists to rescue an odd phrasing, not to spend a frontier model
# on date arithmetic. Overridable via settings if someone wants otherwise.
FALLBACK_MODEL = "claude-haiku-4-5-20251001"

ACCEPTED_FORMS = "5h · 30min · 2d · amanhã · na segunda · sexta 14h · dia 15 · 15/09"


WEEKDAYS = {
    # pt (full + abbreviated)
    "segunda": 0, "segunda-feira": 0, "seg": 0,
    "terça": 1, "terca": 1, "terça-feira": 1, "terca-feira": 1, "ter": 1,
    "quarta": 2, "quarta-feira": 2, "qua": 2,
    "quinta": 3, "quinta-feira": 3, "qui": 3,
    "sexta": 4, "sexta-feira": 4, "sex": 4,
    "sábado": 5, "sabado": 5, "sab": 5, "sáb": 5,
    "domingo": 6, "dom": 6,
    # en
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

_DUR = re.compile(
    r"^(\d+)\s*(min|mins|minuto|minutos|m|h|hora|horas|hour|hours|d|dia|dias|day|days|sem|semana|semanas|week|weeks)$"
)
_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*h?$")
_DAY_OF_MONTH = re.compile(r"^dia\s+(\d{1,2})$")
_DATE_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$")


def _at_hour(d: datetime, hour: int, minute: int = 0) -> datetime:
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _next_weekday(now: datetime, weekday: int, default_hour: int) -> datetime:
    """Always the next FUTURE occurrence — 'segunda' said on a Monday means
    next Monday, not today (SPEC §6.3)."""
    days = (weekday - now.weekday()) % 7
    if days == 0:
        days = 7
    return _at_hour(now + timedelta(days=days), default_hour)


def parse_natural(
    text: str,
    now: datetime | None = None,
    default_hour: int = 9,
    end_of_day_hour: int = 18,
) -> datetime:
    """Deterministic layer. Raises DateParseError if nothing matches."""
    now = now or datetime.now().astimezone()
    t = " ".join(text.strip().lower().split())
    if not t:
        raise DateParseError("entrada vazia")

    # durations: 5h · 30min · 2d · 3 dias · 2 semanas — exact from now
    m = _DUR.match(t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit.startswith(("min", "m")) and unit not in ("mes", "meses"):
            if unit == "m" or unit.startswith("min"):
                return now + timedelta(minutes=n)
        if unit in ("h", "hora", "horas", "hour", "hours"):
            return now + timedelta(hours=n)
        if unit in ("d", "dia", "dias", "day", "days"):
            return now + timedelta(days=n)
        if unit in ("sem", "semana", "semanas", "week", "weeks"):
            return now + timedelta(weeks=n)

    if t in ("amanhã", "amanha", "tomorrow"):
        return _at_hour(now + timedelta(days=1), default_hour)
    if t in ("amanhã cedo", "amanha cedo", "tomorrow morning"):
        return _at_hour(now + timedelta(days=1), 8)
    if t in ("fim do dia", "end of day", "eod"):
        return _at_hour(now, end_of_day_hour)
    if t in ("fim da semana", "end of week", "eow"):
        return _next_weekday(now, 4, end_of_day_hour)  # Friday end-of-day
    if t in ("próximo mês", "proximo mes", "next month"):
        year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        return _at_hour(now.replace(year=year, month=month, day=1), default_hour)

    # weekday, optionally prefixed: 'na segunda' · 'próxima terça' · 'next tue'
    wd_text = re.sub(r"^(na|no|em|próxima|proxima|próximo|proximo|next|on)\s+", "", t)
    parts = wd_text.split()
    if parts and parts[0] in WEEKDAYS:
        base = _next_weekday(now, WEEKDAYS[parts[0]], default_hour)
        if len(parts) == 2:  # 'sexta 14h'
            tm = _TIME.match(parts[1])
            if tm:
                return _at_hour(base, int(tm.group(1)), int(tm.group(2) or 0))
            raise DateParseError(f"hora não entendida: {parts[1]!r}")
        if len(parts) == 1:
            return base

    # 'dia 15' — next occurrence of that day of month
    m = _DAY_OF_MONTH.match(t)
    if m:
        day = int(m.group(1))
        candidate = _replace_day(now, day)
        if candidate <= now:
            candidate = _replace_day(
                now.replace(year=now.year + 1, month=1)
                if now.month == 12 else now.replace(month=now.month + 1),
                day,
            )
        return _at_hour(candidate, default_hour)

    # '15/09' or '15/09/2026'
    m = _DATE_SLASH.match(t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.year
        if year < 100:
            year += 2000
        try:
            candidate = _at_hour(now.replace(year=year, month=month, day=day), default_hour)
        except ValueError as e:
            raise DateParseError(str(e)) from e
        if not m.group(3) and candidate <= now:
            candidate = candidate.replace(year=year + 1)
        return candidate

    raise DateParseError(f"não entendi: {text!r}")


def _replace_day(d: datetime, day: int) -> datetime:
    try:
        return d.replace(day=day)
    except ValueError as e:
        raise DateParseError(f"dia inválido: {day}") from e


def parse_with_fallback(
    text: str,
    now: datetime | None = None,
    default_hour: int = 9,
    end_of_day_hour: int = 18,
    claude_bin: str = "claude",
    model: str = FALLBACK_MODEL,
) -> datetime:
    """Regex first; `claude -p` only when it misses (SPEC §6.2).

    Pinned to a cheap model on purpose: without --model this inherits whatever
    the user's default is (here, Opus 1M) to convert four words into a date.
    """
    now = now or datetime.now().astimezone()
    try:
        return parse_natural(text, now, default_hour, end_of_day_hour)
    except DateParseError:
        pass
    prompt = (
        f"Hoje é {now.isoformat()}. Converta para timestamp ISO 8601: '{text}'. "
        "Responda apenas o timestamp."
    )
    try:
        proc = subprocess.run(
            [claude_bin, "-p", "--model", model, "--output-format", "json", prompt],
            capture_output=True, text=True, timeout=60,
        )
        result = json.loads(proc.stdout).get("result", "").strip()
        parsed = datetime.fromisoformat(result)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=now.tzinfo)
        return parsed
    except Exception as e:
        raise DateParseError(f"não entendi {text!r} (fallback: {e})") from e


def human_confirmation(due: datetime, now: datetime | None = None, locale: str = "pt") -> str:
    """'em 3d (seg, 11/08 09:00)' — always confirm the resolved absolute date
    next to the relative one (SPEC §6.3)."""
    now = now or datetime.now().astimezone()
    delta = due - now
    days = delta.days
    secs = delta.seconds
    if days > 0:
        rel = f"{days}d"
    elif secs >= 3600:
        rel = f"{secs // 3600}h"
    else:
        rel = f"{max(1, secs // 60)}min"
    wd = (["seg", "ter", "qua", "qui", "sex", "sáb", "dom"] if locale == "pt"
          else ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])[due.weekday()]
    prefix = "em" if locale == "pt" else "in"
    return f"{prefix} {rel} ({wd}, {due.strftime('%d/%m %H:%M')})"
