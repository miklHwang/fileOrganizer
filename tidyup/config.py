"""Configuration loader. Merges packaged defaults <- config file <- overrides and resolves folders."""
from __future__ import annotations

import copy
import dataclasses
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from tidyup import console, winutil
from tidyup.errors import ConfigError

FOLDER_KEYS = ("home", "documents", "desktop", "downloads", "pictures", "videos", "onedrive", "screenshots")

_ROOTS = ("documents", "pictures", "videos", "installers", "archives",
          "_unsorted", "_duplicates", "_review_delete")
_DELETE_MODES = ("review", "recycle_bin")
_PCT_VAR = re.compile(r"%([^%]+)%")


@dataclass(frozen=True)
class SourceRoot:
    label: str
    path: Path
    in_onedrive: bool


@dataclass(frozen=True)
class Config:
    path: Path | None
    config_dir: Path
    folders: dict[str, Path | None]
    sources: tuple[SourceRoot, ...]
    dest_root: Path | None
    work_dir: Path
    delete_mode: str
    exclusions: dict
    atomic: dict
    categories: dict[str, dict]
    structure: dict[str, str]
    naming: dict
    rules: dict
    upkeep: dict
    migrate: dict
    report: dict
    csv: dict
    raw: dict

    @property
    def inventory_path(self) -> Path:
        return self.work_dir / "inventory.csv"

    @property
    def manifest_path(self) -> Path:
        return self.work_dir / "manifest.csv"

    @property
    def report_path(self) -> Path:
        return self.work_dir / "report.html"

    @property
    def migrate_manifest_path(self) -> Path:
        return self.work_dir / "migrate_manifest.csv"

    @property
    def logs_dir(self) -> Path:
        return self.work_dir / "logs"

    def with_sources(self, labels: Sequence[str]) -> Config:
        sources = _resolve_sources(list(labels), self.folders, self.config_dir, self.folders.get("onedrive"))
        return dataclasses.replace(self, sources=sources)

    def folder_containing(self, path: Path) -> str | None:
        best: str | None = None
        best_len = -1
        for key, folder in self.folders.items():
            if key == "home" or folder is None:
                continue
            if winutil.is_within(path, folder):
                n = len(winutil.norm_key(folder))
                if n > best_len:
                    best, best_len = key, n
        if best is not None:
            return best
        home = self.folders.get("home")
        if home is not None and winutil.is_within(path, home):
            return "home"
        return None

    def in_onedrive(self, path: Path) -> bool:
        od = self.folders.get("onedrive")
        return od is not None and winutil.is_within(path, od)

    def resolve_destination(self, logical_dir: str, source: Path) -> Path:
        parts = [p for p in logical_dir.replace("\\", "/").split("/") if p]
        if not parts:
            raise ConfigError("structure: empty destination folder")
        if self.dest_root is not None:
            return self.dest_root.joinpath(*parts)
        first, rest = parts[0].casefold(), parts[1:]
        f = self.folders
        base: Path | None
        if first in ("documents", "pictures", "videos"):
            base = f.get(first)
            target = (base or f["home"] or Path.home()).joinpath(*(rest if base else parts))
        elif first in ("installers", "archives"):
            base = f.get("downloads")
            target = (base or f["home"] or Path.home()).joinpath(*parts)
        else:
            target = (f.get("home") or Path.home()).joinpath(*parts)
        od = f.get("onedrive")
        if od is not None and self.in_onedrive(source) and not winutil.is_within(target, od):
            target = od.joinpath(*parts)
        return target


# ----------------------------------------------------------------------------- helpers

def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def default_config_text() -> str:
    return resources.files("tidyup").joinpath("default_config.yaml").read_text("utf-8")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_home() -> Path:
    return Path.home() / "tidyup"


def app_install_dir() -> Path | None:
    return Path(sys.executable).resolve().parent if is_frozen() else None


def ensure_app_config() -> Path:
    """Create app_home()/config.yaml from the defaults if missing. Never overwrites."""
    path = app_home() / "config.yaml"
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(winutil.long_path(path), "x", encoding="utf-8", newline="") as f:
                f.write(default_config_text())
        except FileExistsError:
            pass
        except OSError as e:
            raise ConfigError(f"Cannot create the settings file {path}: {e.strerror or e}") from e
    return path


def find_default_config(cwd: Path | None = None) -> Path | None:
    if is_frozen():
        return ensure_app_config()
    p = (cwd or Path.cwd()) / "config.yaml"
    return p if p.is_file() else None


def _parse_yaml(text: str, label: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        problem = getattr(e, "problem", None) or str(e)
        raise ConfigError(f"{label} is not valid YAML{where}: {problem}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{label} must contain settings in 'key: value' form")
    return data


def _expand(value: Any) -> str:
    s = str(value)
    s = _PCT_VAR.sub(lambda m: os.environ.get(m.group(1), m.group(0)), s)
    return os.path.expanduser(os.path.expandvars(s))


def _resolve_folders(raw: dict) -> dict[str, Path | None]:
    section = raw.get("folders") or {}
    known: dict[str, Path | None] | None = None
    out: dict[str, Path | None] = {}
    for key in FOLDER_KEYS:
        if key == "screenshots":
            continue
        val = section.get(key, "auto")
        if val is None:
            out[key] = None
        elif isinstance(val, str) and val.strip().lower() == "auto":
            if known is None:
                known = winutil.known_folders()
            out[key] = known.get(key)
        else:
            out[key] = Path(_expand(val))
    val = section.get("screenshots", "auto")
    if val is None:
        out["screenshots"] = None
    elif isinstance(val, str) and val.strip().lower() == "auto":
        out["screenshots"] = out["pictures"] / "Screenshots" if out.get("pictures") else None
    else:
        out["screenshots"] = Path(_expand(val))
    return {k: out[k] for k in FOLDER_KEYS}


def _resolve_sources(entries: list, folders: dict[str, Path | None], config_dir: Path,
                     onedrive: Path | None) -> tuple[SourceRoot, ...]:
    cands: list[tuple[str, Path]] = []
    for entry in entries:
        name = str(entry)
        if name in FOLDER_KEYS:
            p = folders.get(name)
            if p is None:
                console.warn(f"Source '{name}' is not available on this PC; skipping it.")
                continue
            label = name
        else:
            p = Path(_expand(name))
            if not p.is_absolute():
                p = config_dir / p
            label = p.name or str(p)
        if not os.path.isdir(winutil.long_path(p)):
            console.warn(f"Source folder {p} does not exist; skipping it.")
            continue
        cands.append((label, p))
    kept: list[tuple[str, Path]] = []
    for i, (label, p) in enumerate(cands):
        covered_by = None
        for j, (olabel, op) in enumerate(cands):
            if i == j:
                continue
            same = winutil.norm_key(p) == winutil.norm_key(op)
            if (same and j < i) or (not same and winutil.is_within(p, op)):
                covered_by = olabel
                break
        if covered_by is not None:
            console.info(f"Source '{label}' is already covered by '{covered_by}'; not counting it twice.")
        else:
            kept.append((label, p))
    return tuple(
        SourceRoot(label, p, onedrive is not None and winutil.is_within(p, onedrive))
        for label, p in kept)


def _validate(merged: dict, defaults: dict) -> None:
    for key in merged:
        if key not in defaults:
            raise ConfigError(f"Unknown setting '{key}' in config (check the spelling and indentation)")
    if merged.get("version") != 1:
        raise ConfigError(f"version: must be 1 (found {merged.get('version')!r})")
    if merged.get("delete_mode") not in _DELETE_MODES:
        raise ConfigError(f"delete_mode: must be 'review' or 'recycle_bin' (found {merged.get('delete_mode')!r})")
    for key, default in defaults.items():
        if isinstance(default, dict) and not isinstance(merged.get(key), dict):
            raise ConfigError(f"{key}: must be a group of settings, not {merged.get(key)!r}")
    if not isinstance(merged.get("sources"), list):
        raise ConfigError("sources: must be a list of folder names")
    for k, v in merged["structure"].items():
        if v is None or not str(v).strip():
            raise ConfigError(f"structure.{k}: must not be empty")
        first = str(v).replace("\\", "/").strip("/").split("/")[0].casefold()
        if first not in _ROOTS:
            raise ConfigError(
                f"structure.{k}: '{v}' must start with one of Documents, Pictures, Videos, Installers, "
                "Archives, _Unsorted, _Duplicates, _Review_Delete")


def load_config(path: Path | None = None, overrides: dict | None = None) -> Config:
    defaults = _parse_yaml(default_config_text(), "packaged default config")
    merged = defaults
    if path is not None:
        path = Path(path)
        try:
            text = path.read_text("utf-8-sig")
        except OSError as e:
            raise ConfigError(f"Cannot read config file {path}: {e.strerror or e}") from e
        merged = deep_merge(merged, _parse_yaml(text, str(path)))
    if overrides:
        merged = deep_merge(merged, overrides)
    _validate(merged, defaults)

    config_dir = path.resolve().parent if path is not None else Path.cwd()
    folders = _resolve_folders(merged)
    sources = _resolve_sources(list(merged["sources"]), folders, config_dir, folders.get("onedrive"))

    dest = (merged.get("destination") or {}).get("root")
    dest_root = None
    if dest:
        dest_root = Path(_expand(dest))
        if not dest_root.is_absolute() and not winutil.is_windows():
            dest_root = config_dir / dest_root
    work_dir = Path(_expand(merged["work_dir"]))
    if not work_dir.is_absolute():
        work_dir = config_dir / work_dir

    exclusions = dict(merged["exclusions"])
    exclusions["names"] = [str(n) for n in exclusions.get("names") or []]
    exclusions["paths"] = [Path(_expand(p)) for p in exclusions.get("paths") or []]
    install = app_install_dir()
    if install is not None:
        exclusions["paths"].append(install)
    atomic = {"markers": [str(n) for n in merged["atomic"].get("markers") or []],
              "folder_names": [str(n) for n in merged["atomic"].get("folder_names") or []]}
    categories: dict[str, dict] = {}
    for name, body in merged["categories"].items():
        body = dict(body or {})
        body["keywords"] = [str(k) for k in body.get("keywords") or []]
        categories[str(name)] = body

    return Config(
        path=path, config_dir=config_dir, folders=folders, sources=sources, dest_root=dest_root,
        work_dir=work_dir, delete_mode=merged["delete_mode"], exclusions=exclusions, atomic=atomic,
        categories=categories, structure={k: str(v) for k, v in merged["structure"].items()},
        naming=merged["naming"], rules=merged["rules"], upkeep=merged["upkeep"],
        migrate=merged["migrate"], report=merged["report"], csv=merged["csv"], raw=merged)
