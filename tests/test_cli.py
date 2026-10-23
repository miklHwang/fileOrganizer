from __future__ import annotations

import argparse
import sys
import types

import pytest

from tidyup import cli, config, errors

CMDS = ["scan", "plan", "report", "apply", "undo", "migrate", "upkeep"]


@pytest.fixture
def fake_cfg(tmp_path, monkeypatch):
    cfg = types.SimpleNamespace(work_dir=tmp_path / "work")
    monkeypatch.setattr(config, "load_config", lambda path=None, overrides=None: cfg)
    monkeypatch.setattr(config, "find_default_config", lambda cwd=None: None)
    return cfg


def _patch_cmd(monkeypatch, name, fn):
    mod = types.ModuleType(f"tidyup.{name}")
    mod.command = fn
    monkeypatch.setitem(sys.modules, f"tidyup.{name}", mod)


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    for c in CMDS:
        with pytest.raises(SystemExit) as e:
            cli.main([c, "--help"])
        assert e.value.code == 0


def test_no_command_and_bad_args_exit_2(capsys):
    for argv in ([], ["bogus"], ["migrate"], ["apply", "--limit", "x"]):
        with pytest.raises(SystemExit) as e:
            cli.main(argv)
        assert e.value.code == 2


def test_namespace_attributes():
    p = cli.build_parser()
    a = p.parse_args(["apply", "--manifest", "m.csv", "--limit", "5", "--only-rule", "r",
                      "--only-folder", "downloads", "--yes"])
    assert (a.manifest, a.limit, a.only_rule, a.only_folder, a.yes) == \
        ("m.csv", 5, "r", "downloads", True)
    a = p.parse_args(["migrate", "--to", "D:\\"])
    assert a.to == "D:\\"
    a = p.parse_args(["undo", "abc", "--list"])
    assert a.run_id == "abc" and a.list
    assert p.parse_args(["report", "--open"]).open
    assert p.parse_args(["upkeep", "--yes"]).yes


def test_options_before_and_after(fake_cfg, monkeypatch):
    seen = []
    _patch_cmd(monkeypatch, "scan", lambda cfg, args: seen.append(args) or 0)
    assert cli.main(["--dry-run", "scan"]) == 0
    assert cli.main(["scan", "--dry-run"]) == 0
    assert cli.main(["scan"]) == 0
    assert [a.dry_run for a in seen] == [True, True, False]
    a = seen[0]
    for k in ("command", "config", "dry_run", "debug", "open", "manifest", "limit",
              "only_rule", "only_folder", "yes", "run_id", "list", "to"):
        assert hasattr(a, k)
    assert a.command == "scan"


def test_dispatch_returns_code(fake_cfg, monkeypatch):
    _patch_cmd(monkeypatch, "upkeep", lambda cfg, args: 3)
    assert cli.main(["upkeep", "--yes"]) == 3


def test_missing_config_file(tmp_path, capsys):
    assert cli.main(["--config", str(tmp_path / "nope.yaml"), "scan"]) == 1
    err = capsys.readouterr().err
    assert "not found" in err and "Traceback" not in err


def test_tidyup_error_exit_1(fake_cfg, monkeypatch, capsys):
    def boom(cfg, args):
        raise errors.TidyupError("plain message")
    _patch_cmd(monkeypatch, "plan", boom)
    assert cli.main(["plan"]) == 1
    assert "plain message" in capsys.readouterr().err


def test_runtime_error_writes_crash_file(fake_cfg, monkeypatch, capsys):
    def boom(cfg, args):
        raise RuntimeError("kaboom")
    _patch_cmd(monkeypatch, "plan", boom)
    assert cli.main(["plan"]) == 1
    files = list((fake_cfg.work_dir / "logs").glob("crash_*.txt"))
    assert len(files) == 1 and "kaboom" in files[0].read_text()
    assert "crash_" in capsys.readouterr().out


def test_debug_reraises(fake_cfg, monkeypatch):
    def boom(cfg, args):
        raise RuntimeError("kaboom")
    _patch_cmd(monkeypatch, "plan", boom)
    with pytest.raises(RuntimeError):
        cli.main(["plan", "--debug"])


def test_keyboard_interrupt(fake_cfg, monkeypatch, capsys):
    def boom(cfg, args):
        raise KeyboardInterrupt
    _patch_cmd(monkeypatch, "apply", boom)
    assert cli.main(["apply"]) == 130
    assert "tidyup undo" in capsys.readouterr().out
