from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tidyup import app, console, config
from fixtures.fake_tree import snapshot

CFG = Path("/x/config.yaml")


def test_build_tasks_table():
    t = app.build_tasks(CFG)
    assert list(t) == ["scan", "plan", "report", "trial", "undo", "apply", "upkeep", "runs"]
    for task in t.values():
        assert task.argv[:2] == ["--config", str(CFG)]
        if task.preview_argv:
            assert task.preview_argv[:2] == ["--config", str(CFG)]
            assert "--dry-run" in task.preview_argv and "--yes" not in task.preview_argv
            assert task.confirm
    assert t["scan"].argv[2:] == ["scan"] and t["scan"].preview_argv is None
    assert t["plan"].preview_argv is None and t["plan"].label == "2. Make a plan"
    assert t["report"].argv[2:] == ["report", "--open"]
    assert t["trial"].argv[2:] == ["apply", "--only-folder", "downloads", "--limit", "50", "--yes"]
    assert t["trial"].preview_argv[2:] == ["--dry-run", "apply", "--only-folder", "downloads",
                                           "--limit", "50"]
    assert t["undo"].argv[2:] == ["undo"] and t["undo"].preview_argv[2:] == ["--dry-run", "undo"]
    assert t["apply"].argv[2:] == ["apply", "--yes"]
    assert t["upkeep"].argv[2:] == ["upkeep", "--yes"]
    assert t["runs"].argv[2:] == ["undo", "--list"] and t["runs"].preview_argv is None
    assert t["undo"].confirm == "Put these files back where they were?"


def test_migrate_task():
    t = app.build_tasks(CFG, migrate_to=Path("/d"))
    m = t["migrate"]
    assert m.argv[2:] == ["migrate", "--to", "/d", "--yes"]
    assert m.preview_argv[2:] == ["--dry-run", "migrate", "--to", "/d"]
    assert "/d" in m.confirm and m.label == "Move to another drive..."


def test_run_cli_bad_args_and_sink_restored():
    seen = []
    prev = lambda k, t: None  # noqa: E731
    console.set_sink(prev)
    try:
        assert app.run_cli(["--nonsense"], lambda k, t: seen.append((k, t))) == 2
        assert console.get_sink() is prev
    finally:
        console.set_sink(None)


def test_run_cli_exception_restores_sink(monkeypatch):
    def boom(argv):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(app.cli, "main", boom)
    seen = []
    assert app.run_cli(["scan"], lambda k, t: seen.append((k, t))) == 1
    assert seen[0][0] == "error" and "kaboom" in seen[0][1]
    assert console.get_sink() is None


def test_run_cli_captures_output(tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("sources: []\n", encoding="utf-8")
    seen = []
    app.run_cli(["--config", str(tmp_path / "missing.yaml"), "scan"],
                lambda k, t: seen.append((k, t)))
    assert any(k == "error" and t.startswith("Error: ") for k, t in seen)


def test_plan_exists_and_work_paths(cfg):
    assert not app.plan_exists(cfg)
    p = app.work_paths(cfg)
    assert set(p) == {"config", "work_dir", "manifest", "report", "migrate_manifest"}
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cfg.manifest_path.write_text("x", encoding="utf-8")
    assert app.plan_exists(cfg)


def test_end_to_end_trial_and_undo(fake_tree, mock_attrs, tmp_path):
    cfgfile = tmp_path / "cfgdir" / "config.yaml"
    cfgfile.parent.mkdir()
    cfgfile.write_text(yaml.safe_dump(fake_tree.config_overrides()), encoding="utf-8")
    tasks = app.build_tasks(cfgfile)
    out: list = []
    sink = lambda k, t: out.append((k, t))  # noqa: E731
    home = fake_tree.folders["home"]
    before = snapshot(home)
    assert app.run_cli(tasks["scan"].argv, sink) == 0
    assert app.run_cli(tasks["plan"].argv, sink) == 0
    cfg = config.load_config(cfgfile)
    assert app.plan_exists(cfg)
    after_plan = snapshot(home)
    assert app.run_cli(tasks["trial"].preview_argv, sink) == 0
    assert snapshot(home) == after_plan
    assert app.run_cli(tasks["trial"].argv, sink) == 0
    assert snapshot(home) != after_plan
    assert app.run_cli(tasks["undo"].argv, sink) == 0
    assert snapshot(home) == after_plan
    assert app.run_cli(tasks["runs"].argv, sink) == 0
    assert out
