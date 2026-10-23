"""Safe file operations: the ONLY module that mutates user files (stub; filled by P2-T2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from tidyup.journal import Journal

PARTIAL_PREFIX = "~tidyup-partial-"


@dataclass(frozen=True)
class MoveResult:
    src: Path
    dst: Path
    cross_volume: bool
    is_dir: bool
    size: int
    sha256: str | None


def sha256_file(path: Path, *, is_cloud_only: bool = False) -> str:
    raise NotImplementedError


def reserve_key(path: Path) -> str:
    raise NotImplementedError


def unique_path(dst: Path, reserved: set[str] | None = None) -> Path:
    raise NotImplementedError


def ensure_dir(path: Path, journal: Journal | None = None, *, dry_run: bool = False) -> list[Path]:
    raise NotImplementedError


def move_file(src: Path, dst: Path, *, journal: Journal | None = None, row_id: str | None = None,
              action: str = "move", is_cloud_only: bool = False, adopt_identical: bool = False,
              dry_run: bool = False) -> MoveResult:
    raise NotImplementedError


def move_tree(src_dir: Path, dst_dir: Path, *, journal: Journal | None = None, row_id: str | None = None,
              action: str = "move", adopt_identical: bool = False, dry_run: bool = False) -> MoveResult:
    raise NotImplementedError


def trash(path: Path, *, journal: Journal | None = None, row_id: str | None = None,
          dry_run: bool = False) -> None:
    raise NotImplementedError


def remove_empty_dir(path: Path) -> bool:
    raise NotImplementedError
