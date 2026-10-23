"""tidyup undo (stub; filled by P3-T4)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

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
    raise NotImplementedError


def command(cfg: Config, args: argparse.Namespace) -> int:
    raise NotImplementedError
