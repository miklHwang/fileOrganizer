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
    res = upkeep.run_upkeep(cfg, dry_run=True)
    assert res.dry_run and res.run_id is None
    assert upkeep.command(cfg, _args(dry_run=True)) == 0
    assert snapshot(fake_tree.folders["home"]) == before


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
