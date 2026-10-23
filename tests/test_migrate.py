from __future__ import annotations

import argparse
import dataclasses
import os
from pathlib import Path

import pytest

from tidyup import console, fsops, journal, migrate, models, winutil
from tidyup.errors import InsufficientSpaceError, TidyupError
from tidyup.models import Action

from fixtures.fake_tree import snapshot


@pytest.fixture
def target(tmp_path) -> Path:
    d = tmp_path / "D"
    d.mkdir()
    return d


def _args(target, **kw) -> argparse.Namespace:
    ns = dict(to=str(target), manifest=None, yes=True, dry_run=False)
    ns.update(kw)
    return argparse.Namespace(**ns)


def _movable(snap):
    return {k: v for k, v in snap.items() if not k.startswith(".hidden") and not k.startswith("AppData")}


def _home_snapshot(fake_tree):
    return snapshot(fake_tree.folders["home"])


def test_dry_run_writes_manifest_moves_nothing(cfg, fake_tree, target, cross_volume):
    before = _home_snapshot(fake_tree)
    assert migrate.command(cfg, _args(target, dry_run=True)) == 0
    assert cfg.migrate_manifest_path.is_file()
    assert _home_snapshot(fake_tree) == before
    assert snapshot(target) == {}
    rows = models.read_manifest(cfg.migrate_manifest_path)
    assert rows and all(r.rule == "migrate" for r in rows)
    assert any(r.destination == target / "Documents" / "Code" / "myapp" for r in rows)


def test_requires_manifest(cfg, fake_tree, target, cross_volume):
    before = _home_snapshot(fake_tree)
    with pytest.raises(TidyupError, match="--dry-run"):
        migrate.command(cfg, _args(target))
    assert _home_snapshot(fake_tree) == before


def test_refuses_insufficient_space(cfg, fake_tree, target, cross_volume, monkeypatch):
    migrate.command(cfg, _args(target, dry_run=True))
    before = _home_snapshot(fake_tree)
    monkeypatch.setattr(winutil, "free_space", lambda p: 10)
    assert migrate.command(cfg, _args(target)) == 1
    assert _home_snapshot(fake_tree) == before
    plan = migrate.build_migration(cfg, target)
    with pytest.raises(InsufficientSpaceError, match=r"KB|MB|B"):
        migrate.check_space(plan)


def test_full_migration_atomic_and_timestamps(cfg, fake_tree, target, cross_volume):
    docs = fake_tree.folders["documents"]
    before = snapshot(docs)
    migrate.command(cfg, _args(target, dry_run=True))
    assert migrate.command(cfg, _args(target)) == 0
    after = snapshot(target / "Documents")
    assert after == _movable(before)  # contents and mtimes preserved
    assert (target / "Documents" / "Code" / "myapp" / ".git" / "HEAD").is_file()
    assert not (docs / "Code" / "myapp").exists()
    assert not any("(2)" in k for k in after)


def test_verify_before_remove(cfg, fake_tree, target, cross_volume, monkeypatch):
    order: list[str] = []
    orig_copy, orig_rm = fsops._copy_verified, fsops._remove_verified_source
    monkeypatch.setattr(fsops, "_copy_verified", lambda *a, **k: (orig_copy(*a, **k), order.append("copy"))[0])
    monkeypatch.setattr(fsops, "_remove_verified_source",
                        lambda *a, **k: (order.append("remove"), orig_rm(*a, **k))[1])
    plan = migrate.build_migration(cfg, target)
    one = [r for r in plan.rows if r.source == fake_tree.files["collision_a"]] if "collision_a" in fake_tree.files else []
    rows = one or [r for r in plan.rows if r.source.is_file()][:1]
    migrate.run_migration(cfg, rows, target)
    assert order[:2] == ["copy", "remove"]


def test_corrupt_copy_keeps_source(cfg, fake_tree, target, cross_volume, monkeypatch):
    real = fsops.sha256_file

    def bad(path, **kw):
        h = real(path, **kw)
        return "0" * 64 if Path(path).name.startswith(fsops.PARTIAL_PREFIX) else h

    monkeypatch.setattr(fsops, "sha256_file", bad)
    plan = migrate.build_migration(cfg, target)
    rows = [r for r in plan.rows if r.source.is_file()][:1]
    res = migrate.run_migration(cfg, rows, target)
    assert res.errors == 1 and res.moved == 0
    assert rows[0].source.exists()
    assert not rows[0].destination.exists()


def test_cloud_only_skipped_never_opened(cfg, fake_tree, target, cross_volume, mock_attrs, open_spy):
    cfg2 = dataclasses.replace(cfg, migrate={**cfg.migrate, "include_onedrive": True,
                                              "folders": ["onedrive"]})
    plan = migrate.build_migration(cfg2, target)
    skips = [r for r in plan.rows if r.action == Action.SKIP]
    assert {r.source.name for r in skips} == {p.name for p in fake_tree.cloud_only}
    assert all("cloud-only" in r.notes for r in skips)
    migrate.run_migration(cfg2, plan.rows, target)
    for p in fake_tree.cloud_only:
        assert p.exists()
        assert winutil.norm_key(p) not in open_spy


def test_onedrive_skipped_by_default(cfg, fake_tree, target, cross_volume):
    plan = migrate.build_migration(cfg, target)
    od = fake_tree.folders["onedrive"]
    assert not any(winutil.is_within(r.source, od) for r in plan.rows)


def test_resume_after_crash(cfg, fake_tree, target, cross_volume, monkeypatch):
    docs = fake_tree.folders["documents"]
    before = snapshot(docs)
    migrate.command(cfg, _args(target, dry_run=True))
    real = fsops.move_file
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated crash")
        return real(*a, **k)

    monkeypatch.setattr(fsops, "move_file", flaky)
    with pytest.raises(RuntimeError):
        migrate.command(cfg, _args(target))
    monkeypatch.setattr(fsops, "move_file", real)
    runs = journal.list_runs(cfg.logs_dir)
    assert len(runs) == 1 and not runs[0].finished
    first_id = runs[0].run_id
    assert migrate.command(cfg, _args(target)) == 0
    runs = journal.list_runs(cfg.logs_dir)
    assert len(runs) == 1 and runs[0].run_id == first_id and runs[0].finished
    after = snapshot(target / "Documents")
    assert after == _movable(before)
    assert not any("(2)" in k for k in snapshot(target))


def test_refuses_target_containing_sources(cfg, fake_tree):
    home = fake_tree.folders["home"]
    before = _home_snapshot(fake_tree)
    with pytest.raises(TidyupError):
        migrate.build_migration(cfg, home)
    with pytest.raises(TidyupError):
        migrate.command(cfg, _args(home, dry_run=True))
    with pytest.raises(TidyupError):
        migrate.run_migration(cfg, [], home)
    with pytest.raises(TidyupError):
        migrate.build_migration(cfg, fake_tree.folders["documents"])
    assert _home_snapshot(fake_tree) == before


def test_row_with_dst_equal_src_is_error(cfg, fake_tree, target, cross_volume):
    plan = migrate.build_migration(cfg, target)
    row = [r for r in plan.rows if r.source.is_file()][0]
    bad = dataclasses.replace(row, destination=row.source)
    before = _home_snapshot(fake_tree)
    res = migrate.run_migration(cfg, [bad], target)
    assert res.errors == 1 and res.moved == 0
    assert _home_snapshot(fake_tree) == before


def test_row_outside_scope_is_error(cfg, fake_tree, target, cross_volume, tmp_path):
    plan = migrate.build_migration(cfg, target)
    row = [r for r in plan.rows if r.source.is_file()][0]
    elsewhere = dataclasses.replace(row, destination=tmp_path / "elsewhere" / "x")
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    stray = dataclasses.replace(row, source=outside, destination=target / "outside.txt")
    res = migrate.run_migration(cfg, [elsewhere, stray], target)
    assert res.errors == 2 and res.moved == 0
    assert row.source.exists() and outside.exists()


def test_run_migration_checks_space(cfg, fake_tree, target, cross_volume, monkeypatch):
    plan = migrate.build_migration(cfg, target)
    before = _home_snapshot(fake_tree)
    monkeypatch.setattr(winutil, "free_space", lambda p: 10)
    with pytest.raises(InsufficientSpaceError):
        migrate.run_migration(cfg, plan.rows, target)
    assert _home_snapshot(fake_tree) == before
    assert journal.list_runs(cfg.logs_dir) == []


def test_overlapping_folders_deduped(cfg, fake_tree, target, cross_volume):
    cfg2 = dataclasses.replace(cfg, migrate={**cfg.migrate, "folders": ["{documents}", "{documents}/Code"]})
    plan = migrate.build_migration(cfg2, target)
    srcs = [winutil.norm_key(r.source) for r in plan.rows]
    assert len(srcs) == len(set(srcs))


def test_resume_partial_tree(cfg, fake_tree, target, cross_volume, monkeypatch):
    src = fake_tree.files["git_project"]
    rel = src.relative_to(fake_tree.folders["documents"])
    before = snapshot(src)
    plan = migrate.build_migration(cfg, target)
    rows = [r for r in plan.rows if r.source == src]
    real = fsops._copy_verified
    n = {"c": 0}

    def flaky(*a, **k):
        n["c"] += 1
        if n["c"] == 3:
            raise KeyboardInterrupt
        return real(*a, **k)

    monkeypatch.setattr(fsops, "_copy_verified", flaky)
    with pytest.raises(KeyboardInterrupt):
        migrate.run_migration(cfg, rows, target)
    assert src.exists()
    monkeypatch.setattr(fsops, "_copy_verified", real)
    res = migrate.run_migration(cfg, rows, target)
    assert res.moved == 1 and res.errors == 0
    assert snapshot(target / "Documents" / rel) == before
    assert not (target / "Documents" / (str(rel) + " (2)")).exists()


def test_undo_restores_sources(cfg, fake_tree, target, cross_volume):
    from tidyup import undo

    before = _home_snapshot(fake_tree)
    migrate.command(cfg, _args(target, dry_run=True))
    migrate.command(cfg, _args(target))
    assert _home_snapshot(fake_tree) != before
    run = journal.list_runs(cfg.logs_dir)[0]
    assert run.kind == "migrate"
    undo.undo_run(cfg, run)
    assert _home_snapshot(fake_tree) == before


def test_missing_target(cfg, tmp_path):
    with pytest.raises(TidyupError):
        migrate.build_migration(cfg, tmp_path / "nope")


def test_human_units_in_error(cfg, target):
    plan = migrate.MigratePlan(target, [], 5 * 1024**3, 1024**2)
    with pytest.raises(InsufficientSpaceError) as ei:
        migrate.check_space(plan)
    assert "GB" in str(ei.value) and "MB" in str(ei.value)
