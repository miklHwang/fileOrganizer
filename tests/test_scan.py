from __future__ import annotations

import argparse
from collections import Counter

from fixtures.fake_tree import snapshot

from tidyup import fsops, models, scan, winutil
from tidyup.errors import FileLockedError
from tidyup.models import EntryType


def _scan(cfg):
    return scan.scan_sources(cfg, show_progress=False)


def _by_path(res):
    return {winutil.norm_key(r.path): r for r in res.records}


def test_expected_files_present(cfg, fake_tree, mock_attrs):
    idx = _by_path(_scan(cfg))
    for h in ("invoice_pdf", "photo_exif", "screenshot_capture", "video", "budget_xlsx",
              "long_path_file", "cloud_pdf", "onedrive_letter", "installer_old"):
        assert winutil.norm_key(fake_tree.files[h]) in idx, h


def test_protected_hidden_excluded_not_inventoried(cfg, fake_tree, mock_attrs):
    idx = _by_path(_scan(cfg))
    for p in fake_tree.hidden + fake_tree.protected + fake_tree.excluded:
        assert winutil.norm_key(p) not in idx, p
    res = _scan(cfg)
    assert res.skipped.get("hidden", 0) >= 2
    assert res.skipped.get("protected", 0) >= 3
    assert res.skipped.get("excluded", 0) >= 2


def test_atomic_folders_single_row(cfg, fake_tree, mock_attrs):
    res = _scan(cfg)
    atomic = [r for r in res.records if r.entry_type == EntryType.ATOMIC_FOLDER]
    assert {winutil.norm_key(r.path) for r in atomic} == {winutil.norm_key(p) for p in fake_tree.atomic_dirs}
    assert len(atomic) == 3
    for a in atomic:
        assert a.atomic_root == a.path and a.extension == ""
        assert not any(winutil.is_within(r.path, a.path) and r is not a for r in res.records)
    git = next(r for r in atomic if r.path == fake_tree.files["git_project"])
    files = [p for p in fake_tree.all_files() if winutil.is_within(p, git.path)]
    assert git.file_count == len(files)
    assert git.size == sum(p.stat().st_size for p in files)


def test_cloud_only_marked_not_hashed(cfg, fake_tree, mock_attrs, open_spy):
    res = _scan(cfg)
    idx = _by_path(res)
    for p in fake_tree.cloud_only:
        r = idx[winutil.norm_key(p)]
        assert r.is_cloud_only
        assert r.sha256 is None and r.image_width is None and r.phash is None and r.camera_make is None
    cloud = {winutil.norm_key(p) for p in fake_tree.cloud_only}
    assert not cloud & set(open_spy)


def test_sha_only_for_size_collisions(cfg, fake_tree, mock_attrs):
    res = _scan(cfg)
    sizes = Counter(r.size for r in res.records if r.entry_type == EntryType.FILE and not r.is_cloud_only)
    for r in res.records:
        if r.entry_type != EntryType.FILE:
            continue
        if r.sha256 is not None:
            assert sizes[r.size] >= 2 and not r.is_cloud_only
        elif not r.is_cloud_only:
            assert sizes[r.size] == 1
    idx = _by_path(res)
    a, b = fake_tree.duplicate_groups[1]
    assert idx[winutil.norm_key(a)].sha256 == idx[winutil.norm_key(b)].sha256 is not None


def test_image_info_filled(cfg, fake_tree, mock_attrs):
    idx = _by_path(_scan(cfg))
    r = idx[winutil.norm_key(fake_tree.files["photo_exif"])]
    assert r.camera_make == "Canon" and r.exif_datetime is not None and r.phash
    s = idx[winutil.norm_key(fake_tree.files["screenshot_desktop"])]
    assert (s.image_width, s.image_height) == (1920, 1080)
    assert idx[winutil.norm_key(fake_tree.files["invoice_pdf"])].image_width is None


def test_kfm_no_double_rows(fake_tree, mock_attrs):
    from tidyup import config

    ov = fake_tree.config_overrides()
    ov["folders"]["documents"] = str(fake_tree.folders["onedrive"] / "Documents")
    (fake_tree.folders["onedrive"] / "Documents").mkdir()
    (fake_tree.folders["onedrive"] / "Documents" / "a.txt").write_text("a")
    cfg = config.load_config(None, overrides=ov)
    res = _scan(cfg)
    keys = [winutil.norm_key(r.path) for r in res.records]
    assert len(keys) == len(set(keys))
    assert sum(r.path.name == "a.txt" for r in res.records) == 1


def test_locked_file_continues(cfg, fake_tree, mock_attrs, monkeypatch):
    target = fake_tree.files["budget_xlsx"]
    real = fsops.sha256_file

    def fake(path, *, is_cloud_only=False):
        if path == target:
            raise FileLockedError("in use")
        return real(path, is_cloud_only=is_cloud_only)

    monkeypatch.setattr(fsops, "sha256_file", fake)
    res = _scan(cfg)
    idx = _by_path(res)
    r = idx[winutil.norm_key(target)]
    assert r.error.startswith("locked:") and r.sha256 is None
    assert res.skipped["locked"] == 1
    assert idx[winutil.norm_key(fake_tree.files["budget_copy"])].sha256


def test_summary_counts(cfg, fake_tree, mock_attrs):
    res = _scan(cfg)
    s = scan.summarize(res.records)
    assert s["total"][0] == len(res.records)
    assert s["total"][1] == sum(r.size for r in res.records)
    assert s["cloud_only"] == 3
    assert s["atomic_folders"] == 3
    assert sum(n for n, _ in s["by_type"].values()) == len(res.records)
    assert sum(n for n, _ in s["by_source"].values()) == len(res.records)
    assert s["by_type"]["PDFs"][0] >= 5


def test_command_writes_inventory_and_dry_run(cfg, fake_tree, mock_attrs):
    before = snapshot(fake_tree.root)
    assert scan.command(cfg, argparse.Namespace(dry_run=True)) == 0
    assert not cfg.inventory_path.exists()
    assert snapshot(fake_tree.root) == before
    assert scan.command(cfg, argparse.Namespace(dry_run=False)) == 0
    assert cfg.inventory_path.exists()
    recs = models.read_inventory(cfg.inventory_path)
    assert len(recs) == len(_scan(cfg).records)
    after = snapshot(fake_tree.root)
    after.pop("tidyup_data/inventory.csv")
    after = {k: v for k, v in after.items() if not k.startswith("tidyup_data")}
    before = {k: v for k, v in before.items() if not k.startswith("tidyup_data")}
    assert after == before


def test_walk_sources_stat_only(cfg, fake_tree, mock_attrs, open_spy):
    recs = list(scan.walk_sources(cfg))
    assert recs and all(r.sha256 is None and r.image_width is None for r in recs)
    assert not [p for p in open_spy if p.endswith((".jpg", ".png", ".pdf"))]
