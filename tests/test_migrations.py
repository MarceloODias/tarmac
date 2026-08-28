"""The panel must not freeze, and must not lie when it does.

One database built by a draft of the notification feature had a `notifications`
table keyed on blocked_at. CREATE TABLE IF NOT EXISTS saw the name and did
nothing, every collect afterwards died on `no column named transition_id`, and
the panel kept drawing its last good frame for nine hours — calling a day-old
list current, with every target marked ok.

Three defects, one story: schema drift is never repaired (migrations), a
failure that raises leaves no trace (isolation), and old data is indistinguish-
able from fresh data (staleness). Each gets its own section below.
"""

from __future__ import annotations

import sqlite3

import pytest

from tarmac import db as dbm
from tarmac.collect import TargetResult, collect
from tarmac.config import Config, Settings, Target
from tarmac.derive import badge, build_view
from tarmac.model import Session

# the drafted table, verbatim from the database that froze
OLD_NOTIFICATIONS = """
CREATE TABLE notifications (
  target_id  TEXT NOT NULL,
  session_id TEXT NOT NULL,
  blocked_at INTEGER NOT NULL,
  at         INTEGER,
  PRIMARY KEY (target_id, session_id, blocked_at)
);
"""


def make_target(**kw) -> Target:
    defaults = dict(id="t1", label="T1", mine=True, transport="local")
    defaults.update(kw)
    return Target(**defaults)


def make_config(*targets, **settings) -> Config:
    settings.setdefault("notify", False)
    return Config(targets=list(targets), settings=Settings(**settings))


def make_session(session_id="s1", status="busy", **kw) -> Session:
    return Session(
        session_id=session_id, name=kw.get("name", session_id), kind="interactive",
        cwd="/tmp/p", status=status, state=kw.get("state"),
        waiting_for=kw.get("waiting_for"), pid=kw.get("pid", 123),
        started_at=1, short_id=session_id[:8], uuid=session_id, raw_json="{}",
    )


def drifted_db(path) -> None:
    """A database exactly as the freeze left it: current tables, wrong claim
    table, never stamped with a version."""
    conn = dbm.connect(path)
    conn.execute("DROP TABLE notifications")
    conn.executescript(OLD_NOTIFICATIONS)
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()


# --------------------------------------------------------------- migrations

def test_drifted_notifications_table_is_repaired(tmp_path):
    path = tmp_path / "old.db"
    drifted_db(path)
    with sqlite3.connect(path) as raw:
        cols = {r[1] for r in raw.execute("PRAGMA table_info(notifications)")}
    assert "transition_id" not in cols, "fixture must start drifted"

    conn = dbm.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(notifications)")}
    assert "transition_id" in cols
    assert "blocked_at" not in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbm.SCHEMA_VERSION


def test_drifted_database_can_claim_again(tmp_path):
    """The symptom, not just the schema: this is the call that used to raise."""
    path = tmp_path / "old.db"
    drifted_db(path)
    conn = dbm.connect(path)
    assert dbm.claim_notification(conn, 1, "t1", "s1") is True
    assert dbm.claim_notification(conn, 1, "t1", "s1") is False  # still idempotent


def test_migration_keeps_the_claims_of_a_correct_table(tmp_path):
    """Dropping a good claim table would re-announce sessions already announced,
    and a duplicate alert is the failure the table exists to prevent."""
    path = tmp_path / "good.db"
    conn = dbm.connect(path)
    assert dbm.claim_notification(conn, 7, "t1", "s1") is True
    conn.execute("PRAGMA user_version = 0")  # correct shape, never stamped
    conn.commit()
    conn.close()

    conn = dbm.connect(path)
    assert dbm.claim_notification(conn, 7, "t1", "s1") is False, "claim was lost"


def test_fresh_database_is_stamped_and_skips_the_steps(tmp_path):
    conn = dbm.connect(tmp_path / "fresh.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbm.SCHEMA_VERSION


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "old.db"
    drifted_db(path)
    dbm.connect(path).close()
    conn = dbm.connect(path)  # second open must be a no-op, not a second repair
    assert dbm.migrate(conn) == dbm.SCHEMA_VERSION
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(notifications)")}
    assert "transition_id" in cols


def test_a_fresh_database_never_runs_a_step(tmp_path, monkeypatch):
    """SCHEMA already builds a new database AT SCHEMA_VERSION. Running the
    repair steps over it would fix nothing and could undo what it just built."""
    ran = []
    monkeypatch.setattr(dbm, "MIGRATIONS",
                        [(1, lambda conn: ran.append("step"))])
    dbm.connect(tmp_path / "fresh.db")
    assert ran == []


def test_every_migration_has_a_version_within_schema_version():
    versions = [v for v, _ in dbm.MIGRATIONS]
    assert versions == sorted(versions), "steps run in order, so declare them in order"
    assert max(versions, default=0) <= dbm.SCHEMA_VERSION, "bump SCHEMA_VERSION"


# --------------------------------------------------------------- isolation

def test_one_target_raising_does_not_stop_the_cycle(tmp_path, monkeypatch):
    """The freeze itself: apply_result raised, the transaction rolled back, the
    process died, and nothing was ever recorded about it."""
    import tarmac.collect as collect_mod

    good, bad = make_target(id="good"), make_target(id="bad")
    config = make_config(good, bad)
    conn = dbm.connect(tmp_path / "t.db")

    monkeypatch.setattr(collect_mod, "collect_target",
                        lambda t: TargetResult(t, [make_session(f"{t.id}-s1")]))
    real_apply = collect_mod.apply_result

    def flaky(conn_, result, notify=False):
        if result.target.id == "bad":
            # a partial write, then the failure — the rollback must undo it
            conn_.execute(
                "INSERT INTO sessions (target_id, session_id) VALUES (?, ?)",
                ("bad", "half-written"),
            )
            raise sqlite3.OperationalError("table notifications has no column named x")
        return real_apply(conn_, result, notify=notify)

    monkeypatch.setattr(collect_mod, "apply_result", flaky)
    collect(config, conn, force=True)  # must not raise

    ids = {r["session_id"] for r in conn.execute(
        "SELECT session_id FROM sessions WHERE target_id = 'good'")}
    assert ids == {"good-s1"}, "the healthy target still mirrored"
    assert conn.execute(
        "SELECT COUNT(*) c FROM sessions WHERE target_id = 'bad'"
    ).fetchone()["c"] == 0, "the failing target's partial write was rolled back"
    assert dbm.kv_get(conn, "last_collect_at"), "the cycle still finished"

    st = conn.execute(
        "SELECT * FROM target_status WHERE target_id = 'bad'").fetchone()
    assert st["error_kind"] == "error", "an internal failure is still a failure"
    assert "OperationalError" in st["last_error"]


def test_collect_target_never_raises(monkeypatch):
    import tarmac.collect as collect_mod

    def boom(*a, **kw):
        raise RuntimeError("surprise")

    monkeypatch.setattr(collect_mod, "build_command", boom)
    result = collect_mod.collect_target(make_target())
    assert result.sessions is None
    assert result.error_kind == "error"
    assert "RuntimeError" in result.error


def test_a_failing_side_step_is_recorded_not_swallowed(tmp_path, monkeypatch):
    import tarmac.collect as collect_mod

    t = make_target()
    conn = dbm.connect(tmp_path / "t.db")
    monkeypatch.setattr(collect_mod, "collect_target",
                        lambda tt: TargetResult(tt, [make_session()]))
    monkeypatch.setattr(dbm, "prune_service_sessions",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nope")))

    collect(make_config(t), conn, force=True)

    assert conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 1
    assert dbm.kv_get(conn, "last_collect_at")
    assert "RuntimeError" in (dbm.kv_get(conn, "last_error:prune") or "")


# --------------------------------------------------------------- staleness

def _target_status(conn, target_id: str, age_s: int, error_kind=None) -> None:
    conn.execute(
        "INSERT INTO target_status (target_id, last_ok_at, error_kind, fail_count) "
        "VALUES (?, ?, ?, 0)",
        (target_id, dbm.now_ms() - age_s * 1000, error_kind),
    )


def test_old_data_without_an_error_is_stale(tmp_path):
    """No failure was ever recorded — which is exactly the frozen-panel case."""
    t = make_target()
    config = make_config(t, stale_data_after_s=300)
    conn = dbm.connect(tmp_path / "t.db")
    _target_status(conn, "t1", age_s=9 * 3600)
    conn.execute(
        "INSERT INTO sessions (target_id, session_id, eff_state, class, gone) "
        "VALUES ('t1', 's1', 'working', 'owned', 0)"
    )

    view = build_view(config, conn)
    assert [tl.state for tl in view.targets] == ["stale"]
    assert view.has_stale
    assert view.working[0].stale, "the row itself is a memory, and says so"

    text, severity = badge(view)
    assert "⏳" in text, f"badge hides the freeze: {text!r}"
    assert severity == "warn"


def test_fresh_data_is_not_stale(tmp_path):
    t = make_target()
    config = make_config(t, stale_data_after_s=300)
    conn = dbm.connect(tmp_path / "t.db")
    _target_status(conn, "t1", age_s=30)
    conn.execute(
        "INSERT INTO sessions (target_id, session_id, eff_state, class, gone) "
        "VALUES ('t1', 's1', 'working', 'owned', 0)"
    )

    view = build_view(config, conn)
    assert [tl.state for tl in view.targets] == ["ok"]
    assert not view.has_stale
    assert not view.working[0].stale
    assert "⏳" not in badge(view)[0]


def test_a_target_never_read_is_not_stale(tmp_path):
    """Nothing has been read, so there is no old reading to distrust."""
    config = make_config(make_target(), stale_data_after_s=300)
    conn = dbm.connect(tmp_path / "t.db")
    view = build_view(config, conn)
    assert [tl.state for tl in view.targets] == ["ok"]
    assert not view.has_stale


@pytest.mark.parametrize("state", ["✓", "▶"])
def test_quiet_panel_over_old_data_is_never_plain_quiet(tmp_path, state):
    """'nothing is waiting on you' and 'nothing was waiting the last time I
    could look' must not render the same."""
    config = make_config(make_target(), stale_data_after_s=300)
    conn = dbm.connect(tmp_path / "t.db")
    _target_status(conn, "t1", age_s=3600)
    if state == "▶":
        conn.execute(
            "INSERT INTO sessions (target_id, session_id, eff_state, class, gone) "
            "VALUES ('t1', 's1', 'working', 'owned', 0)"
        )
    text, severity = badge(view := build_view(config, conn))
    assert state in text and "⏳" in text, text
    assert severity == "warn"
    assert view.has_stale
