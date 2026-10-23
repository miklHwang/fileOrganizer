from __future__ import annotations

import hashlib
import os
import shutil

import pytest

from tidyup import fsops, journal as jr, winutil
from tidyup.errors import CloudOnlyError, FileLockedError, UnsafePathError, VerifyError


def w(p, data=b"hello"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture
def jnl(tmp_path):
    j = jr.Journal.create(tmp_path / "logs", "apply", {})
    yield j
    j.close()


def ops(j):
    j.close()
    return jr.read_run(j._path if hasattr(j, "_path") else j.path).ops


def test_unique_path_suffixes(tmp_path):
    a = w(tmp_path / "a.txt")
    assert fsops.unique_path(a) == tmp_path / "a (2).txt"
    w(tmp_path / "a (2).txt")
    assert fsops.unique_path(a) == tmp_path / "a (3).txt"
    res: set[str] = set()
    n = tmp_path / "new.txt"
    assert fsops.unique_path(n, res) == n
    assert fsops.unique_path(n, res) == tmp_path / "new (2).txt"
    assert fsops.unique_path(tmp_path / "NEW.TXT", res).name.lower() != "new.txt" or os.name != "nt"
    d = tmp_path / "dir.v1"
    d.mkdir()
    assert fsops.unique_path(d) == tmp_path / "dir.v1 (2)"
    assert fsops.reserve_key(tmp_path / "X") == fsops.reserve_key(tmp_path / "x") or os.name != "nt"


def test_never_overwrites_existing(tmp_path):
    src = w(tmp_path / "s" / "f.txt", b"src")
    dst = tmp_path / "d" / "f.txt"
    dst_planned = fsops.unique_path(dst)
    assert dst_planned == dst
    w(dst, b"other")  # appears between plan and move
    r = fsops.move_file(src, dst)
    assert r.dst == tmp_path / "d" / "f (2).txt"
    assert dst.read_bytes() == b"other"
    assert r.dst.read_bytes() == b"src"


def test_same_volume_move_and_journal(tmp_path, jnl):
    src = w(tmp_path / "s" / "f.txt")
    r = fsops.move_file(src, tmp_path / "x" / "y" / "f.txt", journal=jnl, row_id="7")
    assert not src.exists() and r.dst.read_bytes() == b"hello"
    assert not r.cross_volume and r.sha256 is None and r.size == 5
    o = ops(jnl)
    types = [x["type"] for x in o]
    assert types.count("mkdir") == 2 and types[-1] == "move"
    assert o[-1]["row_id"] == "7"


def test_cross_volume_verifies_before_remove(tmp_path, cross_volume, monkeypatch, jnl):
    src = w(tmp_path / "s" / "f.bin", b"abc" * 1000)
    calls = []
    real_sha, real_unlink = fsops.sha256_file, os.unlink
    monkeypatch.setattr(fsops, "sha256_file",
                        lambda p, **k: (calls.append(("hash", p.name)), real_sha(p, **k))[1])
    monkeypatch.setattr(os, "unlink", lambda p: (calls.append(("unlink", os.path.basename(p))), real_unlink(p))[1])
    r = fsops.move_file(src, tmp_path / "d" / "f.bin", journal=jnl)
    assert r.cross_volume and r.sha256 == hashlib.sha256(b"abc" * 1000).hexdigest()
    assert r.dst.read_bytes() == b"abc" * 1000 and not src.exists()
    assert calls.index(("hash", fsops.PARTIAL_PREFIX + "f.bin")) < calls.index(("unlink", "f.bin"))
    assert not list((tmp_path / "d").glob(fsops.PARTIAL_PREFIX + "*"))
    assert ops(jnl)[-1]["cross_volume"] is True


def test_corrupt_copy_keeps_source(tmp_path, cross_volume, monkeypatch, jnl):
    src = w(tmp_path / "s" / "f.bin", b"good data")

    def bad_copy(a, b, **k):
        with open(b, "wb") as fh:
            fh.write(b"bad")

    monkeypatch.setattr(shutil, "copy2", bad_copy)
    with pytest.raises(VerifyError):
        fsops.move_file(src, tmp_path / "d" / "f.bin", journal=jnl)
    assert src.read_bytes() == b"good data"
    assert sorted(p.name for p in (tmp_path / "d").iterdir()) == []
    assert all(x["type"] != "move" for x in ops(jnl))


def test_source_changed_during_copy(tmp_path, cross_volume, monkeypatch):
    src = w(tmp_path / "s" / "f.bin", b"data")
    real = shutil.copy2

    def copy_then_touch(a, b, **k):
        real(a, b, **k)
        with open(a, "ab") as fh:
            fh.write(b"more")

    monkeypatch.setattr(shutil, "copy2", copy_then_touch)
    with pytest.raises(VerifyError):
        fsops.move_file(src, tmp_path / "d" / "f.bin")
    assert src.exists() and (tmp_path / "d" / "f.bin").exists()


@pytest.mark.parametrize("cross", [False, True])
def test_mtime_preserved_same_and_cross_volume(tmp_path, monkeypatch, cross):
    if cross:
        monkeypatch.setattr(winutil, "same_volume", lambda a, b: False)
    src = w(tmp_path / "s" / "f.txt")
    os.utime(src, ns=(1_500_000_000_000_000_000, 1_500_000_000_000_000_000))
    r = fsops.move_file(src, tmp_path / "d" / "f.txt")
    assert abs(r.dst.stat().st_mtime_ns - 1_500_000_000_000_000_000) < 2_000_000_000


def test_cross_volume_cloud_only_refused(tmp_path, cross_volume, open_spy):
    src = w(tmp_path / "s" / "f.txt")
    open_spy.clear()
    with pytest.raises(CloudOnlyError):
        fsops.move_file(src, tmp_path / "d" / "f.txt", is_cloud_only=True)
    assert src.exists() and not (tmp_path / "d" / "f.txt").exists()
    assert winutil.norm_key(src) not in open_spy


def test_cloud_only_same_volume_allowed(tmp_path):
    src = w(tmp_path / "s" / "f.txt")
    r = fsops.move_file(src, tmp_path / "d" / "f.txt", is_cloud_only=True)
    assert r.dst.exists()


def test_move_tree_same_volume(tmp_path, jnl):
    proj = tmp_path / "s" / "proj"
    w(proj / ".git" / "HEAD", b"ref")
    w(proj / "a.txt")
    r = fsops.move_tree(proj, tmp_path / "d" / "proj", journal=jnl)
    assert (tmp_path / "d" / "proj" / ".git" / "HEAD").read_bytes() == b"ref"
    assert not proj.exists() and r.is_dir and r.size == 8
    assert ops(jnl)[-1]["is_dir"] is True


def test_move_tree_cross_volume(tmp_path, cross_volume, jnl):
    proj = tmp_path / "s" / "proj"
    w(proj / ".git" / "HEAD", b"ref")
    w(proj / ".git" / "objects" / "ab", b"obj")
    w(proj / "a.txt")
    (proj / "empty").mkdir()
    os.utime(proj / "a.txt", ns=(1_500_000_000_000_000_000,) * 2)
    r = fsops.move_tree(proj, tmp_path / "d" / "proj", journal=jnl)
    d = tmp_path / "d" / "proj"
    assert r.cross_volume and r.size == 11 and r.sha256 is None
    assert (d / ".git" / "HEAD").read_bytes() == b"ref" and (d / "empty").is_dir()
    assert abs((d / "a.txt").stat().st_mtime_ns - 1_500_000_000_000_000_000) < 2_000_000_000
    assert not proj.exists()
    assert not [p for p in d.rglob("*") if p.name.startswith(fsops.PARTIAL_PREFIX)]
    assert sum(1 for x in ops(jnl) if x["type"] == "move") == 1


def test_move_tree_cross_volume_cloud_only_copies_nothing(tmp_path, cross_volume, monkeypatch):
    proj = tmp_path / "s" / "proj"
    w(proj / "a.txt")
    cloud = w(proj / "b.txt")
    monkeypatch.setattr(winutil, "is_cloud_only", lambda p: winutil.norm_key(p) == winutil.norm_key(cloud))
    with pytest.raises(CloudOnlyError):
        fsops.move_tree(proj, tmp_path / "d" / "proj")
    assert not (tmp_path / "d").exists() or not list((tmp_path / "d").rglob("*"))
    assert (proj / "a.txt").exists()


def test_move_tree_verify_failure_keeps_everything(tmp_path, cross_volume, monkeypatch):
    proj = tmp_path / "s" / "proj"
    w(proj / "a.txt", b"aaa")
    w(proj / "b.txt", b"bbb")
    real = shutil.copy2
    n = {"c": 0}

    def flaky(a, b, **k):
        n["c"] += 1
        if n["c"] == 2:
            with open(b, "wb") as fh:
                fh.write(b"x")
        else:
            real(a, b, **k)

    monkeypatch.setattr(shutil, "copy2", flaky)
    with pytest.raises(VerifyError):
        fsops.move_tree(proj, tmp_path / "d" / "proj")
    assert (proj / "a.txt").exists() and (proj / "b.txt").exists()


def test_adopt_identical(tmp_path, jnl):
    src = w(tmp_path / "s" / "f.txt", b"same")
    dst = w(tmp_path / "d" / "f.txt", b"same")
    r = fsops.move_file(src, dst, adopt_identical=True, journal=jnl)
    assert r.dst == dst and not src.exists() and sorted(os.listdir(tmp_path / "d")) == ["f.txt"]
    src2 = w(tmp_path / "s" / "g.txt", b"diff")
    w(tmp_path / "d" / "g.txt", b"orig")
    r2 = fsops.move_file(src2, tmp_path / "d" / "g.txt", adopt_identical=True)
    assert r2.dst.name == "g (2).txt"


def test_adopt_identical_tree_resume(tmp_path, cross_volume):
    proj = tmp_path / "s" / "proj"
    w(proj / "a.txt", b"aaa")
    w(proj / "sub" / "b.txt", b"bbb")
    w(tmp_path / "d" / "proj" / "a.txt", b"aaa")
    (tmp_path / "d" / "proj" / f"{fsops.PARTIAL_PREFIX}b.txt").write_bytes(b"junk")
    os.makedirs(tmp_path / "d" / "proj" / "sub")
    w(tmp_path / "d" / "proj" / "sub" / f"{fsops.PARTIAL_PREFIX}b.txt", b"junk")
    r = fsops.move_tree(proj, tmp_path / "d" / "proj", adopt_identical=True)
    assert r.dst == tmp_path / "d" / "proj" and not proj.exists()
    assert (r.dst / "sub" / "b.txt").read_bytes() == b"bbb"
    assert (r.dst / "a.txt").read_bytes() == b"aaa"


def test_uses_long_path(tmp_path, monkeypatch, cross_volume):
    seen = []
    real = winutil.long_path
    monkeypatch.setattr(winutil, "long_path", lambda p: (seen.append(os.fspath(p)), real(p))[1])
    src = w(tmp_path / "s" / "f.txt")
    fsops.move_file(src, tmp_path / "d" / "f.txt")
    assert any(s.endswith("f.txt") for s in seen)
    assert any(s.startswith(fsops.PARTIAL_PREFIX) or fsops.PARTIAL_PREFIX in s for s in seen)


def test_lock_error_to_file_locked(tmp_path, monkeypatch):
    src = w(tmp_path / "s" / "f.txt")

    def boom(a, b):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(FileLockedError):
        fsops.move_file(src, tmp_path / "d" / "f.txt")
    assert src.exists()


def test_sha256_lock_error(tmp_path, monkeypatch):
    src = w(tmp_path / "f.txt")
    import builtins
    real = builtins.open

    def deny(p, *a, **k):
        if str(p).endswith("f.txt"):
            raise PermissionError(13, "denied")
        return real(p, *a, **k)

    monkeypatch.setattr(builtins, "open", deny)
    with pytest.raises(FileLockedError):
        fsops.sha256_file(src)


def test_sha256_refuses_cloud_only(tmp_path, open_spy):
    src = w(tmp_path / "f.txt")
    open_spy.clear()
    with pytest.raises(CloudOnlyError):
        fsops.sha256_file(src, is_cloud_only=True)
    assert open_spy == []


def test_refuses_protected(tmp_path):
    src = w(tmp_path / "s" / "f.txt")
    with pytest.raises(UnsafePathError):
        fsops.move_file(src, tmp_path / "AppData" / "f.txt")
    prot = w(tmp_path / "ProgramData" / "f.txt")
    with pytest.raises(UnsafePathError):
        fsops.move_file(prot, tmp_path / "d" / "f.txt")
    with pytest.raises(UnsafePathError):
        fsops.trash(prot)
    with pytest.raises(UnsafePathError):
        fsops.move_tree(tmp_path / "ProgramData", tmp_path / "d" / "pd")
    assert src.exists() and prot.exists()


def test_refuses_hidden(tmp_path):
    src = w(tmp_path / "s" / ".secret")
    with pytest.raises(UnsafePathError):
        fsops.move_file(src, tmp_path / "d" / ".secret")


def test_ensure_dir_journals_created(tmp_path, jnl):
    created = fsops.ensure_dir(tmp_path / "a" / "b" / "c", jnl)
    assert created == [tmp_path / "a", tmp_path / "a" / "b", tmp_path / "a" / "b" / "c"]
    assert [x["path"] for x in ops(jnl) if x["type"] == "mkdir"] == [str(p) for p in created]
    assert fsops.ensure_dir(tmp_path / "a" / "b") == []


def test_ensure_dir_refuses_file(tmp_path):
    f = w(tmp_path / "f")
    with pytest.raises(UnsafePathError):
        fsops.ensure_dir(f / "sub")


def test_remove_empty_dir(tmp_path):
    d = tmp_path / "d"
    w(d / "f")
    assert fsops.remove_empty_dir(d) is False and (d / "f").exists()
    assert fsops.remove_empty_dir(tmp_path / "missing") is False
    (d / "f").unlink()
    assert fsops.remove_empty_dir(d) is True


def test_dry_run_touches_nothing(tmp_path, cross_volume, jnl):
    src = w(tmp_path / "s" / "f.txt")
    w(tmp_path / "d" / "f.txt", b"x")
    r = fsops.move_file(src, tmp_path / "d" / "f.txt", journal=jnl, dry_run=True)
    assert r.dst.name == "f (2).txt" and src.exists() and not r.dst.exists()
    proj = tmp_path / "s" / "proj"
    w(proj / "a")
    r2 = fsops.move_tree(proj, tmp_path / "new" / "proj", dry_run=True)
    assert r2.is_dir and proj.exists() and not (tmp_path / "new").exists()
    assert fsops.ensure_dir(tmp_path / "q" / "r", jnl, dry_run=True) == [tmp_path / "q", tmp_path / "q" / "r"]
    fsops.trash(src, journal=jnl, dry_run=True)
    assert not (tmp_path / "q").exists() and src.exists()
    assert ops(jnl) == [] or all(x["type"] not in ("move", "mkdir", "trash") for x in ops(jnl))


def test_trash_calls_recycle_bin(tmp_path, monkeypatch, jnl):
    f = w(tmp_path / "f.txt")
    called = []
    monkeypatch.setattr(winutil, "send_to_recycle_bin", lambda p: called.append(p))
    fsops.trash(f, journal=jnl, row_id="3")
    assert called == [f]
    assert ops(jnl)[-1]["type"] == "trash"


def test_adopt_identical_dst_is_directory(tmp_path):
    src = w(tmp_path / "s" / "f", b"abcd")
    (tmp_path / "d" / "f").mkdir(parents=True)
    r = fsops.move_file(src, tmp_path / "d" / "f", adopt_identical=True)
    assert r.dst.name == "f (2)" and r.dst.read_bytes() == b"abcd" and not src.exists()


def test_adopt_identical_dst_is_symlink(tmp_path):
    src = w(tmp_path / "s" / "f.txt", b"abcd")
    other = w(tmp_path / "o.txt", b"abcd")
    (tmp_path / "d").mkdir()
    try:
        os.symlink(other, tmp_path / "d" / "f.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    r = fsops.move_file(src, tmp_path / "d" / "f.txt", adopt_identical=True)
    assert r.dst.name == "f (2).txt" and other.read_bytes() == b"abcd"


def test_move_tree_cloud_only_existing_target_not_hashed(tmp_path, cross_volume, monkeypatch):
    proj = tmp_path / "s" / "proj"
    w(proj / "a.txt", b"aaa")
    w(proj / "b.txt", b"bbb")
    cloud = w(tmp_path / "d" / "proj" / "a.txt", b"aaa")
    real = winutil.is_cloud_only_attrs
    real_attrs = winutil.get_file_attributes
    monkeypatch.setattr(winutil, "get_file_attributes",
                        lambda p: 0x400000 if winutil.norm_key(p) == winutil.norm_key(cloud) else real_attrs(p))
    monkeypatch.setattr(winutil, "is_cloud_only_attrs", lambda a: bool(a & 0x400000) or real(a))
    r = fsops.move_tree(proj, tmp_path / "d" / "proj", adopt_identical=True)
    assert (r.dst / "a (2).txt").read_bytes() == b"aaa" and cloud.read_bytes() == b"aaa"
    assert not proj.exists()


def test_adopt_dest_changed_keeps_source(tmp_path, monkeypatch):
    src = w(tmp_path / "s" / "f.txt", b"same")
    dst = w(tmp_path / "d" / "f.txt", b"same")
    real = fsops.sha256_file
    calls = []

    def hash_then_change(p, **k):
        h = real(p, **k)
        if p == src:
            calls.append(1)
            if len(calls) == 2:
                dst.write_bytes(b"changed!")
        return h

    monkeypatch.setattr(fsops, "sha256_file", hash_then_change)
    with pytest.raises(VerifyError):
        fsops.move_file(src, dst, adopt_identical=True)
    assert src.read_bytes() == b"same"


def test_copy_dest_vanishes_keeps_source(tmp_path, cross_volume, monkeypatch):
    src = w(tmp_path / "s" / "f.txt", b"data")
    real = fsops._copy_verified

    def copy_then_delete(s, final, h):
        real(s, final, h)
        os.unlink(final)

    monkeypatch.setattr(fsops, "_copy_verified", copy_then_delete)
    with pytest.raises(VerifyError):
        fsops.move_file(src, tmp_path / "d" / "f.txt")
    assert src.read_bytes() == b"data"


def test_move_tree_dest_changed_before_source_removal(tmp_path, cross_volume, monkeypatch):
    proj = tmp_path / "s" / "proj"
    w(proj / "a.txt", b"aaa")
    w(proj / "b.txt", b"bbb")
    real = fsops._copy_verified

    def copy(s, final, h):
        real(s, final, h)
        if s.name == "b.txt":
            (final.parent / "a.txt").unlink()

    monkeypatch.setattr(fsops, "_copy_verified", copy)
    with pytest.raises(VerifyError):
        fsops.move_tree(proj, tmp_path / "d" / "proj")
    assert (proj / "a.txt").exists() and (proj / "b.txt").exists()


def test_partial_name_within_limit(tmp_path):
    name = "x" * 250 + ".txt"
    pn = fsops._partial_name(name)
    assert len(pn) <= 255 and pn.startswith(fsops.PARTIAL_PREFIX) and pn.endswith(".txt")
    assert fsops._partial_name("a.txt") == fsops.PARTIAL_PREFIX + "a.txt"


def test_cross_volume_long_name(tmp_path, cross_volume):
    name = "y" * 245 + ".txt"
    src = w(tmp_path / "s" / name, b"data")
    r = fsops.move_file(src, tmp_path / "d" / name)
    assert r.dst.read_bytes() == b"data" and not src.exists()
