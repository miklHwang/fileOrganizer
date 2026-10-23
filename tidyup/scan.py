"""tidyup scan: read-only inventory of the configured source folders."""
from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from tidyup import console, extract, fsops, models, safety, winutil
from tidyup.errors import FileLockedError
from tidyup.models import EntryType, FileRecord

if TYPE_CHECKING:
    import argparse

    from tidyup.config import Config


@dataclass
class ScanResult:
    records: list[FileRecord]
    skipped: dict[str, int]
    errors: list[tuple[Path, str]]


@dataclass
class _Walk:
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        junction = getattr(entry, "is_junction", None)
        return bool(junction()) if junction else False
    except OSError:
        return False


def _atomic_totals(directory: Path) -> tuple[int, int]:
    """Stat-only walk: (total bytes, file count) inside an atomic folder."""
    total = count = 0
    stack = [directory]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(winutil.long_path(d)) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            try:
                if _is_link(e):
                    continue
                if e.is_dir(follow_symlinks=False):
                    stack.append(d / e.name)
                else:
                    total += e.stat(follow_symlinks=False).st_size
                    count += 1
            except OSError:
                continue
    return total, count


def _walk(cfg: Config, state: _Walk) -> Iterator[FileRecord]:
    seen: set[str] = set()
    for root in cfg.sources:
        stack = [root.path]
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(winutil.long_path(directory)) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except PermissionError:
                state.skip("access denied")
                continue
            except OSError as exc:
                if winutil.is_lock_error(exc):
                    state.skip("access denied")
                else:
                    state.errors.append((directory, str(exc)))
                continue
            subdirs: list[Path] = []
            for entry in entries:
                path = directory / entry.name
                if _is_link(entry):
                    state.skip("symlink")
                    continue
                if safety.is_protected(path):
                    state.skip("protected")
                    continue
                if safety.is_excluded(path, cfg):
                    state.skip("excluded")
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                    attrs = winutil.get_file_attributes(path, st)
                except PermissionError:
                    state.skip("access denied")
                    continue
                except OSError as exc:
                    state.errors.append((path, str(exc)))
                    continue
                if winutil.is_hidden_or_system(entry.name, attrs):
                    state.skip("hidden")
                    continue
                key = winutil.norm_key(path)
                if key in seen:
                    continue
                seen.add(key)
                created, modified = winutil.file_times(st)
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    is_dir = False
                if is_dir:
                    if safety.is_atomic_dir(path, cfg):
                        size, count = _atomic_totals(path)
                        yield FileRecord(
                            path=path, entry_type=EntryType.ATOMIC_FOLDER, source_root=root.label,
                            size=size, extension="", created=created, modified=modified,
                            is_cloud_only=False, atomic_root=path, file_count=count)
                    else:
                        subdirs.append(path)
                else:
                    yield FileRecord(
                        path=path, entry_type=EntryType.FILE, source_root=root.label,
                        size=st.st_size, extension=os.path.splitext(entry.name)[1].lower(),
                        created=created, modified=modified,
                        is_cloud_only=winutil.is_cloud_only_attrs(attrs))
            # top-down, sorted by name: push reversed so the first name is popped first
            stack.extend(reversed(subdirs))


def walk_sources(cfg: Config) -> Iterator[FileRecord]:
    """Stat-only pass; never reads file contents."""
    yield from _walk(cfg, _Walk())


def scan_sources(cfg: Config, *, show_progress: bool = True) -> ScanResult:
    state = _Walk()
    gen = _walk(cfg, state)
    if show_progress:
        gen = console.progress(gen, desc="Scanning folders", unit="file")
    records = list(gen)

    min_size = int((cfg.rules.get("duplicates") or {}).get("min_size", 1))
    by_size: dict[int, list[FileRecord]] = defaultdict(list)
    for rec in records:
        if rec.entry_type == EntryType.FILE and not rec.is_cloud_only and rec.size >= min_size:
            by_size[rec.size].append(rec)
    to_hash = [r for group in by_size.values() if len(group) >= 2 for r in group]
    images = [r for r in records if r.entry_type == EntryType.FILE and not r.is_cloud_only
              and r.extension in extract.IMAGE_EXTS]

    def checked(items: list[FileRecord], desc: str):
        return console.progress(items, desc=desc, unit="file") if show_progress and items else items

    for rec in checked(to_hash, "Comparing identical-size files"):
        try:
            rec.sha256 = fsops.sha256_file(rec.path, is_cloud_only=rec.is_cloud_only)
        except FileLockedError as exc:
            rec.error = f"locked: {exc}"
            state.skip("locked")
        except OSError as exc:
            rec.error = f"unreadable: {exc}"
            state.errors.append((rec.path, str(exc)))

    for rec in checked(images, "Reading image info"):
        try:
            info = extract.read_image_info(rec.path, is_cloud_only=rec.is_cloud_only)
        except FileLockedError as exc:
            if not rec.error:
                rec.error = f"locked: {exc}"
                state.skip("locked")
            continue
        if info is None:
            if not rec.error:
                rec.error = "unreadable image"
            continue
        rec.image_width, rec.image_height = info.width, info.height
        rec.camera_make, rec.camera_model = info.camera_make, info.camera_model
        rec.exif_datetime, rec.phash = info.exif_datetime, info.phash

    return ScanResult(records=records, skipped=dict(state.skipped), errors=state.errors)


def summarize(records: Sequence[FileRecord]) -> dict:
    by_type: dict[str, list[int]] = {}
    by_source: dict[str, list[int]] = {}
    total = [0, 0]
    cloud = atomic = 0
    for r in records:
        group = "Atomic folders" if r.entry_type == EntryType.ATOMIC_FOLDER else models.type_group(r.extension)
        for bucket, key in ((by_type, group), (by_source, r.source_root), (None, None)):
            acc = total if bucket is None else bucket.setdefault(key, [0, 0])
            acc[0] += 1
            acc[1] += r.size
        cloud += bool(r.is_cloud_only)
        atomic += r.entry_type == EntryType.ATOMIC_FOLDER
    return {
        "by_type": {k: (v[0], v[1]) for k, v in by_type.items()},
        "by_source": {k: (v[0], v[1]) for k, v in by_source.items()},
        "cloud_only": cloud,
        "atomic_folders": atomic,
        "total": (total[0], total[1]),
    }


def command(cfg: Config, args: argparse.Namespace) -> int:
    if not cfg.sources:
        console.warn("No source folders were found to scan. Check the 'sources' setting in your config.")
        return 1
    console.heading("Scanning (read-only)")
    for s in cfg.sources:
        console.info(f"  {s.label}: {s.path}")
    result = scan_sources(cfg)
    summary = summarize(result.records)

    console.heading("By type")
    console.table(["Type", "Items", "Size"], [
        (k, n, console.human_size(b)) for k, (n, b) in sorted(summary["by_type"].items())])
    console.heading("By folder")
    console.table(["Folder", "Items", "Size"], [
        (k, n, console.human_size(b)) for k, (n, b) in sorted(summary["by_source"].items())])
    count, size = summary["total"]
    console.info(f"\nTotal: {count} items, {console.human_size(size)}")
    console.info(f"Online-only OneDrive files (not read): {summary['cloud_only']}")
    console.info(f"Project/app folders kept as one unit: {summary['atomic_folders']}")
    if result.skipped:
        console.heading("Skipped")
        console.table(["Reason", "Count"], sorted(result.skipped.items()))
    for path, msg in result.errors[:10]:
        console.warn(f"Could not read {path}: {msg}")

    if getattr(args, "dry_run", False):
        console.info("\nDry run: inventory was not written.")
        return 0
    n = models.write_inventory(cfg.inventory_path, result.records)
    console.success(f"Wrote {n} rows to {cfg.inventory_path}")
    console.info("Next step: run 'tidyup plan' to propose changes.")
    return 0
