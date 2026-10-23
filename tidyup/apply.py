"""tidyup apply (stub; filled by P3-T3)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from tidyup.config import Config
    from tidyup.models import Action, Confidence, ManifestRow


@dataclass
class ApplyResult:
    run_id: str | None
    log_path: Path | None
    moved: int
    trashed: int
    skipped: int
    errors: int
    dry_run: bool


def select_rows(rows: Sequence[ManifestRow], cfg: Config, *, limit: int | None = None,
                only_rule: str | None = None, only_folder: str | None = None,
                actions: set[Action] | None = None,
                min_confidence: Confidence | None = None) -> list[ManifestRow]:
    raise NotImplementedError


def apply_rows(cfg: Config, rows: Sequence[ManifestRow], *, kind: str = "apply",
               manifest_path: Path | None = None, dry_run: bool = False) -> ApplyResult:
    raise NotImplementedError


def command(cfg: Config, args: argparse.Namespace) -> int:
    raise NotImplementedError
