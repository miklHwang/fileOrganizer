from __future__ import annotations

import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tidyup import dupes
from tidyup.models import EntryType, FileRecord

T0 = datetime(2024, 1, 1, 12, 0, 0)


def rec(path, *, size=10, sha="a" * 64, created=T0, modified=T0, cloud=False, phash=None):
    p = Path(path)
    return FileRecord(path=p, entry_type=EntryType.FILE, source_root="documents", size=size,
                      extension=p.suffix.lower(), created=created, modified=modified,
                      is_cloud_only=cloud, sha256=sha, phash=phash)


@pytest.fixture
def base(cfg):
    return cfg.folders


def test_exact_groups_ignore_ineligible(cfg, base):
    d = base["documents"]
    recs = [
        rec(d / "a.txt"), rec(d / "b.txt"),
        rec(d / "c.txt", sha="c" * 64, cloud=True), rec(d / "d.txt", sha="c" * 64, cloud=True),
        rec(d / "e.txt", sha=None), rec(d / "f.txt", sha=None),
        rec(d / "g.txt", sha="e" * 64, size=0), rec(d / "h.txt", sha="e" * 64, size=0),
    ]
    groups = dupes.exact_duplicate_groups(recs, cfg)
    assert len(groups) == 1
    assert groups[0].sha256 == "a" * 64
    assert len(groups[0].duplicates) == 1


def test_keeper_prefers_documents_over_downloads(cfg, base):
    a = rec(base["downloads"] / "x.pdf")
    b = rec(base["documents"] / "zzz" / "longer_name.pdf")
    assert dupes.choose_keeper([a, b], cfg) is b
    g = dupes.exact_duplicate_groups([a, b], cfg)[0]
    assert g.keeper is b and g.duplicates == [a]


def test_keeper_prefers_clean_name_then_older(cfg, base):
    d = base["documents"]
    clean = rec(d / "report - Copy.pdf", created=T0)
    copy1 = rec(d / "report (1).pdf", created=T0 - timedelta(days=5))
    plain = rec(d / "report.pdf", created=T0)
    assert dupes.choose_keeper([copy1, plain], cfg) is plain
    assert dupes.choose_keeper([clean, plain], cfg) is plain
    old = rec(d / "aaa.pdf", created=T0 - timedelta(days=1))
    new = rec(d / "bbb.pdf", created=T0)
    assert dupes.choose_keeper([new, old], cfg) is old


def test_duplicates_sorted_and_deterministic(cfg, base):
    d = base["documents"]
    recs = [rec(d / n) for n in ("k.txt", "z.txt", "m.txt", "b.txt")]
    g1 = dupes.exact_duplicate_groups(recs, cfg)[0]
    g2 = dupes.exact_duplicate_groups(list(reversed(recs)), cfg)[0]
    assert g1.keeper.path == g2.keeper.path
    assert [r.path for r in g1.duplicates] == sorted(r.path for r in g1.duplicates)
    assert [r.path for r in g1.duplicates] == [r.path for r in g2.duplicates]


@pytest.mark.parametrize("stem,expected", [
    ("report v3", "report"),
    ("report_final_FINAL", "report"),
    ("final_FINAL", "final final"),
    ("Copy of x", "x"),
    ("x - Copy (2)", "x"),
    ("x (1)", "x"),
    ("x_v2", "x"),
    ("draft", "draft"),
    ("plain", "plain"),
])
def test_normalize_version_stem(cfg, stem, expected):
    pats = cfg.rules["version_chains"]["patterns"]
    assert dupes.normalize_version_stem(stem, pats) == expected


def test_version_chains(cfg, base):
    d = base["documents"]
    a = rec(d / "report.docx", modified=T0)
    b = rec(d / "report v2.docx", modified=T0 + timedelta(days=2))
    c = rec(d / "report v3.docx", modified=T0 + timedelta(days=1))
    other_ext = rec(d / "report v4.pdf")
    other_dir = rec(d / "sub" / "report v5.docx")
    lone = rec(d / "alone.docx")
    same = [rec(d / "same.docx"), rec(d / "SAME.docx")]  # no marker -> not a chain
    chains = dupes.version_chains([a, b, c, other_ext, other_dir, lone, *same], cfg)
    assert len(chains) == 1
    ch = chains[0]
    assert ch.current is b
    assert {r.path for r in ch.older} == {a.path, c.path}


def test_version_chains_exclude(cfg, base):
    d = base["documents"]
    a, b = rec(d / "x.txt"), rec(d / "x (1).txt")
    assert len(dupes.version_chains([a, b], cfg)) == 1
    assert dupes.version_chains([a, b], cfg, exclude={b.path}) == []


def ph(n: int) -> str:
    return f"{n:016x}"


def test_near_duplicates_threshold_and_exact_exclusion(cfg, base):
    p = base["pictures"]
    h = 0x0123456789ABCDEF
    a = rec(p / "a.jpg", sha="1" * 64, phash=ph(h))
    b = rec(p / "b.jpg", sha="2" * 64, phash=ph(h ^ 0b111111))      # distance 6
    c = rec(p / "c.jpg", sha="3" * 64, phash=ph(h ^ (0x7F << 8)))   # 7 from a
    far = rec(p / "far.jpg", sha="4" * 64, phash=ph(~h & (2**64 - 1)))
    ex1 = rec(p / "e1.jpg", sha="9" * 64, phash=ph(0xFFFF0000FFFF0000))
    ex2 = rec(p / "e2.jpg", sha="9" * 64, phash=ph(0xFFFF0000FFFF0000))
    groups = dupes.near_duplicate_groups([far, c, b, a, ex1, ex2], cfg)
    assert len(groups) == 1
    g = groups[0]
    assert [m.path.name for m in g.members] == ["a.jpg", "b.jpg"]
    assert g.max_distance == 6


def test_near_duplicates_disabled(cfg, base):
    import dataclasses
    rules = {**cfg.rules, "near_duplicates": {"enabled": False, "max_distance": 6}}
    c2 = dataclasses.replace(cfg, rules=rules)
    p = base["pictures"]
    recs = [rec(p / "a.jpg", sha="1" * 64, phash=ph(1)), rec(p / "b.jpg", sha="2" * 64, phash=ph(1))]
    assert dupes.near_duplicate_groups(recs, c2) == []


def test_near_duplicates_performance(cfg, base):
    rnd = random.Random(7)
    p = base["pictures"]
    recs = []
    for i in range(20000):
        if i % 4 == 1:
            h = int(recs[-1].phash, 16) ^ (1 << rnd.randrange(64))
        else:
            h = rnd.getrandbits(64)
        recs.append(rec(p / f"img{i:05d}.jpg", sha=f"{i:064x}", phash=ph(h)))
    t = time.perf_counter()
    groups = dupes.near_duplicate_groups(recs, cfg)
    assert time.perf_counter() - t < 30
    assert len(groups) >= 5000
