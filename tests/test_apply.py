from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from tidyup import apply, console, fsops, journal as jr, winutil
from tidyup.errors import FileLockedError
from tidyup.models import Action, Confidence, ManifestRow, write_manifest


def row(i, src, dst, action=Action.MOVE, rule="r", conf=Confidence.HIGH, approved=True):
    return ManifestRow(i, action, Path(src), None if dst is None else Path(dst), rule, conf, "", approved)


def mk(p, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture
def h(cfg, fake_tree):
    return fake_tree.folders["home"]


def test_only_approved_rows(cfg, h):
    a, b = mk(h / "Downloads/a.txt"), mk(h / "Downloads/b.txt")
    rows = [row(1, a, h / "out/a.txt"), row(2, b, h / "out/b.txt", approved=False),
            row(3, b, None, action=Action.SKIP)]
    res = apply.apply_rows(cfg, apply.select_rows(rows, cfg))
    assert res.moved == 1 and not a.exists() and b.exists()
    assert (h / "out/a.txt").exists()
    assert res.log_path and res.log_path.exists()


def test_select_filters(cfg, h):
    rows = [row(i, h / f"Downloads/f{i}.txt", h / f"o/f{i}.txt", rule="x" if i % 2 else "Y",
                conf=[Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH][i % 3]) for i in range(1, 7)]
    rows.append(row(7, h / "Desktop/d.txt", h / "o/d.txt", rule="x"))
    assert [r.id for r in apply.select_rows(rows, cfg, limit=2)] == [1, 2]
    assert [r.id for r in apply.select_rows(rows, cfg, only_rule="X")] == [1, 3, 5, 7]
    assert [r.id for r in apply.select_rows(rows, cfg, only_folder="desktop")] == [7]
    assert [r.id for r in apply.select_rows(rows, cfg, only_folder=str(h / "Desktop"))] == [7]
    assert [r.id for r in apply.select_rows(rows, cfg, actions={Action.RENAME})] == []
    # rank comparison: "low" < "medium" < "high" lexically would be wrong ("high" < "low")
    got = apply.select_rows(rows, cfg, min_confidence=Confidence.MEDIUM)
    assert {r.confidence for r in got} == {Confidence.MEDIUM, Confidence.HIGH}
    got = apply.select_rows(rows, cfg, min_confidence=Confidence.HIGH)
    assert {r.confidence for r in got} == {Confidence.HIGH}
    assert len(apply.select_rows(rows, cfg, min_confidence=Confidence.LOW)) == 7


def test_review_delete_moves_not_deletes(cfg, h):
    a = mk(h / "Downloads/old.exe", b"data")
    res = apply.apply_rows(cfg, [row(1, a, None, action=Action.REVIEW_DELETE)])
    assert res.moved == 1 and not a.exists()
    moved = cfg.resolve_destination("_Review_Delete", a) / "old.exe"
    assert moved.read_bytes() == b"data"


def test_recycle_mode_calls_trash(cfg, h, monkeypatch):
    import dataclasses
    cfg2 = dataclasses.replace(cfg, delete_mode="recycle_bin")
    calls = []
    monkeypatch.setattr(winutil, "send_to_recycle_bin", lambda p: calls.append(Path(p)))
    a = mk(h / "Downloads/old.exe")
    res = apply.apply_rows(cfg2, [row(1, a, None, action=Action.REVIEW_DELETE)])
    assert res.trashed == 1 and calls == [a]
    ops = jr.read_run(res.log_path).ops
    assert ops[-1]["type"] == "trash"


def test_refuses_protected_and_hidden_rows(cfg, h, fake_tree, mock_attrs):
    hidden = fake_tree.hidden[0]
    assert hidden.exists()
    ok = mk(h / "Downloads/ok.txt")
    prot = h / "AppData/x.txt"
    mk(prot)
    res = apply.apply_rows(cfg, [row(1, hidden, h / "o/h.txt"), row(2, prot, h / "o/p.txt"),
                                row(3, ok, h / "o/ok.txt")])
    assert res.skipped == 2 and res.moved == 1
    assert hidden.exists() and prot.exists()


def test_refuses_row_inside_atomic_folder(cfg, h, fake_tree):
    proj = fake_tree.atomic_dirs[0]
    inner = [p for p in proj.rglob("*") if p.is_file()]
    git = [p for p in inner if ".git" in p.parts]
    other = [p for p in inner if ".git" not in p.parts]
    assert git and other
    res = apply.apply_rows(cfg, [row(1, git[0], h / "o/g"), row(2, other[0], h / "o/m")])
    assert res.skipped == 2 and git[0].exists() and other[0].exists()


def test_atomic_folder_moved_whole(cfg, h, fake_tree):
    proj = fake_tree.atomic_dirs[0]
    before = sorted(p.relative_to(proj).as_posix() for p in proj.rglob("*"))
    dst = h / "Organized/proj"
    res = apply.apply_rows(cfg, [row(1, proj, dst)])
    assert res.moved == 1 and not proj.exists()
    assert sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*")) == before


def test_dir_into_itself_is_error(cfg, h, fake_tree):
    proj = fake_tree.atomic_dirs[0]
    res = apply.apply_rows(cfg, [row(1, proj, proj / "sub/p")])
    assert res.errors + res.skipped == 1 and proj.exists()


def test_collision_gets_suffix(cfg, h):
    a = mk(h / "Downloads/a.txt", b"new")
    mk(h / "o/a.txt", b"old")
    apply.apply_rows(cfg, [row(1, a, h / "o/a.txt")])
    assert (h / "o/a.txt").read_bytes() == b"old"
    assert (h / "o/a (2).txt").read_bytes() == b"new"


def test_locked_file_skipped_and_logged(cfg, h, monkeypatch):
    a, b = mk(h / "Downloads/a.txt"), mk(h / "Downloads/b.txt")
    real = fsops.move_file

    def fake(src, dst, **kw):
        if Path(src) == a:
            raise FileLockedError("in use")
        return real(src, dst, **kw)

    monkeypatch.setattr(fsops, "move_file", fake)
    res = apply.apply_rows(cfg, [row(1, a, h / "o/a.txt"), row(2, b, h / "o/b.txt")])
    assert res.skipped == 1 and res.moved == 1 and a.exists() and not b.exists()
    run = jr.read_run(res.log_path)
    assert any(e["type"] == "skip" and e["row_id"] == "1" for e in run.entries)
    assert run.finished


def test_missing_source_skipped(cfg, h):
    res = apply.apply_rows(cfg, [row(1, h / "Downloads/none.txt", h / "o/n.txt")])
    assert res.skipped == 1


def test_dry_run_changes_nothing(cfg, h):
    a = mk(h / "Downloads/a.txt")
    res = apply.apply_rows(cfg, [row(1, a, h / "o/a.txt")], dry_run=True)
    assert res.dry_run and res.log_path is None and res.run_id is None
    assert a.exists() and not (h / "o").exists()
    assert not cfg.logs_dir.exists() or not list(cfg.logs_dir.glob("*.jsonl"))


def test_interrupt_finishes_journal(cfg, h, monkeypatch):
    a = mk(h / "Downloads/a.txt")
    monkeypatch.setattr(fsops, "move_file", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        apply.apply_rows(cfg, [row(1, a, h / "o/a.txt")])
    (log,) = list(cfg.logs_dir.glob("*.jsonl"))
    assert jr.read_run(log).finished


def _args(manifest, **kw):
    d = dict(manifest=str(manifest), limit=None, only_rule=None, only_folder=None, yes=False, dry_run=False)
    d.update(kw)
    return argparse.Namespace(**d)


def test_confirmation_refused_does_nothing(cfg, h, tmp_path, monkeypatch):
    a = mk(h / "Downloads/a.txt")
    m = tmp_path / "m.csv"
    write_manifest(m, [row(1, a, h / "o/a.txt")])
    monkeypatch.setattr(console, "confirm", lambda *a, **k: False)
    assert apply.command(cfg, _args(m)) == 1
    assert a.exists()


def test_command_yes_and_dry_run(cfg, h, tmp_path, capsys):
    a = mk(h / "Downloads/a.txt")
    m = tmp_path / "m.csv"
    write_manifest(m, [row(1, a, h / "o/a.txt")])
    assert apply.command(cfg, _args(m, dry_run=True)) == 0
    assert a.exists()
    assert apply.command(cfg, _args(m, yes=True)) == 0
    assert not a.exists() and (h / "o/a.txt").exists()
    assert "tidyup undo" in capsys.readouterr().out


def test_command_missing_manifest(cfg, tmp_path):
    from tidyup.errors import TidyupError
    with pytest.raises(TidyupError):
        apply.command(cfg, _args(tmp_path / "nope.csv"))


def test_confirm_changes_helper(monkeypatch):
    seen = []
    monkeypatch.setattr(console, "confirm", lambda p, assume_yes=False: seen.append((p, assume_yes)) or True)
    assert apply.confirm_changes(3, assume_yes=True)
    assert seen == [("Apply 3 changes?", True)]
