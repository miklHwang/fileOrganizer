"""Command-line entry point (stub: only --version until P2-T1)."""
from __future__ import annotations

import argparse
from collections.abc import Sequence

from tidyup import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tidyup", description="Safe, reviewable file organizer.")
    parser.add_argument("--version", action="version", version=f"tidyup {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
