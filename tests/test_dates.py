"""Every form from SPEC §6.2 must parse deterministically (no claude -p)."""

from datetime import datetime, timedelta

import pytest

from tarmac.dates import DateParseError, human_confirmation, parse_natural

# fixed anchor: Friday 2026-08-07 15:30 local
NOW = datetime(2026, 8, 7, 15, 30).astimezone()


def parse(text: str) -> datetime:
    return parse_natural(text, now=NOW)


def test_durations_exact_from_now():
    assert parse("5h") == NOW + timedelta(hours=5)
    assert parse("30min") == NOW + timedelta(minutes=30)
    assert parse("2d") == NOW + timedelta(days=2)
    assert parse("3 dias") == NOW + timedelta(days=3)


def test_tomorrow():
    assert parse("amanhã") == NOW.replace(
        day=8, hour=9, minute=0, second=0, microsecond=0)
    assert parse("amanhã cedo").hour == 8


def test_weekdays_always_future():
    monday = parse("segunda")
    assert monday.weekday() == 0 and monday > NOW
    assert monday.hour == 9
    assert parse("na segunda") == monday
    assert parse("próxima terça").weekday() == 1
    # said on a Friday, 'sexta' means NEXT Friday, not today
    friday = parse_natural("sexta", now=NOW)
    assert friday.weekday() == 4 and friday.date() > NOW.date()


def test_weekday_with_time():
    d = parse("sexta 14h")
    assert d.weekday() == 4 and d.hour == 14


def test_day_of_month_and_slash_date():
    d = parse("dia 15")
    assert d.day == 15 and d > NOW
    d = parse("15/09")
    assert (d.day, d.month, d.year) == (15, 9, 2026)
    # a past slash date without year rolls to next year
    d = parse("15/01")
    assert d.year == 2027


def test_anchors():
    assert parse("fim do dia").hour == 18
    eow = parse("fim da semana")
    assert eow.weekday() == 4
    nm = parse("próximo mês")
    assert (nm.month, nm.day) == (9, 1)


def test_english_forms():
    assert parse("tomorrow").day == 8
    assert parse("monday").weekday() == 0
    assert parse("end of day").hour == 18


def test_unparseable_raises_never_silent():
    with pytest.raises(DateParseError):
        parse("quando der")
    with pytest.raises(DateParseError):
        parse("")


def test_human_confirmation_shows_absolute():
    due = parse("segunda")
    text = human_confirmation(due, now=NOW, locale="pt")
    assert "(" in text and "seg" in text and "09:00" in text
