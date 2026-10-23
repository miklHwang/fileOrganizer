from __future__ import annotations

from pathlib import Path

import pytest

from tidyup import fsops, safety, winutil
from tidyup.errors import UnsafePathError


def test_is_protected(tmp_path, monkeypatch):
    monkeypatch.delenv("SystemRoot", raising=False)
    assert safety.is_protected(tmp_path / "x" / "AppData" / "a.txt")
    assert safety.is_protected(tmp_path / "$Recycle.Bin" / "a")
    assert safety.is_protected(tmp_path / "Program Files (x86)" / "a")
    assert safety.is_protected(tmp_path / "ProgramData")
    assert safety.is_protected(tmp_path / "System Volume Information")
    assert not safety.is_protected(tmp_path / "Documents" / "a.txt")


def test_windows_dir_and_systemroot(tmp_path, monkeypatch):
    monkeypatch.delenv("SystemRoot", raising=False)
    assert safety.is_protected(Path("/Windows/System32/x.dll"))
    assert not safety.is_protected(tmp_path / "Windows")
    monkeypatch.setenv("SystemRoot", str(tmp_path / "sysroot"))
    assert safety.is_protected(tmp_path / "sysroot" / "a")


def test_fixture_protected_and_hidden(fake_tree, mock_attrs, monkeypatch):
    monkeypatch.delenv("SystemRoot", raising=False)
    for p in fake_tree.protected:
        assert safety.is_protected(p)
    for p in fake_tree.hidden:
        assert safety.is_hidden_or_system(p)
    assert not safety.is_hidden_or_system(fake_tree.files["invoice_pdf"] if "invoice_pdf" in fake_tree.files
                                          else fake_tree.files["misc"])


def test_hidden_attr_values(tmp_path):
    p = tmp_path / "a.txt"
    assert not safety.is_hidden_or_system(p, 0)
    assert safety.is_hidden_or_system(p, 0x2)
    assert safety.is_hidden_or_system(p, 0x4)
    assert safety.is_hidden_or_system(tmp_path / "missing.txt")  is False


def test_is_excluded(fake_tree, cfg):
    f = fake_tree.files
    assert safety.is_excluded(f["excluded_desktop_ini"], cfg)
    assert safety.is_excluded(f["excluded_crdownload"], cfg)
    assert safety.is_excluded(cfg.work_dir / "inventory.csv", cfg)
    assert safety.is_excluded(Path(cfg.folders["documents"]) / f"{fsops.PARTIAL_PREFIX}abc", cfg)
    assert not safety.is_excluded(f["misc"], cfg)


def test_atomic_dirs(fake_tree, cfg):
    f = fake_tree.files
    for h in ("git_project", "node_project", "app_folder"):
        assert safety.is_atomic_dir(f[h], cfg)
    assert not safety.is_atomic_dir(cfg.folders["documents"], cfg)
    assert not safety.is_atomic_dir(cfg.folders["documents"] / "nonexistent", cfg)
    inner = f["git_project"] / "src" / "main.py"
    assert safety.find_atomic_ancestor(inner, cfg) == f["git_project"]
    assert safety.find_atomic_ancestor(f["git_project"], cfg) is None
    assert safety.find_atomic_ancestor(f["misc"], cfg) is None


def test_check_source(fake_tree, cfg, mock_attrs):
    f = fake_tree.files
    safety.check_source(f["misc"], cfg, is_dir=False)
    safety.check_source(f["git_project"], cfg, is_dir=True)
    with pytest.raises(UnsafePathError):
        safety.check_source(f["git_project"] / "README.md", cfg, is_dir=False)
    with pytest.raises(UnsafePathError):
        safety.check_source(cfg.folders["documents"] / "plain", cfg, is_dir=True)
    with pytest.raises(UnsafePathError):
        safety.check_source(f["hidden_attr"], cfg, is_dir=False)
    with pytest.raises(UnsafePathError):
        safety.check_source(f["protected_appdata"], cfg, is_dir=False)
    with pytest.raises(UnsafePathError):
        safety.check_source(f["excluded_desktop_ini"], cfg, is_dir=False)


def test_check_destination(fake_tree, cfg):
    f = fake_tree.files
    safety.check_destination(cfg.folders["documents"] / "Finance" / "a.pdf", cfg)
    with pytest.raises(UnsafePathError):
        safety.check_destination(f["git_project"] / "new.txt", cfg)
    with pytest.raises(UnsafePathError):
        safety.check_destination(cfg.folders["documents"] / "AppData" / "a", cfg)


def test_assert_not_protected(tmp_path, monkeypatch):
    monkeypatch.delenv("SystemRoot", raising=False)
    ok = tmp_path / "a.txt"
    ok.write_text("x")
    safety.assert_not_protected(ok)
    safety.assert_not_protected(tmp_path / "missing")
    with pytest.raises(UnsafePathError):
        safety.assert_not_protected(tmp_path / "AppData" / "x")
    monkeypatch.setattr(winutil, "get_file_attributes", lambda p, st=None: 0x2)
    with pytest.raises(UnsafePathError):
        safety.assert_not_protected(ok)


def test_trusted_prefixes_default_is_empty():
    import importlib.util

    spec = importlib.util.find_spec("tidyup.safety")
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert fresh.TRUSTED_PREFIXES == ()


def test_trusted_prefix_scopes_name_check(tmp_path, monkeypatch):
    monkeypatch.delenv("SystemRoot", raising=False)
    prefix = tmp_path / "AppData" / "Local" / "Temp" / "base"
    monkeypatch.setattr(safety, "TRUSTED_PREFIXES", (prefix,))
    assert not safety.is_protected(prefix / "home" / "Documents" / "x.txt")
    assert safety.is_protected(prefix / "home" / "AppData" / "x.txt")
    assert safety.is_protected(prefix / "home" / "Program Files" / "x")
    assert safety.is_protected(tmp_path / "AppData" / "other" / "x")


def test_no_prefix_appdata_ancestor_protected(tmp_path, monkeypatch):
    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.setattr(safety, "TRUSTED_PREFIXES", ())
    p = tmp_path / "AppData" / "Local" / "Temp" / "base" / "home" / "Documents" / "x.txt"
    assert safety.is_protected(p)


def test_trusted_prefix_does_not_bypass_systemroot(tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", str(tmp_path / "sysroot"))
    monkeypatch.setattr(safety, "TRUSTED_PREFIXES", (tmp_path,))
    assert safety.is_protected(tmp_path / "sysroot" / "a")


def test_partial_prefix_matches_fsops():
    assert safety.PARTIAL_PREFIX == fsops.PARTIAL_PREFIX
