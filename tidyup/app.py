"""Controller for the tidyup window: the task table and a safe way to run the CLI.

No tkinter here, so everything is testable headless. The window (gui.py) only calls
`run_cli` and `winutil.open_path`; every file change still happens in the command modules.
"""
from __future__ import annotations

import contextlib
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tidyup import cli, console
from tidyup.config import Config


@dataclass(frozen=True)
class Task:
    key: str
    label: str
    argv: list[str]
    preview_argv: list[str] | None
    confirm: str | None


def build_tasks(config_path: Path, *, migrate_to: Path | None = None) -> dict[str, Task]:
    base = ["--config", str(config_path)]

    def mk(key: str, label: str, argv: list[str], preview: list[str] | None = None,
           confirm: str | None = None) -> Task:
        return Task(key, label, base + argv,
                    None if preview is None else base + preview, confirm)

    tasks = [
        mk("scan", "1. Scan my files", ["scan"]),
        mk("plan", "2. Make a plan", ["plan"]),
        mk("report", "3. Open the report", ["report", "--open"]),
        mk("trial", "4. Try it: 50 files from Downloads",
           ["apply", "--only-folder", "downloads", "--limit", "50", "--yes"],
           ["--dry-run", "apply", "--only-folder", "downloads", "--limit", "50"],
           "Move these files now? You can undo this."),
        mk("undo", "5. Undo the last run", ["undo"], ["--dry-run", "undo"],
           "Put these files back where they were?"),
        mk("apply", "6. Do everything I approved", ["apply", "--yes"],
           ["--dry-run", "apply"], "Carry out all approved changes now? You can undo this."),
        mk("upkeep", "Quick tidy (Downloads, Desktop, Screenshots)", ["upkeep", "--yes"],
           ["--dry-run", "upkeep"], "Tidy these files now? You can undo this."),
    ]
    if migrate_to is not None:
        to = str(migrate_to)
        tasks.append(mk("migrate", "Move to another drive...",
                        ["migrate", "--to", to, "--yes"],
                        ["--dry-run", "migrate", "--to", to],
                        f"Copy, check and move these folders to {to} now?"))
    tasks.append(mk("runs", "Show past runs", ["undo", "--list"]))
    return {t.key: t for t in tasks}


def run_cli(argv: Sequence[str], sink: console.Sink) -> int:
    """Run cli.main with console output sent to `sink`. Never raises."""
    previous = console.get_sink()
    console.set_sink(sink)
    err = io.StringIO()
    try:
        try:
            with contextlib.redirect_stderr(err):
                return int(cli.main(list(argv)) or 0)
        finally:
            text = err.getvalue().strip()
            if text:
                sink("error", text)
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1
    except BaseException as e:  # noqa: BLE001 - the window must never crash
        try:
            sink("error", f"Error: Something unexpected went wrong ({type(e).__name__}: {e}).")
        except Exception:  # noqa: BLE001
            pass
        return 1
    finally:
        console.set_sink(previous)


def plan_exists(cfg: Config) -> bool:
    return cfg.manifest_path.exists()


def work_paths(cfg: Config) -> dict[str, Path]:
    config = cfg.path if cfg.path is not None else cfg.config_dir / "config.yaml"
    return {"config": config, "work_dir": cfg.work_dir, "manifest": cfg.manifest_path,
            "report": cfg.report_path, "migrate_manifest": cfg.migrate_manifest_path}
