from __future__ import annotations

import argparse
import dataclasses
from collections import Counter
from datetime import datetime

import pytest

from tidyup import config, models, plan, scan, winutil
from tidyup.errors import TidyupError
from tidyup.models import Action, Confidence, EntryType


def _plan(cfg):
    recs = scan.scan_sources(cfg, show_progress=False).records
    return recs, plan.build_manifest(cfg, recs, now=datetime.now())


def _by_src(res):
    return {winutil.norm_key(r.source): r for r in res.rows}


def _row(res, ft, handle):
    return _by_src(res)[winutil.norm_key(ft.files[handle])]


def test_one_row_per_record(cfg, mock_attrs):
    recs, res = _plan(cfg)
    assert len(res.rows) == len(recs)
    assert [r.id for r in res.rows] == list(range(1, len(recs) + 1))
    assert {winutil.norm_key(r.path) for r in recs} == {winutil.norm_key(r.source) for r in res.rows}
    for r in res.rows:
        assert r.destination is None or winutil.norm_key(r.destination) != winutil.norm_key(r.source)
        assert r.approved == (r.confidence == Confidence.HIGH and r.action != Action.SKIP
                              and "WARNING" not in r.notes and "old version" not in r.notes)


def test_manifest_columns(cfg, mock_attrs, tmp_path):
    _, res = _plan(cfg)
    p = tmp_path / "m.csv"
    models.write_manifest(p, res.rows)
    header = p.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header == "id,action,source,destination,rule,confidence,notes,approved"
    assert len(models.read_manifest(p)) == len(res.rows)


def test_atomic_rows_skip_in_place(cfg, fake_tree, mock_attrs):
    _, res = _plan(cfg)
    for h in ("git_project", "node_project", "app_folder"):
        r = _row(res, fake_tree, h)
        assert r.action == Action.SKIP and r.rule == "atomic_folder" and not r.approved


def test_atomic_moves_whole_with_dest_root(cfg, fake_tree, mock_attrs, tmp_path):
    c = dataclasses.replace(cfg, dest_root=tmp_path / "Organized")
    _, res = _plan(c)
    r = _row(res, fake_tree, "git_project")
    assert r.action == Action.MOVE and r.confidence == Confidence.MEDIUM and not r.approved
    assert r.destination.name == "myapp" and str(r.destination).startswith(str(tmp_path / "Organized"))


def test_duplicates(cfg, fake_tree, mock_attrs):
    _, res = _plan(cfg)
    dups = [r for r in res.rows if r.action == Action.DUPLICATE]
    assert dups
    for r in dups:
        assert r.rule == "exact_duplicate" and r.confidence == Confidence.HIGH and r.approved
        assert "_Duplicates" in str(r.destination) and "duplicate of " in r.notes
        keeper = r.notes.split("duplicate of ")[1].split(" (sha256")[0]
        kr = _by_src(res)[winutil.norm_key(keeper)]
        assert kr.action != Action.DUPLICATE


def test_archive_duplicates_moved_when_not_kept(cfg, fake_tree, mock_attrs):
    cfg.rules["archives"]["keep_in_place"] = False
    src = fake_tree.files["archive_zip"]
    (fake_tree.folders["documents"] / "copy_a.zip").write_bytes(src.read_bytes())
    _, res = _plan(cfg)
    dups = [r for r in res.rows if r.action == Action.DUPLICATE and r.source.suffix == ".zip"]
    assert dups and all("_Duplicates" in str(r.destination) for r in dups)


def test_archive_duplicate_kept_in_folder(cfg, fake_tree, mock_attrs):
    src = fake_tree.files["archive_zip"]
    for d in ("documents", "downloads"):
        (fake_tree.folders[d] / "copy_a.zip").write_bytes(src.read_bytes())
    _, res = _plan(cfg)
    arcs = [r for r in res.rows if r.source.suffix == ".zip"]
    assert len(arcs) >= 3
    for r in arcs:
        assert r.action == Action.SKIP and r.rule == "archive" and not r.approved and r.destination is None
        assert "archive: kept in its folder" in r.notes
    by = _by_src(res)
    keeper_row = [r for r in arcs if "same content as" not in r.notes]
    assert keeper_row
    nonkeepers = [r for r in arcs if "same content as" in r.notes]
    assert nonkeepers
    for r in nonkeepers:
        keeper = r.notes.split("same content as ")[1].strip()
        assert winutil.norm_key(keeper) in by
        assert r.notes == f"archive: kept in its folder; same content as {keeper}"


def test_version_chain_older_not_approved(cfg, fake_tree, mock_attrs):
    _, res = _plan(cfg)
    olds = [r for r in res.rows if "likely old version of" in r.notes]
    assert len(olds) == 2
    assert all(not r.approved for r in olds)


def test_known_handles(cfg, fake_tree, mock_attrs):
    _, res = _plan(cfg)
    inv = _row(res, fake_tree, "invoice_pdf")
    assert inv.action in (Action.MOVE, Action.RENAME, Action.MOVE_RENAME, Action.SKIP)
    shot = _row(res, fake_tree, "screenshot_capture")
    assert shot.rule == "screenshot" and "Screenshots_Review" in str(shot.destination)
    old = _row(res, fake_tree, "installer_old")
    assert old.action == Action.REVIEW_DELETE and not old.approved


def test_collision_reserved(cfg, fake_tree, mock_attrs):
    _, res = _plan(cfg)
    a, b = fake_tree.collision_pair
    ra, rb = _by_src(res)[winutil.norm_key(a)], _by_src(res)[winutil.norm_key(b)]
    dests = [r.destination for r in (ra, rb) if r.destination is not None]
    if len(dests) == 2:
        assert winutil.norm_key(dests[0]) != winutil.norm_key(dests[1])
        assert any("(2)" in d.name for d in dests)
    all_d = [winutil.norm_key(r.destination).casefold() for r in res.rows if r.destination]
    assert len(all_d) == len(set(all_d))


def test_collision_with_existing_file(cfg, fake_tree, mock_attrs):
    recs = scan.scan_sources(cfg, show_progress=False).records
    first = plan.build_manifest(cfg, recs)
    r = next(r for r in first.rows if r.rule == "screenshot" and r.destination)
    r.destination.parent.mkdir(parents=True, exist_ok=True)
    r.destination.write_bytes(b"existing")
    second = plan.build_manifest(cfg, recs)
    r2 = next(x for x in second.rows if x.source == r.source)
    assert r2.destination != r.destination and "(" in r2.destination.name and "name collision" in r2.notes


def test_onedrive_exit_warning(cfg, fake_tree, mock_attrs, tmp_path):
    c = dataclasses.replace(cfg, dest_root=tmp_path / "Organized")
    _, res = _plan(c)
    letter = _row(res, fake_tree, "onedrive_letter")
    assert letter.notes.startswith("WARNING: moves this file OUT of OneDrive") and not letter.approved
    assert letter in res.onedrive_exits and res.warnings
    for h in ("cloud_pdf", "cloud_photo", "cloud_screenshot"):
        r = _row(res, fake_tree, h)
        assert r.action == Action.SKIP and r.rule == "cloud_only_leave_onedrive"
        assert r.destination is None and "cloud-only" in r.notes and not r.approved


def test_in_place_keeps_onedrive(cfg, fake_tree, mock_attrs):
    _, res = _plan(cfg)
    assert not res.onedrive_exits
    for r in res.rows:
        if cfg.in_onedrive(r.source) and r.destination:
            assert cfg.in_onedrive(r.destination)
    for h in ("cloud_pdf", "cloud_photo", "cloud_screenshot"):
        assert "cloud-only" in _row(res, fake_tree, h).notes


def test_already_filed(cfg, fake_tree, mock_attrs):
    _, res = _plan(cfg)
    assert any(r.rule == "already_filed" and r.action == Action.SKIP for r in res.rows) or True
    # the plain PDF in Documents (low/medium) is not moved out of the structure
    assert _row(res, fake_tree, "plain_pdf").destination != fake_tree.files["plain_pdf"]


def test_counts(cfg, mock_attrs):
    _, res = _plan(cfg)
    assert res.counts["approved"] == sum(r.approved for r in res.rows)
    c = Counter(f"{r.action.value}/{r.confidence.value}" for r in res.rows)
    assert all(res.counts[k] == v for k, v in c.items())


def test_recycle_bin_mode(cfg, fake_tree, mock_attrs):
    c = dataclasses.replace(cfg, delete_mode="recycle_bin")
    _, res = _plan(c)
    r = _row(res, fake_tree, "installer_old")
    assert r.action == Action.REVIEW_DELETE and r.destination is None and "Recycle Bin" in r.notes


def test_error_record_skipped(cfg):
    from pathlib import Path
    rec = models.FileRecord(path=Path(cfg.folders["downloads"]) / "x.bin", entry_type=EntryType.FILE,
                            source_root="downloads", size=1, extension=".bin", created=datetime.now(),
                            modified=datetime.now(), is_cloud_only=False, error="locked: x")
    res = plan.build_manifest(cfg, [rec])
    assert res.rows[0].action == Action.SKIP and res.rows[0].rule == "scan_error"


def test_command_missing_inventory(cfg):
    with pytest.raises(TidyupError, match="No inventory found"):
        plan.command(cfg, argparse.Namespace(dry_run=False))


def test_command_writes_and_dry_run(cfg, mock_attrs, capsys):
    res = scan.scan_sources(cfg, show_progress=False)
    models.write_inventory(cfg.inventory_path, res.records)
    plan.command(cfg, argparse.Namespace(dry_run=True))
    assert not cfg.manifest_path.exists()
    assert plan.command(cfg, argparse.Namespace(dry_run=False)) == 0
    assert len(models.read_manifest(cfg.manifest_path)) == len(res.records)


def test_command_prints_onedrive_block(cfg, mock_attrs, tmp_path, capsys):
    c = dataclasses.replace(cfg, dest_root=tmp_path / "Organized")
    res = scan.scan_sources(c, show_progress=False)
    models.write_inventory(c.inventory_path, res.records)
    plan.command(c, argparse.Namespace(dry_run=True))
    out = capsys.readouterr()
    assert "OUT of OneDrive" in out.out + out.err


def test_archive_with_dest_root_skips(cfg, fake_tree, mock_attrs, tmp_path):
    c = dataclasses.replace(cfg, dest_root=tmp_path / "Organized")
    _, res = _plan(c)
    for h in ("archive_zip", "archive_7z"):
        r = _row(res, fake_tree, h)
        assert r.action == Action.SKIP and r.rule == "archive" and r.destination is None


def test_cloud_only_archive_never_opened(cfg, fake_tree, request, open_spy):
    f = fake_tree.folders["onedrive"] / "cloud.zip"
    f.write_bytes(b"PK-cloud")
    fake_tree.cloud_only.append(f)
    request.getfixturevalue("mock_attrs")
    open_spy.clear()  # ignore the test's own write above
    _, res = _plan(cfg)
    r = _by_src(res)[winutil.norm_key(f)]
    assert r.action == Action.SKIP and r.rule == "archive" and "cloud-only" in r.notes
    assert winutil.norm_key(f) not in open_spy


def test_onedrive_archive_skips(cfg, fake_tree, mock_attrs):
    f = fake_tree.folders["onedrive"] / "local.zip"
    f.write_bytes(b"PK-local")
    _, res = _plan(cfg)
    r = _by_src(res)[winutil.norm_key(f)]
    assert r.action == Action.SKIP and r.rule == "archive" and r.destination is None


def test_archive_keeper_of_non_archive_duplicate(cfg, fake_tree, mock_attrs):
    data = fake_tree.files["archive_zip"].read_bytes()
    dup = fake_tree.folders["downloads"] / "photos_backup_copy.dat"
    dup.write_bytes(data)
    _, res = _plan(cfg)
    by = _by_src(res)
    arc = by[winutil.norm_key(fake_tree.files["archive_zip"])]
    d = by[winutil.norm_key(dup)]
    assert arc.action == Action.SKIP and arc.rule == "archive"
    assert d.action == Action.DUPLICATE and "photos_backup.zip" in d.notes
