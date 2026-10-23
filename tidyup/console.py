"""Human-friendly console output. The only module allowed to use bare print()."""
from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import TypeVar

T = TypeVar("T")

Sink = Callable[[str, str], None]
_sink: Sink | None = None


def set_sink(sink: Sink | None) -> None:
    """Send all output to `sink(kind, text)`; None restores normal printing."""
    global _sink
    _sink = sink


def get_sink() -> Sink | None:
    return _sink


def info(msg: str) -> None:
    if _sink is not None:
        _sink("info", msg)
        return
    print(msg, file=sys.stdout)


def success(msg: str) -> None:
    if _sink is not None:
        _sink("success", "OK: " + msg)
        return
    print("OK: " + msg, file=sys.stdout)


def warn(msg: str) -> None:
    if _sink is not None:
        _sink("warn", "WARNING: " + msg)
        return
    print("WARNING: " + msg, file=sys.stdout)


def error(msg: str) -> None:
    if _sink is not None:
        _sink("error", "Error: " + msg)
        return
    print("Error: " + msg, file=sys.stderr)


def heading(msg: str) -> None:
    if _sink is not None:
        _sink("heading", msg + "\n" + "-" * len(msg))
        return
    print()
    print(msg)
    print("-" * len(msg))


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    cells = [[str(h) for h in headers]] + [[str(c) for c in r] for r in rows]
    ncols = max(len(r) for r in cells)
    for r in cells:
        r.extend([""] * (ncols - len(r)))
    widths = [max(len(r[i]) for r in cells) for i in range(ncols)]
    lines: list[str] = []
    for n, r in enumerate(cells):
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    if _sink is not None:
        _sink("table", "\n".join(lines))
        return
    for line in lines:
        print(line)


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
    sink = _sink
    if sink is None and sys.stderr is not None:
        from tqdm import tqdm

        yield from tqdm(iterable, desc=desc, total=total, unit=unit, disable=None)
        return
    if sink is None:
        yield from iterable
        return
    if total is None:
        try:
            total = len(iterable)  # type: ignore[arg-type]
        except TypeError:
            total = None
    shown = total if total else "?"
    done = 0
    last = time.monotonic()
    for item in iterable:
        yield item
        done += 1
        now = time.monotonic()
        if now - last >= 0.25:
            last = now
            sink("progress", f"{desc}: {done}/{shown}")
    sink("progress", f"{desc}: {done}/{shown}")


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
