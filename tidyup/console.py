"""Human-friendly console output. The only module allowed to use bare print()."""
from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator, Sequence
from typing import TypeVar

T = TypeVar("T")


def info(msg: str) -> None:
    print(msg, file=sys.stdout)


def success(msg: str) -> None:
    print("OK: " + msg, file=sys.stdout)


def warn(msg: str) -> None:
    print("WARNING: " + msg, file=sys.stdout)


def error(msg: str) -> None:
    print("Error: " + msg, file=sys.stderr)


def heading(msg: str) -> None:
    print()
    print(msg)
    print("-" * len(msg))


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    cells = [[str(h) for h in headers]] + [[str(c) for c in r] for r in rows]
    ncols = max(len(r) for r in cells)
    for r in cells:
        r.extend([""] * (ncols - len(r)))
    widths = [max(len(r[i]) for r in cells) for i in range(ncols)]
    for n, r in enumerate(cells):
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip())
        if n == 0:
            print("  ".join("-" * w for w in widths))


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    size = float(n)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        size /= 1024
        if size < 1024 or unit == "PB":
            return f"{size:.1f} {unit}"
    return f"{size:.1f} PB"  # pragma: no cover


def progress(iterable: Iterable[T], *, desc: str, total: int | None = None,
             unit: str = "file") -> Iterator[T]:
    from tqdm import tqdm

    yield from tqdm(iterable, desc=desc, total=total, unit=unit, disable=None)


def confirm(prompt: str, *, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return False
        answer = input(f"{prompt} [y/N] ")
    except (EOFError, OSError):
        return False
    return answer.strip().lower() in ("y", "yes")
