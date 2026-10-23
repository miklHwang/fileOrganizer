from __future__ import annotations

import argparse
from pathlib import Path

from tidyup import fsops, journal as jr, undo, winutil
from tidyup.config import load_config


def snap(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


def setup(tmp_path):
    cfg = load_config(None, overrides={"work_dir": str(tmp_path / "work")})
    root = tmp_path / "t"
    (root / "src" / "proj").mkdir(parents=True)
    (root / "src" / "a.txt").write_bytes(b"aaa")
    (root / "src" / "proj" / "x.py").write_bytes(b"xx")
    return cfg, root


def do_run(cfg, root, finish=True, kind="apply"):
    j = jr.Journal.create(cfg.logs_dir, kind, {})
    fsops.ensure_dir(root / "dest" / "sub", j)
    fsops.move_file(root / "src" / "a.txt", root / "dest" / "sub" / "a.txt", journal=j, row_id="1")
    fsops.move_tree(root / "src" / "proj", root / "dest" / "proj", journal=j, row_id="2")
    if finish:
        j.finish({})
    else:
        j.close()
    return j


def test_restores_and_removes_dirs(tmp_path):
    cfg, root = setup(tmp_path)
    before = snap(root)
    j = do_run(cfg, root)
    assert snap(root) != before
    res = undo.undo_run(cfg, jr.read_run(j.path))
    assert snap(root) == before
    assert res.restored == 2 and res.errors == 0


def test_cross_volume(tmp_path, cross_volume):
    cfg, root = setup(tmp_path)
    before = snap(root)
    j = do_run(cfg, root)
    undo.undo_run(cfg, jr.read_run(j.path))
    assert snap(root) == before


def test_occupied_original(tmp_path, capsys):
    cfg, root = setup(tmp_path)
    j = do_run(cfg, root)
    (root / "src" / "a.txt").write_bytes(b"new")
    undo.undo_run(cfg, jr.read_run(j.path))
    assert (root / "src" / "a.txt").read_bytes() == b"new"
    assert (root / "src" / "a (2).txt").read_bytes() == b"aaa"
    assert "WARNING" in capsys.readouterr().out


def test_missing_dst_skipped(tmp_path):
    cfg, root = setup(tmp_path)
    j = do_run(cfg, root)
    (root / "dest" / "sub" / "a.txt").unlink()
    res = undo.undo_run(cfg, jr.read_run(j.path))
    assert res.skipped == 1 and res.restored == 1


def test_trash_manual(tmp_path):
    cfg, root = setup(tmp_path)
    j = jr.Journal.create(cfg.logs_dir, "apply", {})
    j.trash(src=root / "gone.txt", row_id="5")
    j.finish({})
    res = undo.undo_run(cfg, jr.read_run(j.path))
    assert res.manual == [root / "gone.txt"]


def test_interrupted_run(tmp_path):
    cfg, root = setup(tmp_path)
    before = snap(root)
    j = do_run(cfg, root, finish=False)
    run = jr.read_run(j.path)
    assert not run.finished
    undo.undo_run(cfg, run)
    assert snap(root) == before


def test_own_log_marks_undone_and_redo(tmp_path):
    cfg, root = setup(tmp_path)
    before = snap(root)
    j = do_run(cfg, root)
    after = snap(root)
    res = undo.undo_run(cfg, jr.read_run(j.path))
    orig = jr.read_run(j.path)
    assert orig.undone_by == res.run_id
    ulog = jr.read_run(res.log_path)
    assert ulog.kind == "undo" and ulog.header["meta"]["undoes"] == j.run_id and ulog.finished
    assert snap(root) == before
    undo.undo_run(cfg, ulog)
    assert snap(root) == after


def test_command_latest_list_dry(tmp_path, capsys):
    cfg, root = setup(tmp_path)
    before = snap(root)
    j = do_run(cfg, root)
    after = snap(root)
    ns = lambda **k: argparse.Namespace(**{"run_id": None, "list": False, **k})  # noqa: E731
    assert undo.command(cfg, ns(list=True)) == 0
    out = capsys.readouterr().out
    assert j.run_id in out and "apply" in out and "finished" in out
    assert undo.command(cfg, ns(dry_run=True)) == 0
    assert snap(root) == after and len(jr.list_runs(cfg.logs_dir)) == 1
    assert undo.command(cfg, ns()) == 0
    assert snap(root) == before
    undo.command(cfg, ns(list=True))
    assert "undone by" in capsys.readouterr().out
    import pytest
    from tidyup.errors import TidyupError
    with pytest.raises(TidyupError):
        jr.find_run(cfg.logs_dir, None)
    # explicit re-undo of an already-undone run warns and continues
    undo.command(cfg, ns(run_id=j.run_id))
    assert "already undone" in capsys.readouterr().out
