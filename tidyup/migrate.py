"""tidyup migrate: move organized folders to another drive (copy -> verify -> remove), resumable.

Nothing moves without a reviewed migrate manifest. All mutation goes through ``fsops``.
"""
from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tidyup import console, fsops, journal, models, safety, winutil
from tidyup.errors import (CloudOnlyError, FileLockedError, InsufficientSpaceError, TidyupError,
                           UnsafePathError, VerifyError)
from tidyup.models import Action, Confidence, ManifestRow

if TYPE_CHECKING:
    import argparse

    from tidyup.apply import ApplyResult
    from tidyup.config import Config

RULE = "migrate"
_DISPLAY = {"documents": "Documents", "pictures": "Pictures", "videos": "Videos",
            "downloads": "Downloads", "desktop": "Desktop"}
CLOUD_NOTE = "cloud-only: not migrated (would download)"


@dataclass
class MigratePlan:
    to_root: Path
    rows: list[ManifestRow]
    required_bytes: int
    free_bytes: int


def _lp(p: Path) -> str:
    return winutil.long_path(p)


def _resolve_folder(cfg: Config, spec: str) -> tuple[Path, str] | None:
    """Return (folder, display name) for a migrate.folders entry, or None if it is not available."""
    spec = str(spec).strip()
    if spec.startswith("{"):
        end = spec.find("}")
        if end < 0:
            return None
        base = cfg.folders.get(spec[1:end].lower())
        if base is None:
            return None
        rest = [p for p in spec[end + 1:].replace("\\", "/").split("/") if p]
        folder = base.joinpath(*rest)
        return folder, folder.name
    key = spec.lower()
    base = cfg.folders.get(key)
    if base is None:
        return None
    return base, _DISPLAY.get(key, base.name)


def _source_folders(cfg: Config) -> list[tuple[Path, str]]:
    """Existing migrate.folders entries, deduplicated; a folder nested in another listed one is dropped."""
    found: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for spec in cfg.migrate.get("folders") or []:
        res = _resolve_folder(cfg, spec)
        if res is None or not os.path.isdir(_lp(res[0])):
            continue
        key = winutil.norm_key(res[0])
        if key in seen:
            continue
        seen.add(key)
        found.append(res)
    return [(f, d) for f, d in found
            if not any(o is not f and winutil.is_within(f, o) for o, _ in found)]


def _overlaps(a: Path, b: Path) -> bool:
    return winutil.is_within(a, b) or winutil.is_within(b, a)


def _check_to_root(cfg: Config, to_root: Path) -> None:
    for folder, _d in _source_folders(cfg):
        if _overlaps(to_root, folder):
            raise TidyupError(
                f"The target {to_root} is the same as, inside, or contains the folder {folder} that would "
                "be migrated. Choose a different drive or an empty folder elsewhere. Nothing was moved.")


def _tree_bytes(path: Path) -> int:
    total = 0
    for root, _d, files in os.walk(_lp(path)):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _walk(cfg: Config, folder: Path, dest_base: Path, make_row) -> None:
    try:
        with os.scandir(_lp(folder)) as it:
            entries = sorted(it, key=lambda e: e.name.casefold())
    except OSError as exc:
        console.warn(f"Could not read {folder}: {exc}")
        return
    for e in entries:
        p = folder / e.name
        try:
            st = e.stat(follow_symlinks=False)
            attrs = winutil.get_file_attributes(p, st)
        except OSError:
            continue
        if safety.is_excluded(p, cfg) or safety.is_hidden_or_system(p, attrs):
            continue
        if attrs & winutil.FILE_ATTRIBUTE_REPARSE_POINT or stat.S_ISLNK(st.st_mode):
            continue
        dst = dest_base / e.name
        if stat.S_ISDIR(st.st_mode):
            if safety.is_atomic_dir(p, cfg):
                make_row(p, dst, _tree_bytes(p), cloud=_tree_has_cloud(p))
            else:
                _walk(cfg, p, dst, make_row)
        elif stat.S_ISREG(st.st_mode):
            make_row(p, dst, st.st_size, cloud=winutil.is_cloud_only_attrs(attrs))


def _tree_has_cloud(path: Path) -> bool:
    for root, _d, files in os.walk(_lp(path)):
        for f in files:
            if winutil.is_cloud_only(Path(winutil.strip_long_path(os.path.join(root, f)))):
                return True
    return False


def build_migration(cfg: Config, to_root: Path) -> MigratePlan:
    to_root = Path(to_root)
    if not os.path.isdir(_lp(to_root)):
        raise TidyupError(f"The target drive or folder {to_root} does not exist.")
    _check_to_root(cfg, to_root)
    rows: list[ManifestRow] = []
    sizes: list[tuple[Path, int]] = []
    include_od = bool(cfg.migrate.get("include_onedrive", False))

    def make_row(src: Path, dst: Path, size: int, *, cloud: bool) -> None:
        if _overlaps(src, dst):
            console.warn(f"Skipping {src}: its destination overlaps the source.")
            return
        rid = len(rows) + 1
        if cloud:
            rows.append(ManifestRow(rid, Action.SKIP, src, dst, RULE, Confidence.HIGH, CLOUD_NOTE, False))
            return
        rows.append(ManifestRow(rid, Action.MOVE, src, dst, RULE, Confidence.HIGH,
                                f"{size} bytes", True))
        sizes.append((src, size))

    for folder, display in _source_folders(cfg):
        if cfg.in_onedrive(folder) and not include_od:
            console.warn(f"Skipping {folder}: it is inside OneDrive (set migrate.include_onedrive to include it).")
            continue
        _walk(cfg, folder, to_root / display, make_row)

    crossing = sum(sz for src, sz in sizes if not winutil.same_volume(src, to_root))
    margin = int(cfg.migrate.get("free_space_margin_mb", 1024)) * 1024 * 1024
    required = crossing + margin if crossing else 0
    return MigratePlan(to_root, rows, required, winutil.free_space(to_root))


def check_space(plan: MigratePlan) -> None:
    if plan.free_bytes < plan.required_bytes:
        raise InsufficientSpaceError(
            f"Not enough free space on {plan.to_root}: need {console.human_size(plan.required_bytes)} "
            f"(including a safety margin) but only {console.human_size(plan.free_bytes)} is free. "
            "Nothing was moved.")


def _is_dir(p: Path) -> bool:
    return os.path.isdir(_lp(p)) and not os.path.islink(_lp(p))


def _find_resumable(cfg: Config, to_root: Path):
    key = winutil.norm_key(to_root)
    for run in reversed(journal.list_runs(cfg.logs_dir)):
        to = (run.header.get("meta") or {}).get("to")
        if (run.kind == "migrate" and not run.finished and run.undone_by is None and to
                and winutil.norm_key(Path(to)) == key):
            return run
    return None


def run_migration(cfg: Config, rows: Sequence[ManifestRow], to_root: Path, *,
                  dry_run: bool = False) -> ApplyResult:
    from tidyup.apply import ApplyResult

    to_root = Path(to_root)
    _check_to_root(cfg, to_root)
    todo = [r for r in rows if r.approved and r.action != Action.SKIP]
    check_space(_remaining_plan(cfg, todo, to_root))
    sources = [f for f, _d in _source_folders(cfg)]
    jr = None
    if not dry_run:
        prev = _find_resumable(cfg, to_root)
        if prev is not None:
            jr = journal.Journal.resume(prev.path)
        else:
            jr = journal.Journal.create(cfg.logs_dir, "migrate", {"config": cfg.path, "to": to_root})
    counts = {"moved": 0, "trashed": 0, "skipped": 0, "errors": 0}
    try:
        for row in console.progress(todo, desc="Migrating", total=len(todo), unit="item"):
            src, dst = row.source, row.destination
            rid = str(row.id)
            try:
                if not os.path.lexists(_lp(src)):
                    # Resumable: a source that is gone but whose destination exists was already migrated.
                    done = dst is not None and os.path.lexists(_lp(dst))
                    reason = "already migrated" if done else "source no longer exists"
                    counts["skipped"] += 1
                    if jr:
                        jr.skip(src=src, row_id=rid, reason=reason)
                    continue
                if dst is None or not str(dst).strip():
                    raise TidyupError("Row has no destination")
                if _overlaps(src, dst):
                    raise TidyupError("Destination is the same as, or nested with, the source")
                if not winutil.is_within(dst, to_root) or winutil.norm_key(dst) == winutil.norm_key(to_root):
                    raise TidyupError(f"Destination is not inside {to_root}")
                if not any(winutil.is_within(src, f) for f in sources):
                    raise TidyupError("Source is not inside a configured migrate folder")
                is_dir = _is_dir(src)
                safety.check_source(src, cfg, is_dir=is_dir)
                safety.check_destination(dst, cfg)
                if is_dir and winutil.is_within(dst, src):
                    raise UnsafePathError("Cannot move a folder into itself")
                if is_dir:
                    fsops.move_tree(src, dst, journal=jr, row_id=rid, action="move",
                                    adopt_identical=True, dry_run=dry_run)
                else:
                    fsops.move_file(src, dst, journal=jr, row_id=rid, action="move",
                                    is_cloud_only=winutil.is_cloud_only(src), adopt_identical=True,
                                    dry_run=dry_run)
                counts["moved"] += 1
            except (UnsafePathError, CloudOnlyError, FileLockedError) as exc:
                counts["skipped"] += 1
                if jr:
                    jr.skip(src=src, row_id=rid, reason=str(exc))
                else:
                    console.warn(f"Would skip {src}: {exc}")
            except (VerifyError, OSError, TidyupError) as exc:
                counts["errors"] += 1
                if jr:
                    jr.error(src=src, row_id=rid, reason=str(exc))
                else:
                    console.warn(f"Would fail {src}: {exc}")
    except KeyboardInterrupt:
        if jr:
            jr.finish(counts)
        raise
    except BaseException:
        if jr:
            jr.close()  # unfinished on purpose: the next run resumes this journal
        raise
    if jr:
        jr.finish(counts)
    return ApplyResult(run_id=jr.run_id if jr else None, log_path=jr.path if jr else None,
                       moved=counts["moved"], trashed=0, skipped=counts["skipped"],
                       errors=counts["errors"], dry_run=dry_run)


def _remaining_plan(cfg: Config, rows: Sequence[ManifestRow], to_root: Path) -> MigratePlan:
    todo = [r for r in rows if r.approved and r.action != Action.SKIP and os.path.lexists(_lp(r.source))]
    crossing = 0
    for r in todo:
        if winutil.same_volume(r.source, to_root):
            continue
        crossing += _tree_bytes(r.source) if _is_dir(r.source) else _size(r.source)
    margin = int(cfg.migrate.get("free_space_margin_mb", 1024)) * 1024 * 1024
    return MigratePlan(to_root, list(todo), crossing + margin if crossing else 0, winutil.free_space(to_root))


def _size(p: Path) -> int:
    try:
        return os.lstat(_lp(p)).st_size
    except OSError:
        return 0


def command(cfg: Config, args: argparse.Namespace) -> int:
    to_root = Path(args.to)
    delim = cfg.csv.get("delimiter", ",")
    if getattr(args, "dry_run", False):
        plan = build_migration(cfg, to_root)
        models.write_manifest(cfg.migrate_manifest_path, plan.rows, delim)
        moves = [r for r in plan.rows if r.action != Action.SKIP]
        skipped = len(plan.rows) - len(moves)
        console.heading("Migration preview (nothing was moved)")
        console.info(f"{len(moves)} item(s) to move, {skipped} skipped.")
        console.info(f"Space needed: {console.human_size(plan.required_bytes)}; "
                     f"free on {to_root}: {console.human_size(plan.free_bytes)}")
        try:
            check_space(plan)
            console.success("Space check: OK")
        except InsufficientSpaceError as exc:
            console.error(str(exc))
        console.info(f"Review {cfg.migrate_manifest_path}, then run 'tidyup migrate --to {to_root}'.")
        return 0

    mpath = Path(args.manifest) if getattr(args, "manifest", None) else cfg.migrate_manifest_path
    if not os.path.isfile(_lp(mpath)):
        raise TidyupError(f"Run 'tidyup migrate --to {to_root} --dry-run' first and review "
                          f"{cfg.migrate_manifest_path.name}")
    if not os.path.isdir(_lp(to_root)):
        raise TidyupError(f"The target drive or folder {to_root} does not exist.")
    rows = models.read_manifest(mpath)
    plan = _remaining_plan(cfg, rows, to_root)
    try:
        check_space(plan)
    except InsufficientSpaceError as exc:
        console.error(str(exc))
        return 1
    console.info(f"{len(plan.rows)} item(s) left to migrate to {to_root}.")
    for r in plan.rows[:20]:
        console.info(f"  {r.source} -> {r.destination}")
    if not plan.rows:
        console.success("Nothing left to migrate.")
        return 0
    if not console.confirm(f"Migrate {len(plan.rows)} item(s) to {to_root}?",
                           assume_yes=bool(getattr(args, "yes", False))):
        console.info("Cancelled. Nothing was moved.")
        return 1
    res = run_migration(cfg, rows, to_root)
    console.success(f"Moved {res.moved}, skipped {res.skipped}, errors {res.errors}.")
    if res.run_id:
        console.info(f"To reverse this run: tidyup undo {res.run_id}")
    return 1 if res.errors else 0
