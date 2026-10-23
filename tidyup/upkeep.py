"""tidyup upkeep: on-demand quick tidy of Downloads, Desktop and Screenshots (no scheduled task)."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tidyup import apply, console, plan, scan, winutil
from tidyup.models import Action, Confidence, ManifestRow, write_manifest

if TYPE_CHECKING:
    import argparse

    from tidyup.apply import ApplyResult
    from tidyup.config import Config

# Upkeep may never auto-apply these, whatever the config says.
_NEVER = {Action.REVIEW_DELETE, Action.DUPLICATE, Action.SKIP}


def _prepare(cfg: Config, dry_run: bool = False) -> tuple[Config, list[ManifestRow], Path]:
    ucfg = cfg.with_sources(cfg.upkeep["sources"])
    result = scan.scan_sources(ucfg, show_progress=False)
    rows = plan.build_manifest(ucfg, result.records).rows
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = cfg.logs_dir / f"upkeep_manifest_{stamp}.csv"
    if not dry_run:  # dry-run writes nothing at all
        os.makedirs(winutil.long_path(cfg.logs_dir), exist_ok=True)
        write_manifest(path, rows, cfg.csv.get("delimiter", ","))
    actions = {Action(a) for a in cfg.upkeep["actions"]} - _NEVER
    selected = apply.select_rows(rows, ucfg, actions=actions, min_confidence=Confidence.HIGH)
    return ucfg, selected, path


def _execute(cfg: Config, ucfg: Config, selected: list[ManifestRow], path: Path,
             dry_run: bool) -> ApplyResult:
    res = apply.apply_rows(ucfg, selected, kind="upkeep", manifest_path=path, dry_run=dry_run)
    if not dry_run:
        line = (f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} run={res.run_id} "
                f"selected={len(selected)} moved={res.moved} skipped={res.skipped} "
                f"errors={res.errors} manifest={path}\n")
        with open(winutil.long_path(cfg.logs_dir / "upkeep.log"), "a", encoding="utf-8") as fh:
            fh.write(line)
    return res


def run_upkeep(cfg: Config, *, dry_run: bool = False) -> ApplyResult:
    """Scan, plan and apply high-confidence move/rename rows. No prompt: callers confirm first."""
    ucfg, selected, path = _prepare(cfg, dry_run)
    return _execute(cfg, ucfg, selected, path, dry_run)


def command(cfg: Config, args: argparse.Namespace) -> int:
    dry_run = bool(getattr(args, "dry_run", False))
    ucfg, selected, path = _prepare(cfg, dry_run)
    if not selected:
        console.info("Nothing to tidy: no high-confidence moves found in Downloads, Desktop or Screenshots.")
        return 0
    console.heading(f"{len(selected)} high-confidence change(s) selected")
    for r in selected[:20]:
        console.info(f"  [{r.id}] {r.action.value}: {r.source} -> {r.destination}")
    if len(selected) > 20:
        console.info(f"  ... and {len(selected) - 20} more")
    if not dry_run:
        console.info(f"Upkeep manifest: {path}")
    if not dry_run and not apply.confirm_changes(len(selected), assume_yes=bool(getattr(args, "yes", False)),
                                                 what="upkeep changes"):
        console.info("Nothing was changed.")
        return 1
    res = _execute(cfg, ucfg, selected, path, dry_run)
    verb = "Would apply" if dry_run else "Done"
    console.success(f"{verb}: {res.moved} moved, {res.skipped} skipped, {res.errors} errors.")
    if res.run_id:
        console.info(f"To reverse this run: tidyup undo {res.run_id}")
    return 1 if res.errors else 0
