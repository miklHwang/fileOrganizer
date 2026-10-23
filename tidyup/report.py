"""tidyup report (stub; filled by P3-T5)."""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

    from tidyup.config import Config
    from tidyup.models import FileRecord, ManifestRow


def build_report(cfg: Config, records: Sequence[FileRecord], rows: Sequence[ManifestRow] | None) -> str:
    raise NotImplementedError


def command(cfg: Config, args: argparse.Namespace) -> int:
    raise NotImplementedError
