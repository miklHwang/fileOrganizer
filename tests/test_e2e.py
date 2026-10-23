"""End-to-end safety tests driving the public functions (no module is mocked except winutil)."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pytest

from tidyup import apply, journal as jr, migrate, models, plan, report, scan, undo, upkeep, winutil
from tidyup import cli
from tidyup.errors import VerifyError
from tidyup.models import Action

from fixtures.fake_tree import snapshot


def _plan(cfg, approve_all=False):
    records = scan.scan_sources(cfg, show_progress=False).records
    rows = plan.build_manifest(cfg, records).rows
    if approve_all:
        for r in rows:
            if r.action != Action.SKIP:
                r.approved = True
    # round-trip through the CSV like a real user would
    models.write_manifest(cfg.manifest_path, rows)
    return records, models.read_manifest(cfg.manifest_path)


def _run_and_undo(cfg, rows):
    res = apply.apply_rows(cfg, apply.select_rows(rows, cfg))
    assert res.errors == 0
    ures = undo.undo_run(cfg, jr.find_run(cfg.logs_dir, res.run_id))
    assert ures.errors == 0
    return res


@pytest.mark.parametrize("approve_all", [True, False])
def test_plan_apply_undo_restores_tree_byte_for_byte(cfg, fake_tree, mock_attrs, approve_all):
    home = fake_tree.folders["home"]
    before = snapshot(home)
    assert str(fake_tree.long_path_file.relative_to(home).as_posix()) in before
    _records, rows = _plan(cfg, approve_all)
    res = apply.apply_rows(cfg, apply.select_rows(rows, cfg))
    assert res.errors == 0 and res.moved > 0
    assert snapshot(home) != before
    ures = undo.undo_run(cfg, jr.find_run(cfg.logs_dir, res.run_id))
    assert ures.errors == 0
    assert snapshot(home) == before
    for p in fake_tree.collision_pair:
        assert p.exists()


def test_atomic_folders_never_split(cfg, fake_tree, mock_attrs):
    docs = fake_tree.folders["documents"]
    atomic_before = {d: snapshot(d) for d in fake_tree.atomic_dirs}
    _records, rows = _plan(cfg, approve_all=True)
    atomic_keys = {winutil.norm_key(d) for d in fake_tree.atomic_dirs}
    # no row ever targets something inside an atomic dir
    for r in rows:
        for d in fake_tree.atomic_dirs:
            assert d not in r.source.parents, r
    apply.apply_rows(cfg, apply.select_rows(rows, cfg))
    for d, snap in atomic_before.items():
        if d.exists():
            assert snapshot(d) == snap
        else:
            # moved whole: exactly one location holds an identical copy
            found = [p for p in (cfg.work_dir.parent).rglob(d.name)
                     if p.is_dir() and snapshot(p) == snap]
            assert len(found) == 1, d
    assert atomic_keys and docs.exists()


def test_cloud_only_never_opened(cfg, fake_tree, mock_attrs, open_spy):
    cloud = {winutil.norm_key(p).lower() for p in fake_tree.cloud_only}
    records, rows = _plan(cfg, approve_all=True)
    report.build_report(cfg, records, rows)
    res = apply.apply_rows(cfg, apply.select_rows(rows, cfg))
    assert res.errors == 0
    upkeep.run_upkeep(cfg)
    opened = {s.lower() for s in open_spy}
    assert not (opened & cloud), opened & cloud


def test_file_count_preserved(cfg, fake_tree, mock_attrs):
    home = fake_tree.folders["home"]
    before = [k for k, v in snapshot(home).items() if v is not None]
    _records, rows = _plan(cfg, approve_all=True)
    apply.apply_rows(cfg, apply.select_rows(rows, cfg))
    after = [k for k, v in snapshot(home).items() if v is not None]
    assert len(after) == len(before)
    review = [r for r in rows if r.action == Action.REVIEW_DELETE]
    assert review
    assert not [r for r in review if r.source.exists()]
    assert any("_Review_Delete" in k for k in after)


def test_never_overwrites_collisions(cfg, fake_tree, mock_attrs):
    a, b = fake_tree.collision_pair
    data = {a: a.read_bytes(), b: b.read_bytes()}
    home = fake_tree.folders["home"]
    _records, rows = _plan(cfg, approve_all=True)
    apply.apply_rows(cfg, apply.select_rows(rows, cfg))
    found = [p for p in home.rglob("notes*.txt")]
    assert sorted(p.read_bytes() for p in found if p.read_bytes() in data.values()) == \
        sorted(data.values())


def test_cross_volume_apply_and_undo(cfg, fake_tree, mock_attrs, cross_volume):
    home = fake_tree.folders["home"]
    before = snapshot(home)
    _records, rows = _plan(cfg)
    _run_and_undo(cfg, rows)
    assert snapshot(home) == before


def test_upkeep_only_high_confidence_move_rename(cfg, fake_tree, mock_attrs):
    home = fake_tree.folders["home"]
    before = snapshot(home)
    res = upkeep.run_upkeep(cfg)
    assert res.errors == 0
    run = jr.find_run(cfg.logs_dir, res.run_id)
    allowed = {"move", "rename", "move_rename"}
    moves = [o for o in run.ops if o["type"] == "move"]
    assert all(o["action"] in allowed for o in moves)
    assert not [o for o in run.ops if o["type"] == "trash"]
    # nothing from the review/duplicate buckets
    assert not [k for k in snapshot(home) if "_Review_Delete" in k or "_Duplicates" in k]
    undo.undo_run(cfg, run)
    assert snapshot(home) == before


def test_migrate_then_undo_restores(cfg, fake_tree, mock_attrs, cross_volume, tmp_path):
    home = fake_tree.folders["home"]
    target = tmp_path / "D"
    target.mkdir()
    before = snapshot(home)
    migrate.command(cfg, argparse.Namespace(to=str(target), manifest=None, yes=True, dry_run=True))
    assert snapshot(home) == before
    rows = models.read_manifest(cfg.migrate_manifest_path)
    res = migrate.run_migration(cfg, rows, target)
    assert res.errors == 0 and res.moved > 0
    assert snapshot(target)
    undo.undo_run(cfg, jr.find_run(cfg.logs_dir, res.run_id))
    assert snapshot(home) == before


def test_migrate_corrupt_copy_leaves_source(tmp_path, cross_volume, monkeypatch):
    from tidyup import fsops

    src = tmp_path / "s" / "f.bin"
    src.parent.mkdir()
    src.write_bytes(b"good data")

    def bad_copy(a, b, **k):
        with open(b, "wb") as fh:
            fh.write(b"bad")

    monkeypatch.setattr(shutil, "copy2", bad_copy)
    with pytest.raises(VerifyError):
        fsops.move_file(src, tmp_path / "D" / "f.bin")
    assert src.read_bytes() == b"good data"
    assert not (tmp_path / "D" / "f.bin").exists()


def test_cli_walkthrough(fake_tree, mock_attrs, tmp_path, capsys):
    home = fake_tree.folders["home"]
    before = snapshot(home)
    conf = tmp_path / "e2e_config.yaml"
    conf.write_text(json.dumps(fake_tree.config_overrides()), encoding="utf-8")
    base = ["--config", str(conf)]
    assert cli.main(base + ["scan"]) == 0
    assert cli.main(base + ["plan"]) == 0
    assert snapshot(home) == before
    assert cli.main(base + ["apply", "--only-folder", "downloads", "--limit", "50", "--yes"]) == 0
    assert cli.main(base + ["undo"]) == 0
    assert snapshot(home) == before
