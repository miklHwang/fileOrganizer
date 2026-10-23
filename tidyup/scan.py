"""tidyup scan (stub; filled by P3-T1)."""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from tidyup.config import Config
    from tidyup.models import FileRecord


@dataclass
class ScanResult:
    records: list[FileRecord]
    skipped: dict[str, int]
    errors: list[tuple[Path, str]]


def walk_sources(cfg: Config) -> Iterator[FileRecord]:
    raise NotImplementedError


def scan_sources(cfg: Config, *, show_progress: bool = True) -> ScanResult:
    raise NotImplementedError


def summarize(records: Sequence[FileRecord]) -> dict:
    raise NotImplementedError


def command(cfg: Config, args: argparse.Namespace) -> int:
    raise NotImplementedError
