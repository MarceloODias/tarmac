"""Omit one Claude account from the panel (`A` in the TUI).

Two accounts on the same machine are two targets (SPEC §3): same box, same
`claude`, a different `CLAUDE_CONFIG_DIR` and so a different session universe.
The panel merges them on purpose — that merge is the point. But the merge is
wrong for whole stretches of the day: the personal account's sessions are not
work, and during work they are noise in the one list whose job is to say what
is waiting on me.

So: exactly one account can be omitted at a time, chosen by cycling with `A`,
and the choice lives in the DB — the panel stays open for days and a filter I
have to re-apply after every restart is a filter I stop trusting.

Three rules this module exists to keep:

* **The default account is a real value.** It is `account: ""` in the config,
  and "" is what gets stored to omit it. Absent key = omit nothing; that is a
  different state, and `omitted()` does not collapse the two.
* **A filtered panel must never read as a quiet one.** `build_view` puts the
  omission in the View and `badge` renders `⊘ <account>` next to the counts,
  the same way a mute renders 🔕.
* **An omitted account is silent too.** A macOS alert about a row the panel is
  hiding would be an alert you cannot act on: `collect` suppresses the
  notification for the omitted account (DECISIONS #42).
"""

from __future__ import annotations

import sqlite3

from . import db as dbm
from .config import Config
from .strings import tr

KEY = "omit_account"

DEFAULT = ""   # the account with no name in targets.yaml (SPEC §3)


def names(config: Config) -> list[str]:
    """Every account in play, in the order `A` cycles through omitting them.

    Named accounts first, the default one last: the first press should hide the
    side project, not the work the panel exists for.
    """
    seen = {t.account for t in config.enabled_targets() if t.mine}
    named = sorted(a for a in seen if a)
    return named + ([DEFAULT] if DEFAULT in seen else [])


def can_omit(config: Config) -> bool:
    """With a single account there is nothing to omit — and a key that silently
    does nothing is worse than one that says so."""
    return len(names(config)) > 1


def label(account: str | None, locale: str = "en") -> str:
    """What to call an account on screen. The default one has no name of its
    own in the config, so it borrows a word from `strings`."""
    if account is None:
        return tr(locale, "account_all")
    return account or tr(locale, "account_default")


def omitted(conn: sqlite3.Connection) -> str | None:
    """The stored choice: None = omit nothing, "" = omit the default account.

    `kv_get` returns None only when the row is absent, so "" survives the round
    trip as itself — which is the whole reason the default account can be
    omitted at all.
    """
    return dbm.kv_get(conn, KEY)


def effective(conn: sqlite3.Connection, config: Config) -> str | None:
    """The omission actually in force.

    An account that has left the config (target renamed, disabled, removed)
    filters nothing, so it must not linger in the badge either — the panel
    would be claiming to hide something it is not hiding.
    """
    value = omitted(conn)
    return value if value in names(config) else None


def set_omitted(conn: sqlite3.Connection, account: str | None) -> None:
    """Commits: this is a single kv write, and the panel re-renders right after."""
    if account is None:
        conn.execute("DELETE FROM kv WHERE key = ?", (KEY,))
    else:
        dbm.kv_set(conn, KEY, account)
    conn.commit()


def cycle(conn: sqlite3.Connection, config: Config) -> str | None:
    """Advance to the next state and return it: show all → omit each account
    in turn → show all."""
    order: list[str | None] = [None, *names(config)]
    current = effective(conn, config)
    nxt = order[(order.index(current) + 1) % len(order)]
    set_omitted(conn, nxt)
    return nxt


def resolve(config: Config, wanted: str, locale: str = "en") -> str | None:
    """Turn a CLI argument into an account. Raises ValueError with the list.

    Accepts the account name (case-insensitively), `default` for the unnamed
    one, and the localized word the panel shows for it.
    """
    wanted = wanted.strip()
    if wanted.lower() in ("all", "none"):
        return None
    if wanted.lower() in ("default", label(DEFAULT, locale).lower()):
        if DEFAULT not in names(config):
            raise ValueError(f"no default account among: {_listing(config, locale)}")
        return DEFAULT
    for account in names(config):
        if account.lower() == wanted.lower():
            return account
    raise ValueError(f"unknown account {wanted!r} — known: {_listing(config, locale)}")


def _listing(config: Config, locale: str) -> str:
    return ", ".join(label(a, locale) for a in names(config)) or "(none)"
