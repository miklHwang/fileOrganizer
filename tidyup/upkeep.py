"""tidyup upkeep: on-demand quick tidy, no scheduled task (stub; filled by P4-T2)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

    from tidyup.apply import ApplyResult
    from tidyup.config import Config


def run_upkeep(cfg: Config, *, dry_run: bool = False) -> ApplyResult:
    raise NotImplementedError


def command(cfg: Config, args: argparse.Namespace) -> int:
    raise NotImplementedError
