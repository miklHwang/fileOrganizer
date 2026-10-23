"""tidyup migrate (stub; filled by P3-T6)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from tidyup.apply import ApplyResult
    from tidyup.config import Config
    from tidyup.models import ManifestRow


@dataclass
class MigratePlan:
    to_root: Path
    rows: list[ManifestRow]
    required_bytes: int
    free_bytes: int


def build_migration(cfg: Config, to_root: Path) -> MigratePlan:
    raise NotImplementedError


def check_space(plan: MigratePlan) -> None:
    raise NotImplementedError


def run_migration(cfg: Config, rows: Sequence[ManifestRow], to_root: Path, *,
                  dry_run: bool = False) -> ApplyResult:
    raise NotImplementedError


def command(cfg: Config, args: argparse.Namespace) -> int:
    raise NotImplementedError
