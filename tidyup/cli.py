"""Command-line entry point: argument parsing, config resolution, dispatch, friendly errors."""
from __future__ import annotations

import argparse
import importlib
import traceback
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from tidyup import __version__, console, errors

_COMMAND_DEFAULTS: dict[str, object] = {
    "open": False, "manifest": None, "limit": None, "only_rule": None,
    "only_folder": None, "yes": False, "run_id": None, "list": False, "to": None,
}


def _common_parent(suppress: bool) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    d = argparse.SUPPRESS if suppress else None
    p.add_argument("--config", metavar="PATH", default=d if suppress else None,
                   help="path to config.yaml (default: config.yaml in the current folder)")
    p.add_argument("--dry-run", action="store_true", default=d if suppress else False,
                   help="show what would happen without changing anything")
    p.add_argument("--debug", action="store_true", default=d if suppress else False,
                   help="show full error details (for troubleshooting)")
    return p


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tidyup", description="Safe, reviewable file organizer.",
        parents=[_common_parent(False)])
    parser.add_argument("--version", action="version", version=f"tidyup {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    shared = _common_parent(True)

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_, description=help_, parents=[shared])

    add("scan", "Read-only: list your files into inventory.csv")
    add("plan", "Decide where files should go and write manifest.csv")
    p = add("report", "Build the report.html overview")
    p.add_argument("--open", action="store_true", help="open the report in your browser")
    p = add("apply", "Carry out the approved rows of the manifest")
    p.add_argument("--manifest", metavar="PATH", help="manifest file to use")
    p.add_argument("--limit", type=int, metavar="N", help="only do the first N approved rows")
    p.add_argument("--only-rule", metavar="NAME", help="only rows from this rule")
    p.add_argument("--only-folder", metavar="NAME", help="only rows from this source folder")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p = add("undo", "Reverse a previous run")
    p.add_argument("run_id", nargs="?", help="run to undo (default: the latest)")
    p.add_argument("--list", action="store_true", help="list runs that can be undone")
    p = add("migrate", "Move organized files to another drive")
    p.add_argument("--to", required=True, metavar="PATH", help="destination, e.g. D:\\")
    p.add_argument("--manifest", metavar="PATH", help="reviewed migration manifest")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p = add("upkeep", "Quick on-demand tidy of Downloads, Desktop and Screenshots")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    return parser


def _load_config(args: argparse.Namespace):
    from tidyup import config

    if args.config:
        path = Path(args.config)
        if not path.is_file():
            raise errors.ConfigError(f"Config file not found: {path}")
        return config.load_config(path)
    default = config.find_default_config()
    return config.load_config(default)


def _write_crash(work_dir: Path) -> Path | None:
    try:
        logs = work_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / f"crash_{datetime.now():%Y%m%d-%H%M%S}.txt"
        path.write_text(traceback.format_exc(), encoding="utf-8")
        return path
    except OSError:
        return None


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for key, value in _COMMAND_DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    work_dir: Path | None = None
    try:
        cfg = _load_config(args)
        work_dir = cfg.work_dir
        module = importlib.import_module(f"tidyup.{args.command}")
        return int(module.command(cfg, args) or 0)
    except errors.TidyupError as e:
        if args.debug:
            raise
        console.error(str(e))
        return 1
    except KeyboardInterrupt:
        console.info("Interrupted. Changes made so far are logged and can be reversed "
                     "with 'tidyup undo'.")
        return 130
    except Exception as e:
        if args.debug:
            raise
        crash = _write_crash(work_dir or Path.cwd())
        console.error(f"Something unexpected went wrong ({type(e).__name__}: {e}).")
        if crash:
            console.info(f"Technical details were saved to {crash}")
        return 1
