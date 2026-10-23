"""Windows / OS specific helpers. Everything here is small and mockable in tests."""
from __future__ import annotations

import errno
import ntpath
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from tidyup.errors import CloudOnlyError

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ONLY_MASK = 0x400000 | 0x40000 | 0x1000
LONG_PATH_THRESHOLD = 240

_LP = "\\\\?\\"
_LP_UNC = "\\\\?\\UNC\\"


def is_windows() -> bool:
    return os.name == "nt"


def long_path(path: str | os.PathLike) -> str:
    s = os.fspath(path)
    if not is_windows():
        return s
    if s.startswith(_LP):
        return s
    if len(s) < LONG_PATH_THRESHOLD:
        return s
    if not ntpath.isabs(s) or (not s.startswith("\\\\") and not ntpath.splitdrive(s)[0]):
        s = ntpath.abspath(s)
    if s.startswith("\\\\"):
        return _LP_UNC + ntpath.normpath(s)[2:]
    return _LP + ntpath.normpath(s)


def strip_long_path(path: str | os.PathLike) -> str:
    s = os.fspath(path)
    if s.startswith(_LP_UNC):
        return "\\\\" + s[len(_LP_UNC):]
    if s.startswith(_LP):
        return s[len(_LP):]
    return s


def get_file_attributes(path: str | os.PathLike, st: os.stat_result | None = None) -> int:
    if not is_windows():
        if st is None:
            os.lstat(long_path(path))  # raises OSError if missing
        return 0
    st = st if st is not None else os.lstat(long_path(path))
    return int(getattr(st, "st_file_attributes", 0))


def is_cloud_only_attrs(attrs: int) -> bool:
    return bool(attrs & CLOUD_ONLY_MASK)


def is_cloud_only(path: str | os.PathLike) -> bool:
    try:
        return is_cloud_only_attrs(get_file_attributes(path))
    except OSError:
        return False


def assert_local(path: str | os.PathLike, known_cloud_only: bool = False) -> None:
    if known_cloud_only or is_cloud_only(path):
        raise CloudOnlyError(f"{strip_long_path(path)} is an online-only OneDrive file; not reading it")


def is_hidden_or_system(name: str, attrs: int) -> bool:
    if attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
        return True
    if name.startswith("."):
        return True
    return False


def file_times(st: os.stat_result) -> tuple[datetime, datetime]:
    birth = getattr(st, "st_birthtime", None)
    if birth is not None:
        created = birth
    elif is_windows():
        created = st.st_ctime
    else:
        created = min(st.st_ctime, st.st_mtime)
    return datetime.fromtimestamp(created), datetime.fromtimestamp(st.st_mtime)


def norm_key(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(strip_long_path(path))))


def is_within(child: str | os.PathLike, parent: str | os.PathLike) -> bool:
    c, p = norm_key(child), norm_key(parent)
    if c == p:
        return True
    cp = Path(c).parts
    pp = Path(p).parts
    return len(cp) > len(pp) and cp[: len(pp)] == pp


def onedrive_root() -> Path | None:
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        val = os.environ.get(var)
        if val and Path(val).exists():
            return Path(val)
    cand = Path.home() / "OneDrive"
    return cand if cand.exists() else None


def _existing(p: Path) -> Path | None:
    return p if p.exists() else None


def known_folders() -> dict[str, Path | None]:
    home = Path.home()
    names = {"documents": "Documents", "desktop": "Desktop", "downloads": "Downloads",
             "pictures": "Pictures", "videos": "Videos"}
    found: dict[str, Path | None] = {k: home / v for k, v in names.items()}
    if is_windows():
        try:
            import winreg  # type: ignore[import-not-found]
            valmap = {"Personal": "documents", "Desktop": "desktop", "My Pictures": "pictures",
                      "My Video": "videos", "{374DE290-123F-4565-9164-39C4925E467B}": "downloads"}
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                for value_name, k in valmap.items():
                    try:
                        raw, _ = winreg.QueryValueEx(key, value_name)
                    except OSError:
                        continue
                    if raw:
                        found[k] = Path(ntpath.expandvars(str(raw)))
        except (ImportError, OSError):
            pass
    result: dict[str, Path | None] = {"home": home}
    for k in names:
        result[k] = _existing(found[k]) if found[k] is not None else None
    result["onedrive"] = onedrive_root()
    return result


def _nearest_existing(path: str | os.PathLike) -> str:
    p = os.path.abspath(strip_long_path(path))
    while not os.path.exists(long_path(p)):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def same_volume(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    return os.stat(long_path(_nearest_existing(a))).st_dev == os.stat(long_path(_nearest_existing(b))).st_dev


def free_space(path: str | os.PathLike) -> int:
    return shutil.disk_usage(_nearest_existing(path)).free


def is_lock_error(exc: OSError) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "winerror", None) in (5, 32, 33):
        return True
    return exc.errno in (errno.EACCES, errno.EBUSY, errno.ETXTBSY)


def send_to_recycle_bin(path: str | os.PathLike) -> None:
    import send2trash
    send2trash.send2trash(strip_long_path(path))
