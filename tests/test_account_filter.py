"""Omitting one account from the panel (`A` in the TUI) — tarmac/accounts.py.

Marcelo runs two Claude accounts on the same machines: the work one (the
default `CLAUDE_CONFIG_DIR`) and a personal one, which the panel merges into a
single list on purpose. During work the personal sessions are noise in the one
list whose job is to say what is waiting on him, so exactly one account can be
left out.

What is worth testing is not "does the row disappear" — it is the promises that
make a filter safe to trust:

* the badge stops COUNTING what the list stops showing (a filtered panel that
  reports `⏸ 2` and lists nothing is a lying panel);
* a filtered panel never reads as a quiet one (`⊘ <account>`, like `🔕`);
* the default account, whose name in the config is the empty string, is a real
  choice and not the same state as "omit nothing";
* the choice survives the panel being closed;
* a hidden row is still ADDRESSABLE — `tarmac open` must reach it;
* an omitted account does not push macOS alerts you cannot act on.
"""

import json

import pytest

from tarmac import accounts
from tarmac import db as dbm
from tarmac import notify
from tarmac.collect import TargetResult, apply_result, collect
from tarmac.config import Config, SessionClassRule, Settings, Target
from tarmac.derive import account_width, badge, build_view
from tarmac.model import parse_agents_json
from tarmac.render.tui_app import TarmacApp

WORK, PERSONAL = "", "Personal"


def target(**kw) -> Target:
    base = dict(id="t1", label="T1", mine=True, transport="local")
    base.update(kw)
    return Target(**base)


def config_for(*targets) -> Config:
    return Config(targets=list(targets),
                  settings=Settings(locale="en", stale_after_s=10_000))


def two_accounts():
    """The real shape of Marcelo's config: one machine, two config dirs."""
    return (target(id="mac", label="Mac", config_dir="~/.claude"),
            target(id="mac-personal", label="Mac", account=PERSONAL,
                   config_dir="~/.claude-personal"))


def seed(conn, *pairs, state="blocked"):
    """(target, session name) → one session each, in `state`."""
    for i, (t, name) in enumerate(pairs, start=1):
        raw = [{"id": f"sess{i:04d}", "kind": "background", "state": state,
                "cwd": "/x", "startedAt": i, "name": name}]
        apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    dbm.kv_set(conn, "last_collect_at", str(dbm.now_ms()))
    conn.commit()


@pytest.fixture
def two(tmp_path):
    work, personal = two_accounts()
    conn = dbm.connect(tmp_path / "t.db")
    seed(conn, (work, "trabalho"), (personal, "pessoal"))
    return config_for(work, personal), conn


# ---------- what the list shows, and what the badge counts ----------

def test_omitting_an_account_drops_its_rows(two):
    config, conn = two
    assert {r.display_name for r in build_view(config, conn).blocked} == \
        {"trabalho", "pessoal"}

    accounts.set_omitted(conn, PERSONAL)
    view = build_view(config, conn)
    assert [r.display_name for r in view.blocked] == ["trabalho"]

    accounts.set_omitted(conn, WORK)
    view = build_view(config, conn)
    assert [r.display_name for r in view.blocked] == ["pessoal"]


def test_the_badge_stops_counting_what_the_list_stops_showing(two):
    """The failure this guards: `⏸ 2` over a list with one row in it."""
    config, conn = two
    assert badge(build_view(config, conn))[0].startswith("⏸ 2")
    accounts.set_omitted(conn, PERSONAL)
    assert badge(build_view(config, conn))[0].startswith("⏸ 1")


def test_a_filtered_panel_never_reads_as_a_quiet_one(two):
    """Same rule the mute pays (🔕): silence and emptiness must not look alike."""
    config, conn = two
    accounts.set_omitted(conn, PERSONAL)
    text, severity = badge(build_view(config, conn))
    assert "⊘ Personal" in text
    # omitting is a choice, not a fault: it must not colour the badge
    assert severity == badge(build_view(config_for(*[
        t for t in config.targets if t.account != PERSONAL]), conn))[1]


def test_omitting_the_default_account_says_so_in_the_panels_language(two):
    config, conn = two
    accounts.set_omitted(conn, WORK)
    view = build_view(config, conn)
    assert view.omitted_account == WORK
    assert "⊘ Professional" in badge(view)[0]

    config.settings.locale = "pt"
    assert "⊘ Profissional" in badge(build_view(config, conn))[0]


def test_the_target_health_line_of_an_omitted_account_goes_too(two):
    config, conn = two
    assert {t.target_id for t in build_view(config, conn).targets} == \
        {"mac", "mac-personal"}
    accounts.set_omitted(conn, PERSONAL)
    assert {t.target_id for t in build_view(config, conn).targets} == {"mac"}


def test_the_account_column_disappears_with_the_account(two):
    """A side effect worth keeping: omitting the only named account gives the
    name column its width back (account_width goes to 0)."""
    config, conn = two
    assert account_width(build_view(config, conn).blocked) == len(PERSONAL)
    accounts.set_omitted(conn, PERSONAL)
    assert account_width(build_view(config, conn).blocked) == 0


def test_services_of_the_omitted_account_disappear(tmp_path):
    work, personal = two_accounts()
    rule = SessionClassRule(class_="service", label="slack", match_cwd="/svc/**")
    work.session_classes = [rule]
    personal.session_classes = [rule]
    conn = dbm.connect(tmp_path / "t.db")
    for i, t in enumerate((work, personal), start=1):
        raw = [{"id": f"svc{i:05d}", "kind": "background", "state": "working",
                "cwd": "/svc/a", "startedAt": i, "name": "agent"}]
        apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    conn.commit()
    config = config_for(work, personal)
    assert len(build_view(config, conn).services) == 2
    accounts.set_omitted(conn, PERSONAL)
    services = build_view(config, conn).services
    assert [s.target_id for s in services] == ["mac"]


def test_a_task_tied_to_the_omitted_account_disappears_and_a_loose_one_stays(two):
    """A task is intent, but once it names a folder it names an account too.
    Before it is resolved it belongs to no account, so it always shows."""
    from tarmac.tasks import add_task, set_task_folder
    config, conn = two
    tied = add_task(conn, "personal errand")
    set_task_folder(conn, tied, "mac-personal", "/home/me/side")
    add_task(conn, "no folder yet")
    conn.commit()

    accounts.set_omitted(conn, PERSONAL)
    names = {r.display_name for r in build_view(config, conn).scheduled}
    assert "personal errand" not in names
    assert "no folder yet" in names


# ---------- the stored choice ----------

def test_the_default_account_is_a_different_state_from_omitting_nothing(two):
    """"" is a real value here. `kv_get` returning None only for an absent row
    is what makes the default account selectable at all."""
    config, conn = two
    assert accounts.omitted(conn) is None
    accounts.set_omitted(conn, WORK)
    assert accounts.omitted(conn) == WORK
    assert accounts.omitted(conn) is not None
    accounts.set_omitted(conn, None)
    assert accounts.omitted(conn) is None


def test_the_choice_survives_the_panel_being_closed(tmp_path):
    work, personal = two_accounts()
    config = config_for(work, personal)
    conn = dbm.connect(tmp_path / "t.db")
    seed(conn, (work, "trabalho"), (personal, "pessoal"))
    accounts.set_omitted(conn, PERSONAL)
    conn.close()

    reopened = dbm.connect(tmp_path / "t.db")   # a new panel, a new connection
    assert accounts.effective(reopened, config) == PERSONAL
    assert [r.display_name for r in build_view(config, reopened).blocked] == \
        ["trabalho"]


def test_the_cycle_hides_the_side_project_first_then_the_work_then_nothing(two):
    """Order matters: the first press must not hide the work the panel is for."""
    config, conn = two
    assert accounts.cycle(conn, config) == PERSONAL
    assert accounts.cycle(conn, config) == WORK
    assert accounts.cycle(conn, config) is None
    assert accounts.cycle(conn, config) == PERSONAL


def test_an_account_that_left_the_config_filters_nothing(two):
    """Rename or disable the personal target and the panel must not go on
    claiming to hide it — a badge marker with no effect is a lie."""
    config, conn = two
    accounts.set_omitted(conn, "Freelance")
    assert accounts.effective(conn, config) is None
    view = build_view(config, conn)
    assert view.omitted_account is None
    assert len(view.blocked) == 2
    assert "⊘" not in badge(view)[0]


def test_a_single_account_setup_has_nothing_to_omit(tmp_path):
    work, _ = two_accounts()
    config = config_for(work)
    conn = dbm.connect(tmp_path / "t.db")
    assert accounts.names(config) == [WORK]
    assert accounts.can_omit(config) is False


def test_a_disabled_or_someone_elses_target_is_not_an_account_to_omit(tmp_path):
    work, personal = two_accounts()
    personal.enabled = False
    theirs = target(id="theirs", label="Bob", account="Bob", mine=False)
    assert accounts.names(config_for(work, personal, theirs)) == [WORK]


def test_resolve_accepts_the_name_the_default_and_the_shown_word(two):
    config, _ = two
    assert accounts.resolve(config, "personal") == PERSONAL   # case-insensitive
    assert accounts.resolve(config, "default") == WORK
    assert accounts.resolve(config, "Professional") == WORK
    assert accounts.resolve(config, "all") is None
    with pytest.raises(ValueError) as e:
        accounts.resolve(config, "Freelance")
    assert "Personal" in str(e.value)   # the error lists what does exist


# ---------- a hidden row is still addressable ----------

def test_an_omitted_session_can_still_be_acted_on(two):
    """`A` hides an account from the LIST. A command that already names its
    target and session — a SwiftBar click, `tarmac open` — must still find it,
    with its intent layer attached."""
    from tarmac.cli import _find_row
    config, conn = two
    dbm.upsert_meta(conn, "mac-personal", "sess0002", next_step="finish this")
    conn.commit()
    accounts.set_omitted(conn, PERSONAL)
    row = _find_row(config, conn, "mac-personal", "sess0002")
    assert row.display_name == "pessoal"
    assert row.next_step == "finish this"   # not the raw-DB fallback row


# ---------- alerts follow the filter ----------

def test_an_omitted_account_does_not_alert(tmp_path, monkeypatch):
    """A macOS banner about a row the panel is hiding is an alert you cannot
    act on — and, like a mute, it must not burn the claim either: the session
    is still blocked when the account comes back."""
    work, personal = two_accounts()
    config = config_for(work, personal)
    conn = dbm.connect(tmp_path / "t.db")

    def listing(target, state):
        raw = [{"id": "sess0001" if target.id == "mac" else "sess0002",
                "kind": "background", "state": state, "cwd": "/x",
                "startedAt": 1, "name": target.id}]
        return TargetResult(target, parse_agents_json(json.dumps(raw)))

    monkeypatch.setattr("tarmac.collect.collect_target",
                        lambda t: listing(t, "working"))
    collect(config, conn, force=True)          # warm both: a cold target is silent

    accounts.set_omitted(conn, PERSONAL)
    monkeypatch.setattr("tarmac.collect.collect_target",
                        lambda t: listing(t, "blocked"))
    sent = []
    monkeypatch.setattr(notify, "send", lambda *a, **kw: sent.append(a) or True)
    collect(config, conn, force=True)

    claimed = [r["target_id"] for r in conn.execute(
        "SELECT target_id FROM notifications")]
    assert claimed == ["mac"], "the omitted account alerted (or burned a claim)"
    assert len(sent) == 1


# ---------- the key, through the real app ----------

def render_lines(renderable, width: int = 120) -> list[str]:
    import re

    from rich.console import Console
    console = Console(width=width, no_color=True)
    with console.capture() as cap:
        console.print(renderable)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", cap.get())
    return [ln.rstrip() for ln in plain.splitlines()]


def app_lines(app, width: int = 160) -> list[str]:
    from textual.widgets import OptionList
    ol = app.query_one("#sessions", OptionList)
    out: list[str] = []
    for i in range(ol.option_count):
        option = ol.get_option_at_index(i)
        if option is not None:
            out += render_lines(option.prompt, width)
    return out


def badge_text(app) -> str:
    from textual.widgets import Static
    return "\n".join(render_lines(app.query_one("#badge", Static).render(), 200))


@pytest.fixture
def app_config(tmp_path, monkeypatch):
    """Two accounts in the DB the app itself opens (TARMAC_HOME is the tmp one)."""
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    work, personal = two_accounts()
    seed(dbm.connect(), (work, "trabalho"), (personal, "pessoal"))
    return config_for(work, personal)


async def test_A_omits_an_account_and_brings_it_back(app_config):
    """The whole feature end to end, through the keyboard: `A` hides the
    personal account's row, the badge says the list is filtered, and pressing
    it round the cycle brings the row back."""
    app = TarmacApp(app_config)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        assert any("pessoal" in ln for ln in app_lines(app))

        await pilot.press("A")
        await pilot.pause()
        lines = app_lines(app)
        assert not any("pessoal" in ln for ln in lines), \
            "`A` did not drop the omitted account's row"
        assert any("trabalho" in ln for ln in lines), "it dropped the wrong rows"
        assert "⊘ Personal" in badge_text(app)

        await pilot.press("A")          # omit the work account instead
        await pilot.pause()
        assert any("pessoal" in ln for ln in app_lines(app))
        assert "⊘ Professional" in badge_text(app)

        await pilot.press("A")          # back to everything
        await pilot.pause()
        assert len(app_lines(app)) > 0 and "⊘" not in badge_text(app)
        rendered = "\n".join(app_lines(app))
        assert "pessoal" in rendered and "trabalho" in rendered


async def test_A_survives_a_refresh_and_is_written_down(app_config):
    """The panel stays open for days and refreshes every 60s: a filter that a
    refresh silently undid would be a filter nobody trusts."""
    app = TarmacApp(app_config)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        app.refresh_data()
        await pilot.pause()
        assert not any("pessoal" in ln for ln in app_lines(app))
        assert accounts.omitted(dbm.connect()) == "Personal"


async def test_A_with_a_single_account_says_so_instead_of_doing_nothing(tmp_path,
                                                                       monkeypatch):
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    work, _ = two_accounts()
    seed(dbm.connect(), (work, "trabalho"))
    app = TarmacApp(config_for(work))
    warned = []
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "notify",
                            lambda msg, **kw: warned.append((msg, kw)))
        await pilot.press("A")
        await pilot.pause()
        assert any("nothing to omit" in m for m, _ in warned)
        assert any("trabalho" in ln for ln in app_lines(app))
        assert accounts.omitted(dbm.connect()) is None


def test_the_key_is_labelled_in_the_panels_language():
    """The footer is where a key is discovered — an untranslated `bind_account`
    there is the bug `_localize_bindings` exists to prevent."""
    for locale, word in (("en", "account"), ("pt", "conta")):
        app = TarmacApp(Config(targets=[target()], settings=Settings(locale=locale)))
        assert app._bindings.key_to_bindings["A"][0].description == word


async def test_A_did_not_steal_lowercase_a_from_defer(app_config):
    """`a` is defer and `A` is the account filter. Textual treats them as two
    keys, and this is the test that keeps it that way — the panel has four
    other case-paired bindings (`c/C`, `r/R`, `n/N`, `s/S`)."""
    from tarmac.render.tui_app import TextPrompt
    app = TarmacApp(app_config)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        assert not isinstance(app.screen, TextPrompt), "`A` opened defer"
        assert accounts.omitted(dbm.connect()) == "Personal"

        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, TextPrompt), "`a` stopped deferring"
