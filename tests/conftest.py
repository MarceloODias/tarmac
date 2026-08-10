"""Safety net: no test may ever touch the real ~/.tarmac.

A stray task with a test target's `claude_bin` turned up in Marcelo's live
database, which means one test leaked out of its tmp dir. Rather than audit
every fixture forever, every test now gets TARMAC_HOME pointed at a private
temp dir by default, and opening the real DB path raises.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REAL_HOME = Path("~/.tarmac").expanduser().resolve()


@pytest.fixture(autouse=True)
def isolate_tarmac_home(tmp_path, monkeypatch):
    home = tmp_path / "_isolated-tarmac-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TARMAC_HOME", str(home))
    monkeypatch.delenv("TARMAC_TARGETS", raising=False)

    import tarmac.db as db_mod

    real_connect = db_mod.connect

    def guarded_connect(path=None):
        resolved = Path(path).expanduser().resolve() if path else None
        if resolved is not None and REAL_HOME in resolved.parents:
            raise AssertionError(
                f"teste tentou abrir o banco REAL ({resolved}) — use tmp_path"
            )
        if path is None and REAL_HOME in db_mod.db_path().parents:
            raise AssertionError(
                "teste tentou abrir o banco REAL via TARMAC_HOME — isolamento furou"
            )
        return real_connect(path)

    monkeypatch.setattr(db_mod, "connect", guarded_connect)
    # modules that imported `connect` directly get the guarded one too
    import tarmac.render.tui_app as tui_mod

    monkeypatch.setattr(tui_mod, "connect", guarded_connect)
    # tests are free to point TARMAC_HOME at their own tmp dir; what they are
    # NOT free to do is reach the real one — that is what guarded_connect enforces
    yield
