"""Regressions found by Marcelo while using the panel.

1. `backend-agent` (a chatops session) showed up as TRABALHANDO: its cwd was
   exactly the dir in match_cwd, and `dir/**` didn't match `dir`.
2. That same row had already vanished from the --json (gone=1) hours earlier
   and kept rendering as live — a vanished blocked session would inflate the
   badge forever, and the badge is load-bearing.
3. `x` (resolve) did nothing on a blocked row: it only applies to overdue
   reminders, but it failed silently instead of saying so.
"""

import json
from dataclasses import replace

import pytest
from textual.widgets import OptionList

from tarmac import db as dbm
from tarmac.collect import TargetResult, apply_result
from tarmac.config import Config, SessionClassRule, Settings, Target
from tarmac.derive import badge, build_view
from tarmac.model import parse_agents_json
from tarmac.render.tui_app import TarmacApp


def conn_for(tmp_path):
    return dbm.connect(tmp_path / "t.db")


def target(**kw):
    base = dict(id="t1", label="T1", mine=True, transport="local")
    base.update(kw)
    return Target(**base)


def config_for(*targets):
    return Config(targets=list(targets), settings=Settings(stale_after_s=10_000))


def test_vanished_session_stops_rendering_as_live(tmp_path):
    conn = conn_for(tmp_path)
    t = target()
    raw = [
        {"id": "work0001", "kind": "background", "state": "working",
         "cwd": "/x", "startedAt": 1, "name": "trabalho"},
        {"id": "blok0001", "kind": "background", "state": "blocked",
         "cwd": "/x", "startedAt": 2, "name": "bloqueada"},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    cfg = config_for(t)
    assert len(build_view(cfg, conn).working) == 1
    assert len(build_view(cfg, conn).blocked) == 1

    apply_result(conn, TargetResult(t, []))  # both vanished from the listing
    view = build_view(cfg, conn)
    assert view.working == [], "sessão sumida continuou em TRABALHANDO"
    assert view.blocked == [], "sessão sumida continuou contando no badge"
    assert badge(view)[0].startswith("✓")


def test_vanished_session_with_a_reminder_still_shows(tmp_path):
    # intent outlives the listing (SPEC §5.2): a reminder must survive
    conn = conn_for(tmp_path)
    t = target()
    raw = [{"id": "work0001", "kind": "background", "state": "working",
            "cwd": "/x", "startedAt": 1, "name": "trabalho"}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    dbm.upsert_meta(conn, "t1", "work0001", due_at=dbm.now_ms() - 1000,
                    due_label="ontem")
    apply_result(conn, TargetResult(t, []))
    assert len(build_view(config_for(t), conn).overdue) == 1


def test_pinned_vanished_session_still_shows(tmp_path):
    conn = conn_for(tmp_path)
    t = target()
    raw = [{"id": "work0001", "kind": "background", "state": "working",
            "cwd": "/x", "startedAt": 1, "name": "trabalho"}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    dbm.upsert_meta(conn, "t1", "work0001", pinned=1)
    apply_result(conn, TargetResult(t, []))
    assert len(build_view(config_for(t), conn).working) == 1


def test_service_glob_matches_the_directory_itself(tmp_path):
    conn = conn_for(tmp_path)
    rule = SessionClassRule(class_="service", label="chatops",
                            match_cwd="/home/u/ai-agent-skills/**")
    t = target(session_classes=[rule])
    raw = [
        {"sessionId": "svc-root", "kind": "interactive", "status": "busy",
         "cwd": "/home/u/ai-agent-skills", "name": "backend-agent", "startedAt": 1},
        {"sessionId": "svc-deep", "kind": "interactive", "status": "busy",
         "cwd": "/home/u/ai-agent-skills/Monitoring/Bot", "name": "bot", "startedAt": 2},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    view = build_view(config_for(t), conn)
    assert view.working == [], "sessão de serviço vazou para a lista principal"
    assert view.services and view.services[0].active == 2


def test_sibling_directory_is_not_swallowed_by_the_glob(tmp_path):
    # /ai-agent-skills-old must NOT match /ai-agent-skills/**
    conn = conn_for(tmp_path)
    rule = SessionClassRule(class_="service", match_cwd="/home/u/ai-agent-skills/**")
    t = target(session_classes=[rule])
    raw = [{"sessionId": "mine", "kind": "interactive", "status": "busy",
            "cwd": "/home/u/ai-agent-skills-old", "name": "meu", "startedAt": 1}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    assert len(build_view(config_for(t), conn).working) == 1


@pytest.fixture
def blocked_app(tmp_path, monkeypatch):
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    conn = dbm.connect(tmp_path / "tarmac.db")
    t = target()
    raw = [{"id": "blok0001", "kind": "background", "state": "blocked",
            "cwd": "/x", "startedAt": 1, "name": "bloqueada"}]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    dbm.kv_set(conn, "last_collect_at", str(dbm.now_ms()))
    conn.commit()
    return config_for(t)


async def test_resolve_on_blocked_row_explains_itself(blocked_app):
    """`x` on a blocked session can't resolve anything — the session really is
    waiting. It must SAY so instead of doing nothing."""
    app = TarmacApp(blocked_app)
    notes = []
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.notify = lambda msg, **kw: notes.append(msg)
        ol = app.query_one("#sessions", OptionList)
        ol.highlighted = next(
            i for i in range(ol.option_count)
            if (o := ol.get_option_at_index(i)) is not None and o.id == "t1|blok0001"
        )
        await pilot.press("x")
        await pilot.pause()
        assert notes, "tecla x não deu retorno nenhum ao usuário"
        assert "bloqueada" in notes[0].lower() or "vencid" in notes[0].lower()


def test_idle_sessions_group_by_folder(tmp_path):
    """4. Sessões do mesmo projeto ficavam espalhadas na lista: em IDLE a ordem
    era a do banco (started_at), então dois assuntos vizinhos apareciam
    separados por meia dúzia de linhas de outros projetos."""
    conn = conn_for(tmp_path)
    t = target()
    raw = [
        {"sessionId": "aaa00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/proj/benji-dp", "name": "ingestao", "startedAt": 1},
        {"sessionId": "bbb00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/proj/rtb-index", "name": "stress", "startedAt": 2},
        {"sessionId": "ccc00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/proj/benji-dp", "name": "backfill", "startedAt": 3},
        {"sessionId": "ddd00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/proj/rtb-index", "name": "deploy", "startedAt": 4},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    view = build_view(config_for(t), conn)

    folders = [r.cwd for r in view.other]
    assert folders == ["/proj/benji-dp", "/proj/benji-dp",
                       "/proj/rtb-index", "/proj/rtb-index"], \
        f"sessões do mesmo projeto ficaram separadas: {folders}"


def test_same_size_folders_follow_the_path_not_the_session_name(tmp_path):
    """6. Pastas de mesmo tamanho desempatavam pelo nome da sessão, o que
    espalhava as irmãs de uma mesma árvore (/inpowered/*) pela lista."""
    conn = conn_for(tmp_path)
    t = target()
    raw = [
        {"sessionId": "zzz00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/inpowered/alpha", "name": "zulu", "startedAt": 1},
        {"sessionId": "aaa00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/outro/beta", "name": "alfa", "startedAt": 2},
        {"sessionId": "mmm00001-0000-0000-0000-000000000000", "kind": "interactive",
         "status": "idle", "cwd": "/inpowered/omega", "name": "mike", "startedAt": 3},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    view = build_view(config_for(t), conn)

    folders = [r.cwd for r in view.other]
    assert folders == ["/inpowered/alpha", "/inpowered/omega", "/outro/beta"], \
        f"as pastas de /inpowered deviam ficar vizinhas: {folders}"


def test_folder_with_more_sessions_comes_first(tmp_path):
    """5. Ordem das pastas: a que concentra mais sessões vai para cima, mesmo
    que a espera mais longa esteja numa pasta menor. O badge, que existe para
    escalar, passa a ler a pior espera em vez da primeira linha."""
    conn = conn_for(tmp_path)
    t = target()
    raw = [
        {"id": "big00001", "kind": "background", "state": "blocked",
         "cwd": "/proj/grande", "startedAt": 1, "name": "g1"},
        {"id": "big00002", "kind": "background", "state": "blocked",
         "cwd": "/proj/grande", "startedAt": 2, "name": "g2"},
        {"id": "sml00001", "kind": "background", "state": "blocked",
         "cwd": "/proj/pequeno", "startedAt": 3, "name": "p1"},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    now = dbm.now_ms()
    # a espera mais longa (2h) está na pasta com UMA sessão
    for sid, minutes in (("big00001", 10), ("big00002", 5), ("sml00001", 120)):
        conn.execute(
            "UPDATE transitions SET at = ? WHERE target_id = ? AND session_id = ?",
            (now - minutes * 60_000, "t1", sid))
    conn.commit()

    view = build_view(config_for(t), conn)
    assert [r.display_name for r in view.blocked] == ["g1", "g2", "p1"], \
        "a pasta com mais sessões devia vir primeiro"
    text, severity = badge(view)
    assert "2h" in text, f"badge devia mostrar a pior espera (2h), mostrou: {text}"
    assert severity == "alarm", f"a escalação se perdeu com a nova ordem: {severity}"


def test_blocked_keeps_longest_wait_on_top_while_grouping(tmp_path):
    """Empate de contagem: aí quem manda é a espera. A pasta da sessão mais
    antiga vem primeiro e a irmã de pasta a acompanha, em vez de cair no fim."""
    conn = conn_for(tmp_path)
    t = target()
    raw = [
        {"id": "old00001", "kind": "background", "state": "blocked",
         "cwd": "/proj/a", "startedAt": 1, "name": "antiga"},
        {"id": "mid00001", "kind": "background", "state": "blocked",
         "cwd": "/proj/b", "startedAt": 2, "name": "media"},
        {"id": "new00001", "kind": "background", "state": "blocked",
         "cwd": "/proj/a", "startedAt": 3, "name": "recente"},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    now = dbm.now_ms()
    for sid, minutes in (("old00001", 90), ("mid00001", 45), ("new00001", 5)):
        conn.execute(
            "UPDATE transitions SET at = ? WHERE target_id = ? AND session_id = ?",
            (now - minutes * 60_000, "t1", sid))
    conn.commit()

    view = build_view(config_for(t), conn)
    names = [r.display_name for r in view.blocked]
    assert names[0] == "antiga", f"a maior espera saiu do topo: {names}"
    assert names == ["antiga", "recente", "media"], \
        f"a pasta /proj/a devia vir junta, depois /proj/b: {names}"


# --- the account column (a second Claude account on the same machine) --------
# The label used to carry the account ("Mac (personal)") and the 8-char target
# column cut it to "Mac (per".

def two_account_rows(tmp_path):
    conn = conn_for(tmp_path)
    work = target(id="mac", label="Mac", config_dir="~/.claude")
    personal = target(id="mac-personal", label="Mac", account="Personal",
                      config_dir="~/.claude-personal")
    for t, sid, name in ((work, "aaaaaaaa", "trabalho"),
                         (personal, "bbbbbbbb", "pessoal")):
        raw = [{"id": sid, "kind": "background", "state": "working",
                "cwd": "/x", "startedAt": 1, "name": name}]
        apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    conn.commit()
    dbm.kv_set(conn, "last_collect_at", str(dbm.now_ms()))
    conn.commit()
    return config_for(work, personal), conn


def test_account_column_width_is_zero_with_a_single_account():
    from tarmac.derive import Row, account_width
    from tests.test_config_actions import make_row  # same Row factory
    assert account_width([]) == 0
    assert account_width([make_row()]) == 0
    assert account_width([make_row(target_account="Personal")]) == 8
    assert account_width([make_row(target_account="x" * 40)]) == 10  # capped


def col_of(line: str, text: str) -> int:
    """Display column of `text` in `line`.

    NOT the character index: ✎ and · are multi-byte and (for ✎) can measure
    wider than one cell, so indexes lie about alignment.
    """
    from rich.cells import cell_len
    idx = line.index(text)
    return cell_len(line[:idx])


def render_lines(renderable, width: int = 120) -> list[str]:
    import re

    from rich.console import Console
    console = Console(width=width, no_color=True)
    with console.capture() as cap:
        console.print(renderable)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", cap.get())   # styles, not content
    return [ln.rstrip() for ln in plain.splitlines()]


def test_account_shows_next_to_the_target_without_truncating_it(tmp_path):
    """Target, account and wait share one right-aligned cell: rows that use
    none of the extras must not leave an empty band before the pending."""
    from tarmac.derive import account_width, build_view
    from tarmac.render.rows import layout_for, row_renderable
    config, conn = two_account_rows(tmp_path)
    view = build_view(config, conn)
    rows = {r.display_name: r for r in view.working}
    width = account_width(view.working)

    layout = layout_for(view.working, 120)
    assert layout.account == width
    personal = render_lines(row_renderable(rows["pessoal"], "en", layout), 120)[0]
    work = render_lines(row_renderable(rows["trabalho"], "en", layout), 120)[0]
    assert "Personal" in personal
    assert "Mac (per" not in personal          # the truncation is gone
    assert "Personal" not in work              # default account: nothing shown
    # right-aligned as one block: both rows end at the same column
    assert len(personal) == len(work)


# --- a long pending text must stay in its own column ------------------------
# It used to be appended to one Text, so the terminal wrapped it back to
# column 0 and the sentence ran under the session names.

LONG = ("Pendente: corrigir o bug da MV (composite-path) em "
        "`S3ToClickhousePartitionSwapProcessor` e implementar/testar a "
        "sincronização de nomes DV360 em benji-channels (Phase 2).")


def test_long_pending_wraps_inside_its_own_column(tmp_path):
    from tarmac.derive import build_view
    from tarmac.render.rows import layout_for, row_renderable
    config, conn = two_account_rows(tmp_path)
    dbm.upsert_meta(conn, "mac", "aaaaaaaa", next_step=LONG)
    conn.commit()
    row = next(r for r in build_view(config, conn).working
               if r.display_name == "trabalho")

    lines = render_lines(row_renderable(row, "en", layout_for([row], 110)),
                         width=110)
    first = lines[0]
    column = first.index("→")
    assert column > 30, "pending must start after the other columns"
    wrapped = [ln for ln in lines[1:] if ln.strip() and not ln.strip().startswith("/")]
    assert wrapped, "the sentence is long enough to wrap at this width"
    for ln in wrapped:
        assert len(ln) - len(ln.lstrip()) == column, f"wrapped to column 0: {ln!r}"


def test_name_column_follows_the_content_not_the_window(tmp_path):
    from tarmac.derive import build_view
    from tarmac.render.rows import name_column_width
    config, conn = two_account_rows(tmp_path)
    rows = build_view(config, conn).working
    assert name_column_width(rows) == 18                      # floor
    assert name_column_width(rows, floor=4) == len("trabalho")
    long_row = replace(rows[0], display_name="x" * 200)
    assert name_column_width([long_row]) == 44                # cap


def test_once_renderer_shows_the_account(tmp_path):
    from rich.console import Console

    from tarmac.derive import build_view
    from tarmac.render.tui import render_view
    config, conn = two_account_rows(tmp_path)
    console = Console(width=120, no_color=True)
    with console.capture() as cap:
        console.print(render_view(config, build_view(config, conn)))
    out = cap.get()
    assert "Personal" in out
    assert "Mac (per" not in out


def test_swiftbar_line_names_the_account(tmp_path):
    from tarmac.derive import build_view
    from tarmac.render.swiftbar import render_swiftbar
    config, conn = two_account_rows(tmp_path)
    out = render_swiftbar(config, build_view(config, conn))
    assert "pessoal" in out and "Mac Personal" in out
    # the work session keeps a bare target label
    assert [ln for ln in out.splitlines() if "trabalho" in ln and "Personal" not in ln]


def test_account_is_read_from_the_yaml(tmp_path):
    from tarmac.config import load_config
    path = tmp_path / "targets.yaml"
    path.write_text(
        "targets:\n"
        "  - id: mac\n    transport: local\n"
        "  - id: mac-personal\n    label: Mac\n    account: Personal\n"
        "    transport: local\n    config_dir: ~/.claude-personal\n"
    )
    config = load_config(path)
    assert config.target("mac").account == ""
    assert config.target("mac-personal").account == "Personal"


def test_pending_column_never_takes_more_than_its_share():
    from tarmac.render.rows import PENDING_MAX_SHARE, pending_column_width
    assert pending_column_width(200) == 80
    assert pending_column_width(200) / 200 <= PENDING_MAX_SHARE
    assert pending_column_width(40) == 24          # floor on a narrow window
    assert pending_column_width(0) == 40           # unknown size: assume 100


# The window is 148 columns, not the 200 I first checked at: a layout verified
# at one width says nothing about another, so every width is checked.
PANEL_WIDTHS = [100, 120, 148, 200]


@pytest.mark.parametrize("width", PANEL_WIDTHS)
def test_pending_keeps_its_share_and_hugs_the_right_edge(tmp_path, width):
    from tarmac.derive import build_view
    from tarmac.render.rows import (
        PENDING_MAX_SHARE,
        layout_for,
        pending_column_width,
        row_renderable,
    )
    config, conn = two_account_rows(tmp_path)
    dbm.upsert_meta(conn, "mac", "aaaaaaaa", next_step=LONG)
    conn.commit()
    row = next(r for r in build_view(config, conn).working
               if r.display_name == "trabalho")

    cap = pending_column_width(width)
    lines = render_lines(row_renderable(row, "en", layout_for([row], width)),
                         width=width)
    body = [ln for ln in lines if not ln.strip().startswith("/")]
    start = body[0].index("→")

    # its share of the screen, measured — not assumed from one window size
    share = (width - start) / width
    assert share <= PENDING_MAX_SHARE + 1 / width, f"{share:.0%} of {width} cols"
    # anchored right: the column ends at the edge, so the empty gap sits before
    # the sentence and not after it
    assert start == width - cap, f"start={start}, expected {width - cap}"
    for ln in body:
        assert len(ln) <= width, f"row overflowed the window: {ln!r}"
    # continuation lines carry only pending text: they must start in its column
    for ln in body[1:]:
        if ln.strip():
            assert len(ln) - len(ln.lstrip()) == start, \
                f"pending wrapped left of its column: {ln!r}"


@pytest.fixture
def mixed_app(tmp_path, monkeypatch):
    """A short name working, a long one idle — the long one must stay whole."""
    monkeypatch.setenv("TARMAC_HOME", str(tmp_path))
    conn = dbm.connect(tmp_path / "tarmac.db")
    t = target(id="mac", label="Mac")
    raw = [
        {"id": "short001", "kind": "background", "state": "working",
         "cwd": "/x", "startedAt": 1, "name": "curta"},
        {"sessionId": "long0001-0000-0000-0000-000000000000",
         "kind": "interactive", "status": "idle", "cwd": "/y", "startedAt": 2,
         "name": "wsi-nexxen-clickhouse-ingest"},
    ]
    apply_result(conn, TargetResult(t, parse_agents_json(json.dumps(raw))))
    dbm.upsert_meta(conn, "mac", "long0001-0000-0000-0000-000000000000",
                    next_step=LONG)
    dbm.kv_set(conn, "last_collect_at", str(dbm.now_ms()))
    conn.commit()
    return config_for(t)


def app_lines(app, width: int = 200) -> list[str]:
    from textual.widgets import OptionList as OL
    ol = app.query_one("#sessions", OL)
    out: list[str] = []
    for i in range(ol.option_count):
        option = ol.get_option_at_index(i)
        if option is not None:
            out += render_lines(option.prompt, width)
    return out


@pytest.mark.parametrize("width", PANEL_WIDTHS)
async def test_app_anchors_the_pending_column(mixed_app, width):
    """Through the real app, at every window width — the panel window is 148,
    and a cap honoured at 200 told me nothing about it."""
    from tarmac.render.rows import pending_column_width
    app = TarmacApp(mixed_app)
    async with app.run_test(size=(width, 40)) as pilot:
        await pilot.pause()
        # the rows are laid out in the OptionList's content width, which is the
        # window minus its padding — not the window
        inner = app.query_one("#sessions", OptionList).content_size.width
        width, cap = inner, pending_column_width(inner)
        lines = app_lines(app, inner)
        pending = [ln for ln in lines if "→" in ln]
        assert pending, "the long next_step did not render"
        assert pending[0].index("→") == width - cap
        for ln in lines:
            assert len(ln) <= width, f"row overflowed the window: {ln!r}"


async def test_idle_names_are_sized_in_too(mixed_app):
    """Sizing the name column from the top sections only truncated every idle
    name — and IDLE is where most sessions live."""
    app = TarmacApp(mixed_app)
    async with app.run_test(size=(200, 40)) as pilot:
        await pilot.pause()
        rendered = "\n".join(app_lines(app))
        assert "wsi-nexxen-clickhouse-ingest" in rendered
        assert "…" not in rendered


@pytest.mark.parametrize("width", PANEL_WIDTHS)
def test_meta_block_sits_five_columns_right_of_centre(tmp_path, width):
    """Centred read a touch too far left; Marcelo asked for +5 columns.

    Also pins the arithmetic: every column plus its paddings must add up to the
    width exactly, or the pending stops sitting flush against the right edge.
    """
    from tarmac.derive import build_view
    from tarmac.render.rows import META_SHIFT, layout_for, row_renderable
    config, conn = two_account_rows(tmp_path)
    dbm.upsert_meta(conn, "mac", "aaaaaaaa", next_step=LONG)
    conn.commit()
    row = next(r for r in build_view(config, conn).working
               if r.display_name == "trabalho")

    layout = layout_for([row], width)
    cols = layout.fixed_columns()
    assert (sum(cols) + layout.left_gap + layout.right_gap
            + len(cols) + 1) == width
    # 5 is the number Marcelo asked for, written literally: asserting against
    # META_SHIFT made this test self-referential and it passed with the nudge
    # set to 0
    assert META_SHIFT == 5
    assert layout.left_gap - layout.right_gap in (9, 10)  # 2x5, 1 less if odd

    first = render_lines(row_renderable(row, "en", layout), width=width)[0]
    assert col_of(first, "Mac") + layout.target < col_of(first, "→")


@pytest.mark.parametrize("width", PANEL_WIDTHS)
def test_target_and_account_read_as_columns(tmp_path, width):
    """Right-aligned as one block, `Mac` landed ~10 columns further left on the
    rows that also had an account: the labels no longer read as a column."""
    from tarmac.derive import build_view
    from tarmac.render.rows import layout_for, row_renderable
    config, conn = two_account_rows(tmp_path)
    view = build_view(config, conn)
    layout = layout_for(view.working, width)
    lines = {r.display_name: render_lines(row_renderable(r, "en", layout), width)[0]
             for r in view.working}

    # same column in every row, with and without an account
    assert col_of(lines["pessoal"], "Mac") == col_of(lines["trabalho"], "Mac")
    assert "Personal" in lines["pessoal"]
    assert "Personal" not in lines["trabalho"]
    assert col_of(lines["pessoal"], "Mac") > col_of(lines["pessoal"], "Personal")


@pytest.mark.parametrize("width", PANEL_WIDTHS)
async def test_every_label_forms_one_column_in_the_real_app(mixed_app, width):
    """The rule Marcelo set: the target sits in the same column whether or not
    the row also shows an account. Measured in display cells, through the app."""
    from tarmac import db as dbm2
    from tarmac.collect import TargetResult, apply_result
    from tarmac.model import parse_agents_json
    conn = dbm2.connect()  # TARMAC_HOME is the fixture's tmp_path
    extra = [{"sessionId": "acc00001-0000-0000-0000-000000000000",
              "kind": "interactive", "status": "idle", "cwd": "/z",
              "startedAt": 3, "name": "com-conta"}]
    personal = target(id="mac-personal", label="Mac", account="Personal",
                      config_dir="~/.claude-personal")
    apply_result(conn, TargetResult(personal, parse_agents_json(json.dumps(extra))))
    conn.commit()
    config = config_for(*mixed_app.targets, personal)

    app = TarmacApp(config)
    async with app.run_test(size=(width, 40)) as pilot:
        await pilot.pause()
        columns = {}
        for ln in app_lines(app, width - 2):   # OptionList padding: 0 1
            for label in ("Mac", "Personal"):
                if label in ln and "→" not in ln.split(label)[0]:
                    columns.setdefault(label, set()).add(col_of(ln, label))
        assert columns.get("Personal"), "the account row did not render"
        assert len(columns["Mac"]) == 1, f"target in several columns: {columns}"
        assert len(columns["Personal"]) == 1, f"account in several columns: {columns}"


# --- `waitingFor` in the panel's language (agent view documents five values) --

def _pending(row_kwargs, locale="en"):
    from tarmac.derive import Row
    from tarmac.render.rows import pending_text
    base = dict(target_id="t1", target_label="T1", session_id="s", display_name="s",
                eff_state="blocked", kind="background", cwd="/p", short_id="s",
                uuid="u", gone=False, stale=False, pid=42)
    base.update(row_kwargs)
    return pending_text(Row(**base), locale).plain


def test_waiting_for_is_translated_in_both_locales():
    assert _pending({"waiting_for": "input needed"}, "en") == "waiting for an answer"
    assert _pending({"waiting_for": "input needed"}, "pt") == "esperando resposta"
    assert _pending({"waiting_for": "dialog open"}, "pt") == "diálogo aberto"


def test_a_permission_prompt_keeps_its_warning_sign():
    text = _pending({"waiting_for": "permission prompt", "permission_prompt": True})
    assert text == "⚠ permission prompt"


def test_a_waiting_for_we_have_never_seen_is_shown_as_it_arrives():
    """The field is the CLI's vocabulary, not ours: a new value is news, and
    replacing it with a generic 'blocked' would throw away the only thing the
    row says about what it is waiting on."""
    assert _pending({"waiting_for": "quantum handshake"}) == "quantum handshake"


def test_a_blocked_session_with_no_waiting_for_still_says_so():
    assert _pending({"waiting_for": None}, "en") == "blocked"
    assert _pending({"waiting_for": None}, "pt") == "bloqueada"
