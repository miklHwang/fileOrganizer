from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from tidyup import console, models, plan, scan, upkeep
from tidyup.models import Action, Confidence, ManifestRow

from fixtures.fake_tree import snapshot


def _row(i, action, src, dst, conf):
    return ManifestRow(id=i, action=action, source=src, destination=dst, rule="t",
                       confidence=conf, notes="", approved=True)


@pytest.fixture
def crafted(cfg, fake_tree, monkeypatch):
    d = fake_tree.folders["downloads"]
    names = ["a_high_move", "b_high_rename", "c_med_move", "d_high_del", "e_high_dup", "f_low_mr"]
    for n in names:
        (d / f"{n}.txt").write_text(n)
    dest = fake_tree.folders["home"] / "Out"
    H, M, L = Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW
    spec = [(Action.MOVE, H), (Action.RENAME, H), (Action.MOVE, M),
            (Action.REVIEW_DELETE, H), (Action.DUPLICATE, H), (Action.MOVE_RENAME, L)]
    rows = [_row(i + 1, a, d / f"{n}.txt", dest / f"{n}_new.txt", c)
            for i, (n, (a, c)) in enumerate(zip(names, spec))]
    calls = []
    real_scan = scan.scan_sources

    def spy_scan(c, **kw):
        calls.append(c.sources)
        return real_scan(c, **kw)

    monkeypatch.setattr(scan, "scan_sources", spy_scan)
    monkeypatch.setattr(plan, "build_manifest",
                        lambda c, recs, **kw: plan.PlanResult(rows, [], [], {}))
    return rows, dest, calls


def _args(**kw):
    ns = dict(yes=True, dry_run=False)
    ns.update(kw)
    return argparse.Namespace(**ns)


def test_only_high_move_rename(cfg, fake_tree, crafted):
    rows, dest, calls = crafted
    res = upkeep.run_upkeep(cfg)
    assert res.moved == 2 and res.errors == 0
    assert sorted(p.name for p in dest.iterdir()) == ["a_high_move_new.txt", "b_high_rename_new.txt"]
    d = fake_tree.folders["downloads"]
    for n in ("c_med_move", "d_high_del", "e_high_dup", "f_low_mr"):
        assert (d / f"{n}.txt").exists()
    # sources limited to downloads/desktop/screenshots
    allowed = [fake_tree.folders[k] for k in ("downloads", "desktop", "screenshots")]
    assert calls and {s.path for s in calls[0]} == set(allowed)
    # manifest, journal and log
    assert list(cfg.logs_dir.glob("upkeep_manifest_*.csv"))
    assert len(models.read_manifest(next(cfg.logs_dir.glob("upkeep_manifest_*.csv")))) == 6
    assert res.log_path and "upkeep" in res.log_path.read_text(encoding="utf-8")
    assert len((cfg.logs_dir / "upkeep.log").read_text(encoding="utf-8").splitlines()) == 1


def test_dry_run_changes_nothing(cfg, fake_tree, crafted):
    before = snapshot(fake_tree.folders["home"])
    work_before = snapshot(cfg.work_dir) if cfg.work_dir.exists() else None
    res = upkeep.run_upkeep(cfg, dry_run=True)
    assert res.dry_run and res.run_id is None
    assert upkeep.command(cfg, _args(dry_run=True)) == 0
    assert snapshot(fake_tree.folders["home"]) == before
    if work_before is None:
        assert not cfg.work_dir.exists()
    else:
        assert snapshot(cfg.work_dir) == work_before
    assert not cfg.logs_dir.exists() or not any(cfg.logs_dir.iterdir())


def test_never_actions_even_if_configured(cfg, fake_tree, crafted):
    rows, dest, _ = crafted
    cfg.upkeep["actions"] = ["move", "rename", "move_rename", "review_delete", "duplicate", "skip"]
    res = upkeep.run_upkeep(cfg)
    d = fake_tree.folders["downloads"]
    assert (d / "d_high_del.txt").exists() and (d / "e_high_dup.txt").exists()
    assert not (dest / "d_high_del_new.txt").exists() and not (dest / "e_high_dup_new.txt").exists()
    assert res.moved == 2


def test_confirmation(cfg, fake_tree, crafted, monkeypatch):
    rows, dest, _ = crafted
    # non-interactive without --yes: refused
    monkeypatch.setattr(sys, "stdin", None)
    assert upkeep.command(cfg, _args(yes=False)) == 1
    assert not dest.exists()
    # interactive 'n'
    class Tty:
        def isatty(self):
            return True
    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    assert upkeep.command(cfg, _args(yes=False)) == 1
    assert not dest.exists()
    # --yes proceeds
    assert upkeep.command(cfg, _args(yes=True)) == 0
    assert len(list(dest.iterdir())) == 2


def test_upkeep_leaves_archives(cfg, fake_tree):
    paths = [fake_tree.folders["downloads"] / "u.zip", fake_tree.folders["desktop"] / "v.7z"]
    for p in paths:
        p.write_bytes(b"PK" + p.name.encode())
    _u, selected, _p = upkeep._prepare(cfg, dry_run=True)
    assert not [r for r in selected if r.source in paths]
    upkeep.run_upkeep(cfg)
    for p in paths:
        assert p.read_bytes() == b"PK" + p.name.encode()


def test_upkeep_moves_archive_when_not_kept(cfg, fake_tree):
    cfg.rules["archives"]["keep_in_place"] = False
    z = fake_tree.folders["downloads"] / "u.zip"
    z.write_bytes(b"PKu")
    upkeep.run_upkeep(cfg)
    assert not z.exists()
    assert (fake_tree.folders["downloads"] / "Archives" / "u.zip").read_bytes() == b"PKu"
