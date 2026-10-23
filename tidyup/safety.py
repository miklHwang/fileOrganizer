"""Safety layer: decides which paths tidyup must never touch or reorganize."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from tidyup import winutil
from tidyup.config import Config
from tidyup.errors import UnsafePathError

# Duplicated from fsops (avoids an import cycle); tests assert equality.
PARTIAL_PREFIX = "~tidyup-partial-"
PROTECTED_NAMES = frozenset({"programdata", "appdata", "$recycle.bin", "system volume information"})
PROTECTED_GLOBS = ("program files*",)
# Production default: empty (strict). Tests only: the conftest autouse fixture monkeypatches it, because on
# windows-latest pytest temp dirs live under AppData. Never set from config, env vars or CLI.
TRUSTED_PREFIXES: tuple[Path, ...] = ()


def _matches(name: str, patterns) -> bool:
    n = name.casefold()
    return any(fnmatch.fnmatchcase(n, str(p).casefold()) for p in patterns)


def is_protected(path: Path) -> bool:
    path = Path(winutil.strip_long_path(path))
    parts = path.parts
    if path.anchor and parts:
        parts = parts[1:]
    name_parts = parts
    full = [p.casefold() for p in path.parts]
    for prefix in TRUSTED_PREFIXES:
        pre = [p.casefold() for p in Path(winutil.strip_long_path(prefix)).parts]
        if pre and len(full) >= len(pre) and full[: len(pre)] == pre:
            name_parts = path.parts[len(pre):]
            break
    for part in name_parts:
        if part.casefold() in PROTECTED_NAMES or _matches(part, PROTECTED_GLOBS):
            return True
    if parts and parts[0].casefold() == "windows":
        return True
    root = os.environ.get("SystemRoot")
    if root and winutil.is_within(path, root):
        return True
    return False


def is_hidden_or_system(path: Path, attrs: int | None = None) -> bool:
    if attrs is None:
        try:
            attrs = winutil.get_file_attributes(path)
        except OSError:
            attrs = 0
    return winutil.is_hidden_or_system(path.name, attrs)


def is_excluded(path: Path, cfg: Config) -> bool:
    if is_protected(path):
        return True
    if _matches(path.name, cfg.exclusions.get("names") or []):
        return True
    for p in cfg.exclusions.get("paths") or []:
        if winutil.is_within(path, p):
            return True
    if winutil.is_within(path, cfg.work_dir):
        return True
    return path.name.startswith(PARTIAL_PREFIX)


def _is_root(path: Path, cfg: Config) -> bool:
    key = winutil.norm_key(path)
    roots = [s.path for s in cfg.sources] + [p for p in cfg.folders.values() if p is not None]
    return any(winutil.norm_key(r) == key for r in roots)


def is_atomic_dir(path: Path, cfg: Config) -> bool:
    if _is_root(path, cfg):
        return False
    if _matches(path.name, cfg.atomic.get("folder_names") or []):
        return True
    markers = cfg.atomic.get("markers") or []
    try:
        names = os.listdir(winutil.long_path(path))
    except OSError:
        return False
    return any(_matches(n, markers) for n in names)


def find_atomic_ancestor(path: Path, cfg: Config) -> Path | None:
    current = path.parent
    while True:
        if _is_root(current, cfg):
            return None
        if is_atomic_dir(current, cfg):
            return current
        if current.parent == current:
            return None
        current = current.parent


def assert_not_protected(path: Path) -> None:
    if is_protected(path):
        raise UnsafePathError(f"Refusing to touch a protected system location: {path}")
    try:
        os.lstat(winutil.long_path(path))
    except OSError:
        return
    if is_hidden_or_system(path):
        raise UnsafePathError(f"Refusing to touch a hidden or system file: {path}")


def check_source(path: Path, cfg: Config, *, is_dir: bool) -> None:
    assert_not_protected(path)
    if is_excluded(path, cfg):
        raise UnsafePathError(f"This path is excluded in your settings: {path}")
    anc = find_atomic_ancestor(path, cfg)
    if anc is not None:
        raise UnsafePathError(f"{path} is inside the protected project/app folder {anc}")
    if is_dir and not is_atomic_dir(path, cfg):
        raise UnsafePathError(f"Only whole project/app folders may be moved as folders: {path}")


def check_destination(path: Path, cfg: Config) -> None:
    if is_protected(path):
        raise UnsafePathError(f"Destination is a protected system location: {path}")
    anc = find_atomic_ancestor(path, cfg)
    if anc is not None:
        raise UnsafePathError(f"Destination is inside the protected project/app folder {anc}")
    if is_excluded(path, cfg):
        raise UnsafePathError(f"Destination is excluded in your settings: {path}")
