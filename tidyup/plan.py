"""tidyup plan (stub; filled by P4-T1)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    from datetime import datetime

    from tidyup.config import Config
    from tidyup.models import FileRecord, ManifestRow


@dataclass
class PlanResult:
    rows: list[ManifestRow]
    warnings: list[str]
    onedrive_exits: list[ManifestRow]
    counts: dict[str, int]


def build_manifest(cfg: Config, records: Sequence[FileRecord], *,
                   now: datetime | None = None) -> PlanResult:
    raise NotImplementedError


def command(cfg: Config, args: argparse.Namespace) -> int:
    raise NotImplementedError
