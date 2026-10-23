from __future__ import annotations

import errno
import os
import sys
import types
from pathlib import Path

import pytest

from tidyup import winutil
from tidyup.errors import CloudOnlyError


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(winutil, "is_windows", lambda: True)


def test_long_path_non_windows(monkeypatch):
    monkeypatch.setattr(winutil, "is_windows", lambda: False)
    assert winutil.long_path("C:\\" + "a" * 300) == "C:\\" + "a" * 300


def test_long_path_short_unchanged(win):
    assert winutil.long_path("C:\\x\\y.txt") == "C:\\x\\y.txt"


def test_long_path_drive_and_roundtrip(win):
    p = "C:\\" + "\\".join(["d" * 50] * 6) + "\\f.txt"
    lp = winutil.long_path(p)
    assert lp == "\\\\?\\" + p
    assert winutil.long_path(lp) == lp
    assert winutil.strip_long_path(lp) == p


def test_long_path_unc(win):
    p = "\\\\srv\\share\\" + "\\".join(["d" * 50] * 6)
    lp = winutil.long_path(p)
    assert lp == "\\\\?\\UNC\\srv\\share\\" + "\\".join(["d" * 50] * 6)
    assert winutil.strip_long_path(lp) == p


@pytest.mark.parametrize("bit", [0x400000, 0x40000, 0x1000])
def test_cloud_mask(bit):
    assert winutil.is_cloud_only_attrs(bit)
    assert winutil.is_cloud_only_attrs(bit | 0x20)
    assert not winutil.is_cloud_only_attrs(0x20)


def test_assert_local(monkeypatch, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    winutil.assert_local(f)
    with pytest.raises(CloudOnlyError):
        winutil.assert_local(f, known_cloud_only=True)
    monkeypatch.setattr(winutil, "get_file_attributes", lambda p, st=None: 0x400000)
    with pytest.raises(CloudOnlyError):
        winutil.assert_local(f)
    assert winutil.is_cloud_only(f)


def test_is_cloud_only_missing(tmp_path):
    assert winutil.is_cloud_only(tmp_path / "nope") is False


def test_get_file_attributes_missing(tmp_path):
    with pytest.raises(OSError):
        winutil.get_file_attributes(tmp_path / "nope")


def test_hidden_or_system(monkeypatch):
    assert winutil.is_hidden_or_system("a", 0x2)
    assert winutil.is_hidden_or_system("a", 0x4)
    assert not winutil.is_hidden_or_system("a", 0x20)
    monkeypatch.setattr(winutil, "is_windows", lambda: False)
    assert winutil.is_hidden_or_system(".x", 0)
    monkeypatch.setattr(winutil, "is_windows", lambda: True)
    assert winutil.is_hidden_or_system(".x", 0)
    assert not winutil.is_hidden_or_system("x", 0)


def test_is_lock_error():
    assert winutil.is_lock_error(PermissionError())
    e = OSError(); e.winerror = 32
    assert winutil.is_lock_error(e)
    assert winutil.is_lock_error(OSError(errno.EBUSY, "busy"))
    assert not winutil.is_lock_error(OSError(errno.ENOENT, "missing"))


def test_norm_and_within(tmp_path):
    a = tmp_path / "a"
    assert winutil.is_within(a / "b" / "c", a)
    assert winutil.is_within(a, a)
    assert not winutil.is_within(tmp_path / "ab", a)
    assert not winutil.is_within(tmp_path, a)
    assert winutil.norm_key(a / ".." / "a") == winutil.norm_key(a)


def test_same_volume_and_free_space(tmp_path):
    assert winutil.same_volume(tmp_path, tmp_path / "missing" / "deep")
    assert winutil.free_space(tmp_path / "missing") > 0


def test_known_folders_non_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(winutil, "is_windows", lambda: False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "Documents").mkdir()
    kf = winutil.known_folders()
    assert set(kf) == {"home", "documents", "desktop", "downloads", "pictures", "videos", "onedrive"}
    assert kf["documents"] == tmp_path / "Documents"
    assert kf["desktop"] is None and kf["onedrive"] is None


def test_known_folders_windows_fake_winreg(monkeypatch, tmp_path):
    docs = tmp_path / "od" / "Documents"
    docs.mkdir(parents=True)
    monkeypatch.setenv("FAKEROOT", str(tmp_path / "od"))
    values = {"Personal": "%FAKEROOT%/Documents"}

    class Key:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=1,
        OpenKey=lambda h, p: Key(),
        QueryValueEx=lambda k, n: (values[n], 1) if n in values else (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(winutil, "is_windows", lambda: True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    kf = winutil.known_folders()
    assert kf["documents"] == docs
    assert kf["downloads"] is None


def test_onedrive_root_env(monkeypatch, tmp_path):
    od = tmp_path / "OD"
    od.mkdir()
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.setenv("OneDriveConsumer", str(od))
    assert winutil.onedrive_root() == od


def test_file_times(tmp_path):
    f = tmp_path / "a"
    f.write_text("x")
    c, m = winutil.file_times(f.stat())
    assert c.year >= 2000 and m.year >= 2000


def test_open_path_missing_raises(tmp_path):
    from tidyup.errors import TidyupError

    with pytest.raises(TidyupError):
        winutil.open_path(tmp_path / "nope.txt")


def test_open_path_non_windows_uses_webbrowser(tmp_path, monkeypatch):
    import webbrowser

    f = tmp_path / "a.txt"
    f.write_text("x")
    calls = []
    monkeypatch.setattr(winutil, "is_windows", lambda: False)
    monkeypatch.setattr(webbrowser, "open", lambda url: calls.append(url))
    winutil.open_path(f)
    assert calls == [f.resolve().as_uri()]


def test_open_path_windows_uses_startfile(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("x")
    calls = []
    monkeypatch.setattr(winutil, "is_windows", lambda: True)
    monkeypatch.setattr(os, "startfile", lambda p: calls.append(p), raising=False)
    winutil.open_path(f)
    assert calls == [str(f)]
