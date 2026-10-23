"""Safe file operations: the ONLY module that mutates user files.

Rules enforced here: never overwrite (no-clobber rename + unique names), cross-volume moves are
copy -> SHA-256 verify -> remove source, timestamps preserved, long paths supported, journal only after success.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tidyup import safety, winutil
from tidyup.errors import CloudOnlyError, FileLockedError, UnsafePathError, VerifyError
from tidyup.journal import Journal

PARTIAL_PREFIX = "~tidyup-partial-"
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class MoveResult:
    src: Path
    dst: Path
    cross_volume: bool
    is_dir: bool
    size: int
    sha256: str | None


def _long(p: Path | str) -> str:
    return winutil.long_path(p)


@contextmanager
def _lock_guard(path: Path):
    try:
        yield
    except FileLockedError:
        raise
    except OSError as exc:
        if winutil.is_lock_error(exc):
            raise FileLockedError(f"{path} is in use or access was denied; skipping it") from exc
        raise


def sha256_file(path: Path, *, is_cloud_only: bool = False) -> str:
    winutil.assert_local(path, is_cloud_only)
    h = hashlib.sha256()
    with _lock_guard(path):
        with open(_long(path), "rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
    return h.hexdigest()


def reserve_key(path: Path) -> str:
    return winutil.norm_key(path).casefold()


def unique_path(dst: Path, reserved: set[str] | None = None) -> Path:
    def taken(p: Path) -> bool:
        return os.path.lexists(_long(p)) or (reserved is not None and reserve_key(p) in reserved)

    chosen = dst
    if taken(dst):
        n = 2
        is_dir = os.path.isdir(_long(dst))
        while True:
            if is_dir:
                name = f"{dst.name} ({n})"
            else:
                name = f"{dst.stem} ({n}){dst.suffix}"
            chosen = dst.with_name(name)
            if not taken(chosen):
                break
            n += 1
    if reserved is not None:
        reserved.add(reserve_key(chosen))
    return chosen


def _mkdir(path: Path) -> None:
    with _lock_guard(path):
        os.mkdir(_long(path))


def ensure_dir(path: Path, journal: Journal | None = None, *, dry_run: bool = False) -> list[Path]:
    missing: list[Path] = []
    cur = path
    while not os.path.lexists(_long(cur)):
        missing.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    if os.path.lexists(_long(cur)) and not os.path.isdir(_long(cur)):
        raise UnsafePathError(f"{cur} exists and is not a folder")
    missing.reverse()
    if dry_run:
        return missing
    created: list[Path] = []
    for d in missing:
        safety.assert_not_protected(d)
        try:
            _mkdir(d)
        except FileExistsError:
            if not os.path.isdir(_long(d)):
                raise
            continue
        created.append(d)
        if journal is not None:
            journal.mkdir(d)
    return created


def _no_clobber_rename(src: Path, dst: Path) -> None:
    if os.path.lexists(_long(dst)):
        raise FileExistsError(f"{dst} already exists; refusing to overwrite")
    with _lock_guard(src):
        os.rename(_long(src), _long(dst))


def _discard_partial(temp: Path) -> None:
    try:
        os.unlink(_long(temp))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _remove_verified_source(src: Path, size: int, mtime_ns: int) -> None:
    """Remove a source file ONLY after its copy was verified and the source is unchanged."""
    try:
        st = os.lstat(_long(src))
    except FileNotFoundError:
        return
    if st.st_size != size or st.st_mtime_ns != mtime_ns:
        raise VerifyError(f"{src} changed while it was being copied; both copies were kept")
    with _lock_guard(src):
        os.unlink(_long(src))


def _regular_stat(src: Path) -> os.stat_result:
    try:
        st = os.lstat(_long(src))
    except FileNotFoundError:
        raise
    if not stat.S_ISREG(st.st_mode):
        raise UnsafePathError(f"{src} is not a regular file")
    return st


def _same_content(a: Path, b: Path) -> bool:
    try:
        if os.lstat(_long(a)).st_size != os.lstat(_long(b)).st_size:
            return False
    except OSError:
        return False
    return sha256_file(a) == sha256_file(b)


def _copy_verified(src: Path, final: Path, src_hash: str) -> None:
    """Copy src to a partial file next to final, verify, then rename without clobbering."""
    temp = final.parent / (PARTIAL_PREFIX + final.name)
    if os.path.lexists(_long(temp)):
        _discard_partial(temp)
    try:
        with _lock_guard(src):
            shutil.copy2(_long(src), _long(temp))
        if sha256_file(temp) != src_hash:
            raise VerifyError(f"Copy of {src} did not match the original; the original was left untouched")
        _no_clobber_rename(temp, final)
    except BaseException:
        _discard_partial(temp)
        raise


def move_file(src: Path, dst: Path, *, journal: Journal | None = None, row_id: str | None = None,
              action: str = "move", is_cloud_only: bool = False, adopt_identical: bool = False,
              dry_run: bool = False) -> MoveResult:
    safety.assert_not_protected(src)
    safety.assert_not_protected(dst.parent)
    st = _regular_stat(src)
    size, mtime_ns = st.st_size, st.st_mtime_ns

    adopted = False
    final = dst
    if adopt_identical and os.path.lexists(_long(dst)) and not is_cloud_only \
            and not winutil.is_cloud_only(src) and _same_content(src, dst):
        adopted = True
    else:
        final = unique_path(dst)

    cross = not winutil.same_volume(src, final.parent)
    if dry_run:
        return MoveResult(src, final, cross, False, size, None)

    ensure_dir(final.parent, journal)
    digest: str | None = None
    if adopted:
        cross = not winutil.same_volume(src, final.parent)
        digest = sha256_file(src)
        _remove_verified_source(src, size, mtime_ns)
    elif not cross:
        _no_clobber_rename(src, final)
    else:
        if is_cloud_only or winutil.is_cloud_only(src):
            raise CloudOnlyError(f"{src} is an online-only OneDrive file; not copying it across drives")
        digest = sha256_file(src)
        _copy_verified(src, final, digest)
        _remove_verified_source(src, size, mtime_ns)

    if journal is not None:
        journal.move(src=src, dst=final, row_id=row_id, action=action, is_dir=False,
                     cross_volume=cross, size=size, sha256=digest)
    return MoveResult(src, final, cross, False, size, digest)


def _walk_tree(src_dir: Path) -> tuple[list[Path], list[Path]]:
    """Return (dirs top-down, files), including hidden entries. Links are refused."""
    dirs: list[Path] = [src_dir]
    files: list[Path] = []
    for root, dnames, fnames in os.walk(_long(src_dir)):
        root_p = Path(winutil.strip_long_path(root))
        for d in sorted(dnames):
            p = root_p / d
            if os.path.islink(_long(p)) or (winutil.get_file_attributes(p) & winutil.FILE_ATTRIBUTE_REPARSE_POINT):
                raise UnsafePathError(f"{p} is a link or junction; it cannot be copied across drives safely")
            dirs.append(p)
        for f in sorted(fnames):
            p = root_p / f
            st = os.lstat(_long(p))
            if not stat.S_ISREG(st.st_mode):
                raise UnsafePathError(f"{p} is not a regular file; it cannot be copied across drives safely")
            files.append(p)
    return dirs, files


def move_tree(src_dir: Path, dst_dir: Path, *, journal: Journal | None = None, row_id: str | None = None,
              action: str = "move", adopt_identical: bool = False, dry_run: bool = False) -> MoveResult:
    safety.assert_not_protected(src_dir)
    safety.assert_not_protected(dst_dir.parent)
    if not os.path.isdir(_long(src_dir)) or os.path.islink(_long(src_dir)):
        raise UnsafePathError(f"{src_dir} is not a folder")
    if winutil.is_within(dst_dir, src_dir):
        raise UnsafePathError("Cannot move a folder into itself")

    merge = adopt_identical and os.path.isdir(_long(dst_dir)) and not os.path.islink(_long(dst_dir))
    final = dst_dir if merge else unique_path(dst_dir)
    cross = not winutil.same_volume(src_dir, final.parent)

    if not cross:
        total = 0
        if dry_run:
            return MoveResult(src_dir, final, False, True, total, None)
        ensure_dir(final.parent, journal)
        total = _tree_size(src_dir)
        _no_clobber_rename(src_dir, final)
        if journal is not None:
            journal.move(src=src_dir, dst=final, row_id=row_id, action=action, is_dir=True,
                         cross_volume=False, size=total, sha256=None)
        return MoveResult(src_dir, final, False, True, total, None)

    dirs, files = _walk_tree(src_dir)
    for f in files:
        if winutil.is_cloud_only(f):
            raise CloudOnlyError(f"{f} is an online-only OneDrive file; not copying the folder across drives")
    infos = {f: os.lstat(_long(f)) for f in files}
    total = sum(s.st_size for s in infos.values())
    if dry_run:
        return MoveResult(src_dir, final, True, True, total, None)

    ensure_dir(final.parent, journal)
    for d in dirs:
        target = final / d.relative_to(src_dir)
        if not os.path.isdir(_long(target)):
            _mkdir(target)
    for f in files:
        st = infos[f]
        target = final / f.relative_to(src_dir)
        digest = sha256_file(f)
        if os.path.lexists(_long(target)):
            if adopt_identical and _same_content(f, target):
                continue
            target = unique_path(target)
        _copy_verified(f, target, digest)
    for d in dirs:
        shutil.copystat(_long(d), _long(final / d.relative_to(src_dir)))
    # Everything is verified: now remove the sources.
    for f in files:
        st = infos[f]
        _remove_verified_source(f, st.st_size, st.st_mtime_ns)
    for d in reversed(dirs):
        try:
            os.rmdir(_long(d))
        except OSError as exc:
            if winutil.is_lock_error(exc):
                raise FileLockedError(f"{d} is in use; its verified copy was kept") from exc
            raise VerifyError(f"{d} still has files that were not copied; both copies were kept") from exc
    if journal is not None:
        journal.move(src=src_dir, dst=final, row_id=row_id, action=action, is_dir=True,
                     cross_volume=True, size=total, sha256=None)
    return MoveResult(src_dir, final, True, True, total, None)


def _tree_size(src_dir: Path) -> int:
    total = 0
    for root, _d, fnames in os.walk(_long(src_dir)):
        for f in fnames:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def trash(path: Path, *, journal: Journal | None = None, row_id: str | None = None,
          dry_run: bool = False) -> None:
    safety.assert_not_protected(path)
    if dry_run:
        return
    with _lock_guard(path):
        winutil.send_to_recycle_bin(path)
    if journal is not None:
        journal.trash(src=path, row_id=row_id)


def remove_empty_dir(path: Path) -> bool:
    try:
        os.rmdir(_long(path))
    except OSError:
        return False
    return True
