"""tidyup apply: executes approved manifest rows, journaling every action for undo."""
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tidyup import console, fsops, journal as jr, safety, winutil
from tidyup.errors import (CloudOnlyError, FileLockedError, TidyupError, UnsafePathError,
                           VerifyError)
from tidyup.models import Action, Confidence, ManifestRow, read_manifest

if TYPE_CHECKING:
    import argparse

    from tidyup.config import Config

_RANK = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}


@dataclass
class ApplyResult:
    run_id: str | None
    log_path: Path | None
    moved: int
    trashed: int
    skipped: int
    errors: int
    dry_run: bool


def confirm_changes(count: int, *, assume_yes: bool = False, what: str = "changes") -> bool:
    """Ask 'Apply N <what>?'. Reusable by upkeep. Returns False (do nothing) if refused or non-interactive."""
    return console.confirm(f"Apply {count} {what}?", assume_yes=assume_yes)


def _rank(c: Confidence | str) -> int:
    return _RANK[Confidence(c)]


def select_rows(rows: Sequence[ManifestRow], cfg: Config, *, limit: int | None = None,
                only_rule: str | None = None, only_folder: str | None = None,
                actions: set[Action] | None = None,
                min_confidence: Confidence | None = None) -> list[ManifestRow]:
    folder: Path | None = None
    if only_folder is not None:
        folder = cfg.folders.get(only_folder)
        if folder is None:
            if only_folder in cfg.folders:
                raise TidyupError(f"The folder '{only_folder}' is not set up on this PC.")
            folder = Path(only_folder)
    out: list[ManifestRow] = []
    for r in sorted(rows, key=lambda r: r.id):
        if not r.approved or r.action == Action.SKIP:
            continue
        if only_rule is not None and r.rule.casefold() != only_rule.casefold():
            continue
        if folder is not None and not winutil.is_within(r.source, folder):
            continue
        if actions is not None and r.action not in actions:
            continue
        if min_confidence is not None and _rank(r.confidence) < _rank(min_confidence):
            continue
        out.append(r)
    if limit is not None:
        out = out[:max(limit, 0)]
    return out


def _do_row(cfg: Config, row: ManifestRow, journal, dry_run: bool) -> str:
    """Returns 'moved' or 'trashed'. Raises on failure."""
    src = row.source
    if not os.path.lexists(winutil.long_path(src)):
        raise FileNotFoundError("source no longer exists")
    is_dir = os.path.isdir(winutil.long_path(src)) and not os.path.islink(winutil.long_path(src))
    safety.check_source(src, cfg, is_dir=is_dir)
    rid = str(row.id)
    if row.action == Action.REVIEW_DELETE and cfg.delete_mode == "recycle_bin":
        fsops.trash(src, journal=journal, row_id=rid, dry_run=dry_run)
        return "trashed"
    dst = row.destination
    if (dst is None or str(dst) in ("", ".")) and row.action == Action.REVIEW_DELETE:
        dst = cfg.resolve_destination("_Review_Delete", src) / src.name
    if dst is None or str(dst) in ("", "."):
        raise OSError("manifest row has no destination")
    if winutil.norm_key(dst) == winutil.norm_key(src):
        raise UnsafePathError("destination is the same path as the source")
    if is_dir and winutil.is_within(src, dst):
        raise UnsafePathError("source folder is inside the destination")
    safety.check_destination(dst, cfg)
    if is_dir:
        if winutil.is_within(dst, src):
            raise OSError("destination is inside the folder being moved")
        fsops.move_tree(src, dst, journal=journal, row_id=rid, action=row.action.value,
                        dry_run=dry_run)
    else:
        fsops.move_file(src, dst, journal=journal, row_id=rid, action=row.action.value,
                        is_cloud_only=winutil.is_cloud_only(src), dry_run=dry_run)
    return "moved"


def apply_rows(cfg: Config, rows: Sequence[ManifestRow], *, kind: str = "apply",
               manifest_path: Path | None = None, dry_run: bool = False) -> ApplyResult:
    journal = None
    if not dry_run:
        journal = jr.Journal.create(cfg.logs_dir, kind, {
            "config": cfg.path, "manifest": manifest_path, "rows": len(rows)})
    counts = {"moved": 0, "trashed": 0, "skipped": 0, "errors": 0}

    def finish() -> None:
        if journal is not None:
            journal.finish(dict(counts))

    try:
        for row in rows:
            if not row.approved or row.action == Action.SKIP:
                counts["skipped"] += 1
                if journal is not None:
                    journal.skip(src=row.source, row_id=str(row.id),
                                 reason="row is not approved or is a skip row")
                continue
            try:
                counts[_do_row(cfg, row, journal, dry_run)] += 1
            except (UnsafePathError, CloudOnlyError, FileLockedError, FileNotFoundError) as exc:
                counts["skipped"] += 1
                reason = str(exc) or type(exc).__name__
                console.warn(f"Skipped {row.source}: {reason}")
                if journal is not None:
                    journal.skip(src=row.source, row_id=str(row.id), reason=reason)
            except (VerifyError, OSError, TidyupError) as exc:
                counts["errors"] += 1
                reason = str(exc) or type(exc).__name__
                console.error(f"Could not process {row.source}: {reason}")
                if journal is not None:
                    journal.error(src=row.source, row_id=str(row.id), reason=reason)
    except KeyboardInterrupt:
        finish()
        raise
    finish()
    return ApplyResult(run_id=journal.run_id if journal else None,
                       log_path=journal.path if journal else None,
                       moved=counts["moved"], trashed=counts["trashed"],
                       skipped=counts["skipped"], errors=counts["errors"], dry_run=dry_run)


def command(cfg: Config, args: argparse.Namespace) -> int:
    manifest = Path(args.manifest) if getattr(args, "manifest", None) else cfg.manifest_path
    if not os.path.exists(winutil.long_path(manifest)):
        raise TidyupError(f"No manifest found at {manifest}. Run 'tidyup plan' first.")
    all_rows = read_manifest(manifest)
    rows = select_rows(all_rows, cfg, limit=args.limit, only_rule=args.only_rule,
                       only_folder=args.only_folder)
    if not rows:
        console.info("No approved rows to apply. Mark rows approved (Y) in the manifest first.")
        return 0
    dry_run = bool(getattr(args, "dry_run", False))
    console.heading(f"{len(rows)} approved change(s) selected")
    for r in rows[:20]:
        console.info(f"  [{r.id}] {r.action.value}: {r.source} -> {r.destination or '(review/trash)'}")
    if len(rows) > 20:
        console.info(f"  ... and {len(rows) - 20} more")
    if cfg.delete_mode == "recycle_bin" and any(r.action == Action.REVIEW_DELETE for r in rows):
        console.warn("Items sent to the Recycle Bin cannot be restored by 'tidyup undo'; "
                     "restore them from the Recycle Bin yourself.")
    if not dry_run and not confirm_changes(len(rows), assume_yes=bool(args.yes)):
        console.info("Nothing was changed.")
        return 1
    res = apply_rows(cfg, rows, manifest_path=manifest, dry_run=dry_run)
    verb = "Would apply" if dry_run else "Done"
    console.success(f"{verb}: {res.moved} moved, {res.trashed} sent to Recycle Bin, "
                    f"{res.skipped} skipped, {res.errors} errors.")
    if res.run_id:
        console.info(f"To reverse this run: tidyup undo {res.run_id}")
    return 1 if res.errors else 0
