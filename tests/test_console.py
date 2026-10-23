from __future__ import annotations

import io

import pytest

from tidyup import console


@pytest.mark.parametrize("n,expected", [(0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"),
                                        (1536, "1.5 KB"), (1024 ** 2, "1.0 MB"),
                                        (int(3.2 * 1024 ** 3), "3.2 GB")])
def test_human_size(n, expected):
    assert console.human_size(n) == expected


def test_confirm_assume_yes():
    assert console.confirm("ok?", assume_yes=True) is True


def test_confirm_non_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    assert console.confirm("ok?") is False


class _TTY(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("ans,expected", [("y", True), ("YES", True), ("n", False), ("", False)])
def test_confirm_tty(monkeypatch, ans, expected):
    monkeypatch.setattr("sys.stdin", _TTY(ans + "\n"))
    monkeypatch.setattr("builtins.input", lambda prompt="": ans)
    assert console.confirm("ok?") is expected


def test_progress_yields_all():
    assert list(console.progress(range(5), desc="t")) == [0, 1, 2, 3, 4]


def test_output_prefixes(capsys):
    console.info("a")
    console.success("b")
    console.warn("c")
    console.error("d")
    console.heading("Title")
    console.table(["x", "yy"], [[1, "a"], [22, "bbb"]])
    cap = capsys.readouterr()
    assert "OK: b" in cap.out and "WARNING: c" in cap.out
    assert "Error: d" in cap.err
    assert "Title\n-----" in cap.out
    assert "22" in cap.out and "bbb" in cap.out


def test_sink_receives_all_kinds_and_restores(capsys):
    got = []
    console.set_sink(lambda k, t: got.append((k, t)))
    try:
        assert console.get_sink() is not None
        console.info("a")
        console.success("b")
        console.warn("c")
        console.error("d")
        console.heading("Title")
        console.table(["x", "yy"], [[1, "a"]])
        assert list(console.progress(range(3), desc="t")) == [0, 1, 2]
    finally:
        console.set_sink(None)
    kinds = [k for k, _ in got]
    assert kinds[:6] == ["info", "success", "warn", "error", "heading", "table"]
    assert dict(got)["success"] == "OK: b" and dict(got)["warn"] == "WARNING: c"
    assert dict(got)["error"] == "Error: d"
    assert dict(got)["heading"] == "Title\n-----"
    assert got[-1] == ("progress", "t: 3/3")
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""
    assert console.get_sink() is None
    console.info("printed")
    assert "printed" in capsys.readouterr().out


def test_progress_without_stderr_no_tqdm(monkeypatch):
    monkeypatch.setattr("sys.stderr", None)
    assert list(console.progress(iter(range(4)), desc="t")) == [0, 1, 2, 3]
