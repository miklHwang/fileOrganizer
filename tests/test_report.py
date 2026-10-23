from __future__ import annotations

import argparse
import dataclasses
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

from tidyup import config, models, report
from tidyup.models import Action, Confidence, EntryType, FileRecord, ManifestRow

SECTIONS = ["summary", "tree", "screenshots", "duplicates", "near-duplicates", "versions", "onedrive"]
NOW = datetime(2024, 1, 1)


def _rec(path: Path, *, cloud=False, sha=None, size=100, phash=None) -> FileRecord:
    return FileRecord(path=path, entry_type=EntryType.FILE, source_root="downloads", size=size,
                      extension=path.suffix.lower(), created=NOW, modified=NOW, is_cloud_only=cloud,
                      sha256=sha, phash=phash)


def _png(p: Path, color=(200, 10, 10)) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (64, 48), color) as im:
        im.save(p)
    return p


def _row(i, src, dst, rule="screenshot", notes="", action=Action.MOVE, conf=Confidence.HIGH, ok=True):
    return ManifestRow(i, action, src, dst, rule, conf, notes, ok)


def _cfg(tmp_path, **report_opts):
    c = config.load_config(None, overrides={"folders": {"home": str(tmp_path / "home")}})
    if report_opts:
        c = dataclasses.replace(c, report={**c.report, **report_opts})
    return c


class _P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.urls = [], []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k == "id":
                self.ids.append(v)
            if k in ("src", "href"):
                self.urls.append(v)


def _parse(text):
    p = _P()
    p.feed(text)
    p.close()
    return p


def _scenario(tmp_path):
    d = tmp_path / "dl"
    shot = _rec(_png(d / "Screenshot 1.png"))
    cloud = _rec(d / "Screenshot cloud.png", cloud=True)
    a = _rec(_png(d / "a.png"), sha="a" * 64)
    b = _rec(_png(d / "a (1).png"), sha="a" * 64)
    v1, v2 = _rec(d / "report v1.docx"), _rec(d / "report v2.docx")
    v2.modified = datetime(2024, 2, 1)
    recs = [shot, cloud, a, b, v1, v2]
    rows = [
        _row(1, shot.path, tmp_path / "Pics" / "Screenshots_Review" / "Screenshot 1.png"),
        _row(2, cloud.path, tmp_path / "Pics" / "Screenshots_Review" / "Screenshot cloud.png", notes="cloud-only"),
        _row(3, a.path, tmp_path / "Docs" / "x.png", rule="photo"),
        _row(4, v1.path, tmp_path / "Docs" / "Work" / "r.docx", rule="doc",
             notes="WARNING: moves this file OUT of OneDrive (it will no longer be in the cloud)", ok=False),
        _row(5, v2.path, None, action=Action.SKIP, rule="x"),
    ]
    return recs, rows


def test_sections_and_content(tmp_path):
    recs, rows = _scenario(tmp_path)
    text = report.build_report(_cfg(tmp_path), recs, rows)
    p = _parse(text)
    for s in SECTIONS:
        assert s in p.ids
    assert '<img src="data:image/jpeg;base64,' in text
    assert "KEEP" in text and "Likely old versions" in text
    assert "cloud-only — not downloaded" in text
    assert "OUT of OneDrive" in text
    assert all(not u.startswith(("http://", "https://")) for u in p.urls)
    assert "<script" not in text


def test_cloud_only_not_opened(tmp_path, open_spy):
    recs, rows = _scenario(tmp_path)
    report.build_report(_cfg(tmp_path), recs, rows)
    # open_spy records norm_key() paths, which are lower-cased on Windows
    opened = [o.lower() for o in open_spy]
    assert not any("screenshot cloud" in o for o in opened)
    assert any("screenshot 1.png" in o for o in opened)


def test_escaping(tmp_path):
    bad = _rec(tmp_path / "<script>alert(1)</script>.png", cloud=True)
    rows = [_row(1, bad.path, tmp_path / "x" / "<script>.png", notes="<script>x</script>")]
    text = report.build_report(_cfg(tmp_path), [bad], rows)
    assert "<script" not in text
    assert "&lt;script&gt;" in text


def test_thumbnail_cap(tmp_path):
    recs, rows = [], []
    for i in range(5):
        r = _rec(_png(tmp_path / "dl" / f"Screenshot {i}.png", (i * 40, 0, 0)))
        recs.append(r)
        rows.append(_row(i + 1, r.path, tmp_path / "o" / r.path.name))
    text = report.build_report(_cfg(tmp_path, max_thumbnails=2), recs, rows)
    assert text.count("data:image/jpeg;base64,") == 2
    assert text.count("Screenshot 4.png") >= 1


def test_no_manifest(tmp_path):
    recs, _ = _scenario(tmp_path)
    text = report.build_report(_cfg(tmp_path), recs, None)
    assert "Run tidyup plan to see proposed changes." in text
    _parse(text)
    for s in SECTIONS:
        assert f'id="{s}"' in text


def test_command_writes_and_dry_run(tmp_path):
    recs, rows = _scenario(tmp_path)
    cfg = dataclasses.replace(_cfg(tmp_path), work_dir=tmp_path / "work")
    models.write_inventory(cfg.inventory_path, recs)
    models.write_manifest(cfg.manifest_path, rows)
    ns = argparse.Namespace(dry_run=True, open=False)
    assert report.command(cfg, ns) == 0
    assert not cfg.report_path.exists()
    ns.dry_run = False
    assert report.command(cfg, ns) == 0
    assert 'id="summary"' in cfg.report_path.read_text(encoding="utf-8")
