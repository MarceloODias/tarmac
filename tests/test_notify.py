"""Notification on entering NEEDS YOU (SPEC §7.3).

The thing worth testing here is not "does it post an alert" — it is the promise
§7 gave up when it banned notifications and this feature has to pay back: one
alert per episode, never two, never a burst on a cold database, and never a
sound during a meeting.
"""

import json

from tarmac import db as dbm
from tarmac import notify
from tarmac.collect import TargetResult, apply_result, collect
from tarmac.config import Config, SessionClassRule, Settings, Target
from tarmac.derive import badge, build_view
from tarmac.model import parse_agents_json


def make_conn(tmp_path):
    return dbm.connect(tmp_path / "test.db")


def make_target(**kw) -> Target:
    defaults = dict(id="t1", label="T1", mine=True, transport="local")
    defaults.update(kw)
    return Target(**defaults)


def sessions(*entries):
    return parse_agents_json(json.dumps(list(entries)))


def one(state, sid="s1", **extra):
    entry = {"id": sid, "sessionId": f"{sid}-uuid", "kind": "background",
             "state": state, "cwd": "/p/a", "name": "refactor", "startedAt": 1}
    entry.update(extra)
    return entry


# ---------- the claim: exactly once per episode ----------

def test_entering_blocked_notifies_once(tmp_path):
    conn = make_conn(tmp_path)
    t = make_target()
    # warm the target up first: a cold one is deliberately silent (below)
    assert apply_result(conn, TargetResult(t, sessions(one("working"))), notify=True) == []

    events = apply_result(conn, TargetResult(t, sessions(one("blocked"))), notify=True)
    assert [e.session_id for e in events] == ["s1"]
    assert events[0].name == "refactor"

    # still blocked on the next two cycles: the alert already happened
    for _ in range(2):
        assert apply_result(conn, TargetResult(t, sessions(one("blocked"))),
                            notify=True) == []


def test_blocked_again_after_answering_is_a_new_episode(tmp_path):
    conn = make_conn(tmp_path)
    t = make_target()
    apply_result(conn, TargetResult(t, sessions(one("working"))), notify=True)
    assert len(apply_result(conn, TargetResult(t, sessions(one("blocked"))), notify=True)) == 1
    apply_result(conn, TargetResult(t, sessions(one("working"))), notify=True)
    assert len(apply_result(conn, TargetResult(t, sessions(one("blocked"))), notify=True)) == 1
    assert conn.execute("SELECT COUNT(*) c FROM notifications").fetchone()["c"] == 2


def test_second_collector_racing_on_the_same_transition_sends_nothing(tmp_path):
    """Two renderers collect at once: the claim, not the send, decides."""
    conn = make_conn(tmp_path)
    t = make_target()
    apply_result(conn, TargetResult(t, sessions(one("working"))), notify=True)
    blocked = TargetResult(t, sessions(one("blocked")))
    first = apply_result(conn, blocked, notify=True)
    # the loser reaches the same transition row and loses the INSERT
    tid = conn.execute("SELECT transition_id FROM notifications").fetchone()["transition_id"]
    assert dbm.claim_notification(conn, tid, t.id, "s1") is False
    assert len(first) == 1


def test_cold_target_does_not_burst(tmp_path):
    """A fresh DB (or a target just added) holds a backlog, not news."""
    conn = make_conn(tmp_path)
    t = make_target()
    events = apply_result(conn, TargetResult(t, sessions(
        one("blocked", "a"), one("blocked", "b"), one("blocked", "c"))), notify=True)
    assert events == []
    # and the sessions that arrive blocked LATER, on a warm target, do notify
    events = apply_result(conn, TargetResult(t, sessions(
        one("blocked", "a"), one("blocked", "b"), one("blocked", "c"),
        one("blocked", "d"))), notify=True)
    assert [e.session_id for e in events] == ["d"]


def test_notify_off_claims_nothing(tmp_path):
    conn = make_conn(tmp_path)
    t = make_target()
    apply_result(conn, TargetResult(t, sessions(one("working"))))
    assert apply_result(conn, TargetResult(t, sessions(one("blocked")))) == []
    assert conn.execute("SELECT COUNT(*) c FROM notifications").fetchone()["c"] == 0


# ---------- who is worth waking you up for ----------

def test_service_sessions_never_notify(tmp_path):
    conn = make_conn(tmp_path)
    t = make_target(session_classes=[
        SessionClassRule(class_="service", label="chatops", match_cwd="/svc/**")])
    apply_result(conn, TargetResult(t, sessions(one("working", "s1", cwd="/svc/bot"))),
                 notify=True)
    events = apply_result(conn, TargetResult(t, sessions(one("blocked", "s1", cwd="/svc/bot"))),
                          notify=True)
    assert events == []


def test_someone_elses_target_never_notifies(tmp_path):
    conn = make_conn(tmp_path)
    t = make_target(mine=False)
    apply_result(conn, TargetResult(t, sessions(one("working"))), notify=True)
    assert apply_result(conn, TargetResult(t, sessions(one("blocked"))), notify=True) == []


# ---------- the toggle ----------

def test_toggle_round_trip(tmp_path):
    conn = make_conn(tmp_path)
    assert notify.is_muted(conn) is False
    assert notify.toggle(conn) is True
    assert notify.is_muted(conn) is True
    assert notify.toggle(conn) is False
    assert notify.is_muted(conn) is False


def test_timed_mute_expires_on_its_own(tmp_path):
    """The meeting case: you cannot forget to switch it back on."""
    conn = make_conn(tmp_path)
    now = dbm.now_ms()
    notify.mute(conn, now + 60_000)
    assert notify.is_muted(conn, now) is True
    assert notify.is_muted(conn, now + 61_000) is False
    # the reader stays pure; the collect cycle is what tidies the dead key up
    assert notify.mute_until(conn) is not None
    notify.collect_expired(conn, now + 61_000)
    assert notify.mute_until(conn) is None


def test_corrupt_mute_value_fails_loud_not_silent(tmp_path):
    conn = make_conn(tmp_path)
    dbm.kv_set(conn, notify.MUTE_KEY, "not-a-timestamp")
    conn.commit()
    assert notify.is_muted(conn) is False


def test_muted_cycle_sends_nothing(tmp_path, monkeypatch):
    """The whole point: no alert, and no claim burned while silent."""
    conn = make_conn(tmp_path)
    t = make_target()
    config = Config(targets=[t], settings=Settings())
    monkeypatch.setattr("tarmac.collect.collect_target",
                        lambda target: TargetResult(t, sessions(one("working"))))
    collect(config, conn)
    notify.mute(conn)
    monkeypatch.setattr("tarmac.collect.collect_target",
                        lambda target: TargetResult(t, sessions(one("blocked"))))
    sent = []
    monkeypatch.setattr(notify, "send", lambda *a, **kw: sent.append(a) or True)
    collect(config, conn)
    assert sent == []
    assert conn.execute("SELECT COUNT(*) c FROM notifications").fetchone()["c"] == 0


def test_settings_notify_false_is_a_hard_off(tmp_path, monkeypatch):
    conn = make_conn(tmp_path)
    t = make_target()
    config = Config(targets=[t], settings=Settings(notify=False))
    monkeypatch.setattr("tarmac.collect.collect_target",
                        lambda target: TargetResult(t, sessions(one("working"))))
    collect(config, conn)
    monkeypatch.setattr("tarmac.collect.collect_target",
                        lambda target: TargetResult(t, sessions(one("blocked"))))
    sent = []
    monkeypatch.setattr(notify, "send", lambda *a, **kw: sent.append(a) or True)
    collect(config, conn)
    assert sent == []


def test_expired_mute_is_swept_by_the_next_cycle(tmp_path, monkeypatch):
    conn = make_conn(tmp_path)
    t = make_target()
    config = Config(targets=[t], settings=Settings())
    monkeypatch.setattr("tarmac.collect.collect_target",
                        lambda target: TargetResult(t, sessions(one("working"))))
    notify.mute(conn, dbm.now_ms() - 1)
    collect(config, conn)
    assert notify.mute_until(conn) is None


def test_rendering_never_writes(tmp_path):
    """build_view is a reader: an expired mute must not make it commit."""
    conn = make_conn(tmp_path)
    t = make_target()
    config = Config(targets=[t], settings=Settings())
    apply_result(conn, TargetResult(t, sessions(one("working"))))
    conn.commit()
    notify.mute(conn, dbm.now_ms() - 1)
    writes = []
    conn.set_trace_callback(
        lambda sql: writes.append(sql)
        if sql.strip().split()[0].upper() in ("INSERT", "UPDATE", "DELETE") else None)
    build_view(config, conn)
    conn.set_trace_callback(None)
    assert writes == []


def test_muted_panel_says_so(tmp_path):
    conn = make_conn(tmp_path)
    t = make_target()
    config = Config(targets=[t], settings=Settings())
    apply_result(conn, TargetResult(t, sessions(one("working"))))
    assert "🔕" not in badge(build_view(config, conn))[0]
    notify.mute(conn)
    view = build_view(config, conn)
    assert view.notify_muted is True
    assert "🔕" in badge(view)[0]


def test_status_line_states():
    assert notify.status_line(False, False, None, "en").startswith("Notifications: off")
    if notify.available():
        assert notify.status_line(True, False, None, "en") == "Notifications: on"
        assert notify.status_line(True, True, None, "en") == "Notifications: off"
        assert "until" in notify.status_line(True, True, dbm.now_ms(), "en")


# ---------- delivery, at the process boundary ----------

def _fake_osascript(tmp_path, monkeypatch, exit_code=0):
    """Stand in for /usr/bin/osascript at the process boundary.

    The constant is patched rather than PATH: production calls osascript by
    absolute path (a bare name is what launchd's minimal environment breaks),
    so a fake on PATH would never be reached.
    """
    fake = tmp_path / "fake-osascript"
    # printf and not echo: bash's echo eats backslashes, and one of these tests
    # exists precisely to prove the AppleScript escaping survives to the process
    fake.write_text(
        f'#!/bin/bash\nprintf \'%s\\n\' "$@" >> "{tmp_path}/osascript.log"\n'
        f'exit {exit_code}\n')
    fake.chmod(0o755)
    monkeypatch.setattr(notify, "OSASCRIPT", str(fake))
    monkeypatch.setattr(notify, "available", lambda: True)
    return tmp_path / "osascript.log"


def test_send_builds_applescript_with_sound(tmp_path, monkeypatch):
    log = _fake_osascript(tmp_path, monkeypatch)
    assert notify.send("Needs you", "input needed", subtitle="refactor · Mac",
                       sound="Ping") is True
    script = log.read_text()
    assert 'display notification "input needed"' in script
    assert 'with title "Needs you"' in script
    assert 'subtitle "refactor · Mac"' in script
    assert 'sound name "Ping"' in script


def test_empty_sound_posts_a_silent_notification(tmp_path, monkeypatch):
    log = _fake_osascript(tmp_path, monkeypatch)
    notify.send("t", "m", sound="")
    assert "sound name" not in log.read_text()


def test_quotes_in_a_session_name_do_not_break_the_script(tmp_path, monkeypatch):
    log = _fake_osascript(tmp_path, monkeypatch)
    notify.send('say "hi"', 'back\\slash')
    text = log.read_text()
    assert '\\"hi\\"' in text and "\\\\slash" in text


def test_failed_osascript_is_swallowed(tmp_path, monkeypatch):
    _fake_osascript(tmp_path, monkeypatch, exit_code=1)
    assert notify.send("t", "m") is False


def test_dispatch_groups_a_flood_into_one(tmp_path, monkeypatch):
    log = _fake_osascript(tmp_path, monkeypatch)
    events = [notify.BlockedEvent("t1", "Mac", f"s{i}", f"sess{i}")
              for i in range(notify.GROUP_ABOVE + 1)]
    assert notify.dispatch(events, "en") == 1
    text = log.read_text()
    assert text.count("display notification") == 1
    assert f"{len(events)} sessions need you" in text


def test_dispatch_sends_one_each_below_the_threshold(tmp_path, monkeypatch):
    log = _fake_osascript(tmp_path, monkeypatch)
    events = [notify.BlockedEvent("t1", "Mac", "s1", "alpha", "input needed", "/p/a"),
              notify.BlockedEvent("t1", "EC2", "s2", "beta", "permission prompt")]
    assert notify.dispatch(events, "en") == 2
    text = log.read_text()
    assert text.count("display notification") == 2
    assert "alpha · Mac" in text and "input needed — /p/a" in text


def test_collect_posts_the_alert_end_to_end(tmp_path, monkeypatch):
    """The real cycle, with only osascript faked at the process boundary."""
    log = _fake_osascript(tmp_path, monkeypatch)
    conn = make_conn(tmp_path)
    t = make_target(label="Mac")
    config = Config(targets=[t], settings=Settings(locale="en", notify_sound="Glass"))
    monkeypatch.setattr("tarmac.collect.collect_target",
                        lambda target: TargetResult(t, sessions(one("working"))))
    collect(config, conn)
    assert not log.exists()

    monkeypatch.setattr("tarmac.collect.collect_target",
                        lambda target: TargetResult(
                            t, sessions(one("blocked", waitingFor="input needed"))))
    collect(config, conn)
    text = log.read_text()
    assert 'with title "Needs you"' in text
    assert "refactor · Mac" in text
    assert 'sound name "Glass"' in text

    collect(config, conn)  # still blocked: no second alert
    assert text.count("display notification") == log.read_text().count("display notification")
