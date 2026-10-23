from __future__ import annotations

import json
from pathlib import Path

import pytest

from tidyup import journal
from tidyup.errors import TidyupError
from tidyup.journal import Journal


def _mk(tmp_path, kind="apply"):
    return Journal.create(tmp_path / "logs", kind, {"config": "c.yaml", "manifest": "m.csv"})


def _move(j, n=1):
    j.move(src=Path(f"/a/{n}"), dst=Path(f"/b/{n}"), row_id=str(n), action="move", is_dir=False,
           cross_volume=False, size=5, sha256=None)


def test_create_write_finish_read(tmp_path):
    j = _mk(tmp_path)
    j.mkdir(Path("/b"))
    _move(j)
    j.trash(src=Path("/a/t"), row_id="9")
    j.skip(src=Path("/a/s"), row_id="3", reason="locked: in use")
    j.error(src=Path("/a/e"), row_id=None, reason="boom")
    j.finish({"moved": 1, "trashed": 1, "skipped": 1, "errors": 1})
    assert j.path.name == f"undo_{j.run_id}.jsonl"
    lines = j.path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    assert first["type"] == "run_start" and first["kind"] == "apply" and first["tool_version"] == "0.1.0"
    assert set(first["meta"]) >= {"config", "manifest", "to", "undoes"}
    run = journal.read_run(j.path)
    assert run.finished and run.kind == "apply" and run.run_id == j.run_id and run.undone_by is None
    assert [e["type"] for e in run.entries] == ["mkdir", "move", "trash", "skip", "error", "run_end"]
    assert [o["seq"] for o in run.ops] == [1, 2, 3]
    assert run.entries[1]["dst"] == "/b/1"


def test_fsync_called_per_line(tmp_path, monkeypatch):
    calls = []
    real = journal.os.fsync
    monkeypatch.setattr(journal.os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    j = _mk(tmp_path)
    _move(j)
    j.close()
    assert len(calls) == 2


def test_context_manager_does_not_write_run_end(tmp_path):
    with _mk(tmp_path) as j:
        _move(j)
    run = journal.read_run(j.path)
    assert not run.finished
    with pytest.raises(TidyupError):
        _move(j, 2)


def test_truncated_last_line_tolerated(tmp_path):
    j = _mk(tmp_path)
    _move(j)
    j.close()
    with open(j.path, "a", encoding="utf-8") as f:
        f.write('{"type":"move","seq":2,"src":"/a')
    run = journal.read_run(j.path)
    assert len(run.ops) == 1


def test_resume_continues_seq(tmp_path):
    j = _mk(tmp_path)
    _move(j, 1)
    _move(j, 2)
    j.close()
    with open(j.path, "a", encoding="utf-8") as f:
        f.write('{"type":"mo')
    j2 = Journal.resume(j.path)
    _move(j2, 3)
    j2.finish({"moved": 3})
    run = journal.read_run(j.path)
    assert [o["seq"] for o in run.ops] == [1, 2, 3]
    assert run.finished
    with pytest.raises(TidyupError):
        Journal.resume(j.path)


def test_new_run_id_unique(tmp_path):
    logs = tmp_path / "logs"
    ids = set()
    for _ in range(3):
        j = Journal.create(logs, "apply", {})
        j.close()
        ids.add(j.run_id)
    assert len(ids) == 3
    assert any(i.endswith("-2") for i in ids) or len(ids) == 3
    assert journal.new_run_id(logs) not in ids


def test_list_find_mark_undone(tmp_path):
    logs = tmp_path / "logs"
    assert journal.list_runs(logs) == []
    a = Journal.create(logs, "apply", {}); a.finish({})
    b = Journal.create(logs, "upkeep", {}); b.finish({})
    c = Journal.create(logs, "undo", {"undoes": b.run_id}); c.finish({})
    runs = journal.list_runs(logs)
    assert [r.run_id for r in runs] == sorted([a.run_id, b.run_id, c.run_id])
    assert journal.find_run(logs, None).run_id == b.run_id  # latest, not the undo log
    run_b = journal.find_run(logs, b.run_id)
    journal.mark_undone(run_b, c.run_id)
    assert run_b.undone_by == c.run_id
    assert journal.read_run(b.path).undone_by == c.run_id
    assert journal.find_run(logs, None).run_id == a.run_id
    journal.mark_undone(journal.find_run(logs, a.run_id), "x")
    with pytest.raises(TidyupError, match="No runs to undo"):
        journal.find_run(logs, None)
    with pytest.raises(TidyupError):
        journal.find_run(logs, "nope")


def test_unicode_and_prefix_stripped(tmp_path):
    j = _mk(tmp_path)
    j.trash(src=Path("\\\\?\\C:\\日本\\é.txt"), row_id=None)
    j.close()
    run = journal.read_run(j.path)
    assert run.ops[0]["src"] == "C:\\日本\\é.txt"
    assert run.ops[0]["row_id"] is None
