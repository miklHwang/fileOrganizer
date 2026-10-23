"""Exact duplicates, version chains, near-duplicates (stub; filled by P2-T4)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from tidyup.config import Config
    from tidyup.models import FileRecord


@dataclass
class DuplicateGroup:
    sha256: str
    keeper: FileRecord
    duplicates: list[FileRecord]


@dataclass
class VersionChain:
    key: str
    current: FileRecord
    older: list[FileRecord]


@dataclass
class NearDuplicateGroup:
    members: list[FileRecord]
    max_distance: int


def exact_duplicate_groups(records: Sequence[FileRecord], cfg: Config) -> list[DuplicateGroup]:
    raise NotImplementedError


def choose_keeper(records: Sequence[FileRecord], cfg: Config) -> FileRecord:
    raise NotImplementedError


def normalize_version_stem(stem: str, patterns: Sequence[str]) -> str:
    raise NotImplementedError


def version_chains(records: Sequence[FileRecord], cfg: Config,
                   exclude: set[Path] = frozenset()) -> list[VersionChain]:
    raise NotImplementedError


def near_duplicate_groups(records: Sequence[FileRecord], cfg: Config) -> list[NearDuplicateGroup]:
    raise NotImplementedError
