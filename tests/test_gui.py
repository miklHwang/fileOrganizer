from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")


def test_selftest(tmp_path, monkeypatch):
    from tidyup import config, gui

    monkeypatch.setattr(config, "app_home", lambda: tmp_path / "tidyup")
    monkeypatch.chdir(tmp_path)
    try:
        assert gui.main(["--selftest"]) == 0
    except tk.TclError:
        pytest.skip("no display available")
