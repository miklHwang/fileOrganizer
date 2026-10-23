"""tidyup plan: turn the inventory into a reviewable manifest.csv."""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tidyup import console, dupes, fsops, models, rules, winutil
from tidyup.errors import TidyupError
from tidyup.models import Action, Confidence, EntryType, ManifestRow

if TYPE_CHECKING:
    import argparse

    from tidyup.config import Config
    from tidyup.models import FileRecord

ONEDRIVE_WARNING = "WARNING: moves this file OUT of OneDrive (it will no longer be in the cloud)"


@dataclass
class PlanResult:
    rows: list[ManifestRow]
    warnings: list[str]
    onedrive_exits: list[ManifestRow]
    counts: dict[str, int]


def _key(p: Path) -> str:
    return winutil.norm_key(p)


def _atomic_destination(cfg: Config, rec: FileRecord) -> Path:
    root = next((s.path for s in cfg.sources if s.label == rec.source_root), None)
    rel: Path
    if root is not None and winutil.is_within(rec.path, root):
        rel = rec.path.relative_to(root) if _is_relative(rec.path, root) else Path(rec.path.name)
    else:
        rel = Path(rec.path.name)
    return cfg.dest_root.joinpath(rec.source_root or "Other", rel)  # type: ignore[union-attr]


def _is_relative(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def build_manifest(cfg: Config, records: Sequence[FileRecord], *,
                   now: datetime | None = None) -> PlanResult:
    now = now or datetime.now()
    ctx = rules.RuleContext(cfg, now)

    keeper_of: dict[str, tuple[FileRecord, str]] = {}
    for g in dupes.exact_duplicate_groups(records, cfg):
        for d in g.duplicates:
            keeper_of[_key(d.path)] = (g.keeper, g.sha256)
    non_keeper_paths = {r.path for r in records if _key(r.path) in keeper_of}
    older_of: dict[str, FileRecord] = {}
    for c in dupes.version_chains(records, cfg, exclude=non_keeper_paths):
        for o in c.older:
            older_of[_key(o.path)] = c.current
    near_of: dict[str, int] = {}
    for n, g in enumerate(dupes.near_duplicate_groups(records, cfg), start=1):
        for m in g.members:
            near_of[_key(m.path)] = n

    reserved: set[str] = set()
    rows: list[ManifestRow] = []
    exits: list[ManifestRow] = []
    warnings: list[str] = []

    for rec in sorted(records, key=lambda r: str(r.path).casefold()):
        src = rec.path
        k = _key(src)
        action = Action.SKIP
        dest: Path | None = None
        rule = "unsorted"
        conf = Confidence.LOW
        notes: list[str] = []
        force_n = False

        if rec.error:
            rule, notes = "scan_error", [f"not planned: {rec.error}"]
        elif rec.entry_type == EntryType.ATOMIC_FOLDER:
            rule, conf = "atomic_folder", Confidence.HIGH
            if cfg.dest_root is None:
                notes = ["atomic folder: left as one unit"]
            else:
                action, conf = Action.MOVE, Confidence.MEDIUM
                dest = _atomic_destination(cfg, rec)
                notes = ["atomic folder: moved whole, contents untouched"]
        elif k in keeper_of:
            keeper, sha = keeper_of[k]
            action, rule, conf = Action.DUPLICATE, "exact_duplicate", Confidence.HIGH
            dest = cfg.resolve_destination(cfg.structure.get("duplicates", "_Duplicates"), src) / src.name
            notes = [f"duplicate of {keeper.path} (sha256 {sha[:12]})"]
        else:
            p = rules.classify(rec, ctx)
            rule, conf, notes = p.rule, p.confidence, list(p.notes)
            if p.confidence != Confidence.HIGH and rules.is_in_target_structure(src, cfg):
                rule, notes = "already_filed", notes + ["already in the target folder structure"]
            elif p.kind == "skip":
                pass
            elif p.kind == "review_delete":
                action = Action.REVIEW_DELETE
                if cfg.delete_mode == "recycle_bin":
                    notes.append("will be sent to the Recycle Bin")
                else:
                    dest = cfg.resolve_destination(p.logical_dir, src) / src.name  # type: ignore[arg-type]
            elif p.kind == "file":
                name = p.new_name or src.name
                target = cfg.resolve_destination(p.logical_dir, src) / name  # type: ignore[arg-type]
                same_dir = _key(target.parent) == _key(src.parent)
                if same_dir and name == src.name:
                    rule, notes = "already_in_place", notes + ["already in the right place"]
                else:
                    dest = target
                    action = (Action.RENAME if same_dir else Action.MOVE if name == src.name
                              else Action.MOVE_RENAME)

        if dest is not None and _key(dest) == k:
            action, dest, rule = Action.SKIP, None, "already_in_place"
            notes.append("already in the right place")

        # OneDrive handling
        if dest is not None and action != Action.SKIP:
            leaves = cfg.in_onedrive(src) and not cfg.in_onedrive(dest)
            cross_drive = rec.is_cloud_only and not winutil.same_volume(src, dest.parent)
            if rec.is_cloud_only and (leaves or cross_drive):
                action, dest, rule, conf = Action.SKIP, None, "cloud_only_leave_onedrive", Confidence.HIGH
                notes.append("cloud-only file stays in OneDrive: moving it would force a download")
            elif leaves:
                force_n = True
                notes.insert(0, ONEDRIVE_WARNING)

        if dest is not None:
            final = fsops.unique_path(dest, reserved)
            if final != dest:
                notes.append(f"name collision: will be saved as '{final.name}'")
            dest = final

        if k in older_of:
            notes.append(f"likely old version of {older_of[k].path}")
            if action != Action.SKIP:
                force_n = True
        if k in near_of:
            notes.append(f"near-duplicate group G{near_of[k]} (see report)")
        if rec.is_cloud_only and "cloud-only" not in notes:
            notes.append("cloud-only")

        approved = action != Action.SKIP and conf == Confidence.HIGH and not force_n
        row = ManifestRow(id=len(rows) + 1, action=action, source=src, destination=dest, rule=rule,
                          confidence=conf, notes="; ".join(notes), approved=approved)
        rows.append(row)
        if force_n and ONEDRIVE_WARNING in notes:
            exits.append(row)
            warnings.append(f"{src} -> {dest}: {ONEDRIVE_WARNING}")

    counts: dict[str, int] = dict(Counter(f"{r.action.value}/{r.confidence.value}" for r in rows))
    counts["approved"] = sum(1 for r in rows if r.approved)
    return PlanResult(rows=rows, warnings=warnings, onedrive_exits=exits, counts=counts)


def command(cfg: Config, args: argparse.Namespace) -> int:
    if not Path(winutil.long_path(cfg.inventory_path)).exists():
        raise TidyupError("No inventory found. Run 'tidyup scan' first.")
    records = models.read_inventory(cfg.inventory_path)
    res = build_manifest(cfg, records)

    console.heading("Plan summary")
    table: dict[str, dict[str, int]] = {}
    for r in res.rows:
        table.setdefault(r.action.value, Counter())[r.confidence.value] += 1  # type: ignore[arg-type]
    console.table(["action", "high", "medium", "low", "total"],
                  [[a, c["high"], c["medium"], c["low"], sum(c.values())] for a, c in sorted(table.items())])
    console.info(f"{len(res.rows)} rows, {res.counts['approved']} approved by default (high confidence).")

    if res.onedrive_exits:
        console.warn(f"WARNING: {len(res.onedrive_exits)} file(s) would move OUT of OneDrive "
                     "and no longer be in the cloud. They are marked N (not approved).")
        for r in res.onedrive_exits[:20]:
            console.warn(f"  {r.source} -> {r.destination}")
        if len(res.onedrive_exits) > 20:
            console.warn(f"  ... and {len(res.onedrive_exits) - 20} more")

    if getattr(args, "dry_run", False):
        console.info("Dry run: manifest not written.")
        return 0
    n = models.write_manifest(cfg.manifest_path, res.rows, cfg.csv.get("delimiter", ","))
    console.success(f"Manifest with {n} rows written to {cfg.manifest_path}")
    console.info("Next: review manifest.csv in Excel (set approved to Y or N) and run 'tidyup report', "
                 "then 'tidyup apply'.")
    return 0
