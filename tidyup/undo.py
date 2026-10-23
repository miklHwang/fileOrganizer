"""tidyup undo: reverse a recorded run from its journal."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tidyup import console, fsops, journal, winutil
from tidyup.errors import TidyupError

if TYPE_CHECKING:
    import argparse

    from tidyup.config import Config
    from tidyup.journal import RunLog


@dataclass
class UndoResult:
    undone_run_id: str
    run_id: str | None
    log_path: Path | None
    restored: int
    skipped: int
    errors: int
    manual: list[Path]
    dry_run: bool


def undo_run(cfg: Config, run: RunLog, *, dry_run: bool = False) -> UndoResult:
    jnl = None
    if not dry_run:
        jnl = journal.Journal.create(cfg.logs_dir, "undo", {"undoes": run.run_id})
    restored = skipped = errors = 0
    manual: list[Path] = []
    try:
        for op in reversed(run.ops):
            kind = op.get("type")
            try:
                if kind == "trash":
                    manual.append(Path(op["src"]))
                elif kind == "mkdir":
                    path = Path(op["path"])
                    if dry_run:
                        continue
                    if os.path.lexists(winutil.long_path(path)) and not fsops.remove_empty_dir(path):
                        console.info(f"Left folder in place (not empty): {path}")
                elif kind == "move":
                    src, dst = Path(op["src"]), Path(op["dst"])
                    if not os.path.lexists(winutil.long_path(dst)):
                        skipped += 1
                        if jnl:
                            jnl.skip(src=dst, row_id=op.get("row_id"), reason="moved item no longer exists")
                        continue
                    target = src
                    if os.path.lexists(winutil.long_path(src)):
                        target = fsops.unique_path(src)
                        console.warn(f"{src} is occupied; restoring to {target} instead.")
                    if dry_run:
                        restored += 1
                        continue
                    fsops.ensure_dir(target.parent, jnl)
                    if op.get("is_dir"):
                        fsops.move_tree(dst, target, journal=jnl, row_id=op.get("row_id"), action="undo")
                    else:
                        fsops.move_file(dst, target, journal=jnl, row_id=op.get("row_id"), action="undo")
                    restored += 1
            except (TidyupError, OSError) as exc:
                errors += 1
                console.warn(f"Could not undo {op.get('src') or op.get('path')}: {exc}")
                if jnl:
                    jnl.error(src=Path(op.get("dst") or op.get("src") or op.get("path") or ""),
                              row_id=op.get("row_id"), reason=str(exc))
        if jnl:
            jnl.finish({"restored": restored, "skipped": skipped, "errors": errors})
    finally:
        if jnl:
            jnl.close()
    if jnl:
        journal.mark_undone(run, jnl.run_id)
    return UndoResult(undone_run_id=run.run_id, run_id=jnl.run_id if jnl else None,
                      log_path=jnl.path if jnl else None, restored=restored, skipped=skipped,
                      errors=errors, manual=manual, dry_run=dry_run)


def _status(r: RunLog) -> str:
    if r.undone_by:
        return f"undone by {r.undone_by}"
    return "finished" if r.finished else "unfinished"


def command(cfg: Config, args: argparse.Namespace) -> int:
    run_id = getattr(args, "run_id", None)
    if getattr(args, "list", False):
        runs = journal.list_runs(cfg.logs_dir)
        if not runs:
            console.info("No runs found.")
            return 0
        console.table(["run_id", "kind", "started", "ops", "status"],
                      [(r.run_id, r.kind, r.header.get("started", ""), len(r.ops), _status(r)) for r in runs])
        return 0
    run = journal.find_run(cfg.logs_dir, run_id)
    if run.undone_by:
        if run_id is None:
            raise TidyupError(f"Run {run.run_id} was already undone.")
        console.warn(f"Run {run.run_id} was already undone by {run.undone_by}; continuing as requested.")
    if not run.finished:
        console.warn(f"Run {run.run_id} did not finish; undoing what it recorded.")
    dry = bool(getattr(args, "dry_run", False))
    res = undo_run(cfg, run, dry_run=dry)
    verb = "Would restore" if dry else "Restored"
    console.success(f"{verb} {res.restored}; skipped {res.skipped}; errors {res.errors}.")
    if res.manual:
        console.warn("These items were sent to the Recycle Bin; restore them by hand:")
        for p in res.manual:
            console.info(f"  {p}")
    if res.run_id:
        console.info(f"Undo log: {res.log_path} (run {res.run_id}; 'tidyup undo {res.run_id}' re-applies it)")
    return 1 if res.errors else 0
