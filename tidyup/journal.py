"""Undo journal: JSONL writer/reader. Every line is flushed and fsync'd."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tidyup import __version__, winutil
from tidyup.errors import TidyupError

_OP_TYPES = {"mkdir", "move", "trash"}


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _s(path: Path | str | None) -> str | None:
    if path is None:
        return None
    s = str(path)
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[8:]
    if s.startswith("\\\\?\\"):
        return s[4:]
    return s


def _log_path(logs_dir: Path, run_id: str) -> Path:
    return Path(logs_dir) / f"undo_{run_id}.jsonl"


def new_run_id(logs_dir: Path) -> str:
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id, n = base, 1
    while Path(winutil.long_path(_log_path(logs_dir, run_id))).exists():
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def _append_line(fh, obj: dict) -> None:
    fh.write(json.dumps(obj, ensure_ascii=True) + "\n")
    fh.flush()
    os.fsync(fh.fileno())


class Journal:
    def __init__(self, path: Path, run_id: str, kind: str, fh, seq: int = 0) -> None:
        self.path = path
        self.run_id = run_id
        self.kind = kind
        self._fh = fh
        self._seq = seq

    @classmethod
    def create(cls, logs_dir: Path, kind: str, meta: dict) -> Journal:
        logs_dir = Path(logs_dir)
        Path(winutil.long_path(logs_dir)).mkdir(parents=True, exist_ok=True)
        while True:
            run_id = new_run_id(logs_dir)
            path = _log_path(logs_dir, run_id)
            try:
                fh = open(winutil.long_path(path), "x", encoding="utf-8", newline="\n")
                break
            except FileExistsError:
                continue
        full_meta = {"config": None, "manifest": None, "to": None, "undoes": None}
        full_meta.update({k: (_s(v) if isinstance(v, Path) else v) for k, v in (meta or {}).items()})
        j = cls(path, run_id, kind, fh)
        _append_line(fh, {"type": "run_start", "run_id": run_id, "kind": kind, "started": _now(),
                          "tool_version": __version__, "meta": full_meta})
        return j

    @classmethod
    def resume(cls, path: Path) -> Journal:
        path = Path(path)
        run = read_run(path)
        if run.finished:
            raise TidyupError(f"Run {run.run_id} already finished; cannot resume it.")
        seq = max((e.get("seq", 0) for e in run.ops), default=0)
        # Drop any truncated final line so appended records stay parseable.
        data = Path(winutil.long_path(path)).read_bytes()
        if data and not data.endswith(b"\n"):
            cut = data.rfind(b"\n") + 1
            with open(winutil.long_path(path), "r+b") as f:
                f.truncate(cut)
        fh = open(winutil.long_path(path), "a", encoding="utf-8", newline="\n")
        return cls(path, run.run_id, run.kind, fh, seq)

    def _write(self, obj: dict) -> None:
        if self._fh is None or self._fh.closed:
            raise TidyupError("The undo log is already closed.")
        _append_line(self._fh, obj)

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def mkdir(self, path: Path) -> None:
        self._write({"type": "mkdir", "seq": self._next(), "path": _s(path), "time": _now()})

    def move(self, *, src: Path, dst: Path, row_id: str | None, action: str, is_dir: bool,
             cross_volume: bool, size: int, sha256: str | None) -> None:
        self._write({"type": "move", "seq": self._next(), "row_id": None if row_id is None else str(row_id),
                     "action": action, "src": _s(src), "dst": _s(dst), "is_dir": bool(is_dir),
                     "cross_volume": bool(cross_volume), "size": size, "sha256": sha256, "time": _now()})

    def trash(self, *, src: Path, row_id: str | None) -> None:
        self._write({"type": "trash", "seq": self._next(), "row_id": None if row_id is None else str(row_id),
                     "src": _s(src), "time": _now()})

    def skip(self, *, src: Path, row_id: str | None, reason: str) -> None:
        self._write({"type": "skip", "row_id": None if row_id is None else str(row_id),
                     "src": _s(src), "reason": reason, "time": _now()})

    def error(self, *, src: Path, row_id: str | None, reason: str) -> None:
        self._write({"type": "error", "row_id": None if row_id is None else str(row_id),
                     "src": _s(src), "reason": reason, "time": _now()})

    def finish(self, counts: dict[str, int]) -> None:
        self._write({"type": "run_end", "finished": _now(), "counts": counts})
        self.close()

    def close(self) -> None:
        if self._fh is not None and not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


@dataclass
class RunLog:
    path: Path
    run_id: str
    kind: str
    header: dict
    entries: list[dict] = field(default_factory=list)
    finished: bool = False
    undone_by: str | None = None

    @property
    def ops(self) -> list[dict]:
        return [e for e in self.entries if e.get("type") in _OP_TYPES]


def read_run(path: Path) -> RunLog:
    path = Path(path)
    try:
        text = Path(winutil.long_path(path)).read_text(encoding="utf-8")
    except OSError as exc:
        raise TidyupError(f"Cannot read undo log {path}: {exc.strerror or exc}") from exc
    header: dict | None = None
    entries: list[dict] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue  # truncated/invalid line (normally only the last)
        if not isinstance(obj, dict):
            continue
        if header is None:
            if obj.get("type") != "run_start":
                raise TidyupError(f"{path} is not a valid undo log (no run_start).")
            header = obj
        else:
            entries.append(obj)
    if header is None:
        raise TidyupError(f"{path} is not a valid undo log (empty).")
    undone_by = None
    for e in entries:
        if e.get("type") == "undone":
            undone_by = e.get("by")
    return RunLog(path=path, run_id=header.get("run_id", path.stem.removeprefix("undo_")),
                  kind=header.get("kind", ""), header=header, entries=entries,
                  finished=any(e.get("type") == "run_end" for e in entries), undone_by=undone_by)


def list_runs(logs_dir: Path) -> list[RunLog]:
    logs_dir = Path(logs_dir)
    if not Path(winutil.long_path(logs_dir)).is_dir():
        return []
    runs = []
    for p in Path(winutil.long_path(logs_dir)).glob("undo_*.jsonl"):
        try:
            runs.append(read_run(p))
        except TidyupError:
            continue
    return sorted(runs, key=lambda r: r.run_id)


def find_run(logs_dir: Path, run_id: str | None) -> RunLog:
    runs = list_runs(logs_dir)
    if run_id is not None:
        for r in runs:
            if r.run_id == run_id:
                return r
        raise TidyupError(f"No run with id {run_id!r} found in {logs_dir}.")
    for r in reversed(runs):
        if r.undone_by is None and r.kind != "undo":
            return r
    raise TidyupError("No runs to undo")


def mark_undone(run: RunLog, by_run_id: str) -> None:
    rec = {"type": "undone", "by": by_run_id, "time": _now()}
    data = Path(winutil.long_path(run.path)).read_bytes()
    with open(winutil.long_path(run.path), "a", encoding="utf-8", newline="\n") as fh:
        if data and not data.endswith(b"\n"):
            fh.write("\n")
        _append_line(fh, rec)
    run.entries.append(rec)
    run.undone_by = by_run_id
