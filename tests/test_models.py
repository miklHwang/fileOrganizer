from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from tidyup import models
from tidyup.errors import ManifestError
from tidyup.models import Action, Confidence, EntryType, FileRecord, ManifestRow


def test_type_group():
    assert models.type_group(".JPG") == "Images"
    assert models.type_group(".pdf") == "PDFs"
    assert models.type_group(".msi") == "Installers"
    assert models.type_group(".7z") == "Archives"
    assert models.type_group("") == "Other"
    assert models.type_group(".xyz") == "Other"


def test_dt_helpers():
    d = datetime(2024, 7, 14, 9, 5, 3)
    assert models.format_dt(d) == "2024-07-14 09:05:03"
    assert models.format_dt(None) == ""
    assert models.parse_dt("2024-07-14 09:05:03") == d
    assert models.parse_dt("2024-07-14T09:05:03") == d
    assert models.parse_dt("2024-07-14 09:05") == datetime(2024, 7, 14, 9, 5)
    assert models.parse_dt("") is None


def test_inventory_round_trip(tmp_path):
    d = datetime(2024, 1, 2, 3, 4, 5)
    recs = [
        FileRecord(Path("/x/Doc, \"quoted\" é.pdf"), EntryType.FILE, "documents", 10, ".pdf", d, d, False,
                   sha256="ab" * 32, image_width=1920, image_height=1080, camera_make="Canon",
                   camera_model="R5", exif_datetime=d, phash="0123456789abcdef", error="locked: x"),
        FileRecord(Path("/x/proj"), EntryType.ATOMIC_FOLDER, "desktop", 99, "", d, d, True,
                   atomic_root=Path("/x/proj"), file_count=7),
        FileRecord(Path("/x/日本語.txt"), EntryType.FILE, "downloads", 0, ".txt", d, d, True),
    ]
    p = tmp_path / "sub" / "inv.csv"
    assert models.write_inventory(p, recs) == 3
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8-sig").splitlines()[0] == ",".join(models.INVENTORY_COLUMNS)
    assert models.read_inventory(p) == recs


def _rows():
    return [
        ManifestRow(1, Action.MOVE, Path("/a/b, c.txt"), Path("/d/b, c.txt"), "r", Confidence.HIGH, "n1; \"q\"", True),
        ManifestRow(2, Action.SKIP, Path("/a/é.txt"), None, "skip", Confidence.LOW, "", False),
        ManifestRow(3, Action.REVIEW_DELETE, Path("/a/old.exe"), None, "old", Confidence.MEDIUM, "x", False),
    ]


def test_manifest_round_trip(tmp_path):
    p = tmp_path / "m.csv"
    assert models.write_manifest(p, _rows()) == 3
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")
    text = p.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "id,action,source,destination,rule,confidence,notes,approved"
    assert ",Y" in text and ",N" in text
    assert models.read_manifest(p) == _rows()


def test_long_path_prefix_stripped(tmp_path):
    p = tmp_path / "m.csv"
    models.write_manifest(p, [ManifestRow(1, Action.MOVE, Path("\\\\?\\C:\\a\\b.txt"), None, "r",
                                          Confidence.HIGH, "", True)])
    assert "\\\\?\\" not in p.read_text(encoding="utf-8-sig")


def test_read_manifest_excel_variants(tmp_path):
    p = tmp_path / "m.csv"
    content = ("ID;Action;SOURCE;Destination;Rule;Confidence;Notes;Approved\r\n"
               "1;move;/a/é.txt;/b/é.txt;r;HIGH;;yes\r\n"
               "\r\n"
               ";;;;;;;\r\n"
               "2;rename;/a/2.txt;/a/3.txt;r;medium;n;x\r\n"
               "3;skip;/a/3.txt;;r;low;;1\r\n"
               "4;skip;/a/4.txt;;r;low;;N\r\n")
    p.write_bytes(content.encode("cp1252"))
    rows = models.read_manifest(p)
    assert [r.id for r in rows] == [1, 2, 3, 4]
    assert [r.approved for r in rows] == [True, True, True, False]
    assert rows[0].source == Path("/a/é.txt")
    assert rows[0].confidence is Confidence.HIGH
    assert rows[2].destination is None


def test_read_manifest_reordered_tab_delimited(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("approved\tid\tsource\tdestination\trule\tconfidence\tnotes\taction\n"
                 "Y\t5\t/a\t/b\tr\thigh\tz\tMove\n", encoding="utf-8-sig")
    (r,) = models.read_manifest(p)
    assert (r.id, r.action, r.approved, r.notes) == (5, Action.MOVE, True, "z")


def test_invalid_action_names_row(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("id,action,source,destination,rule,confidence,notes,approved\n"
                 "1,move,/a,/b,r,high,,Y\n2,explode,/a,/b,r,high,,Y\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="Row 3"):
        models.read_manifest(p)


def test_invalid_confidence_and_missing_column(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("id,action,source,destination,rule,confidence,notes,approved\n1,move,/a,/b,r,huge,,Y\n",
                 encoding="utf-8")
    with pytest.raises(ManifestError, match="Row 2"):
        models.read_manifest(p)
    p.write_text("id,action,source\n1,move,/a\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="missing"):
        models.read_manifest(p)


def test_manifest_formula_injection_neutralised(tmp_path):
    p = tmp_path / "m.csv"
    row = ManifestRow(id=1, action=Action.MOVE, source=Path("C:/a/b.txt"), destination=Path("C:/x/b.txt"),
                      rule="+cmd", confidence=Confidence.HIGH, notes='=HYPERLINK("x")', approved=True)
    plain = ManifestRow(id=2, action=Action.MOVE, source=Path("C:/a/c.txt"), destination=Path("C:/x/c.txt"),
                        rule="ext", confidence=Confidence.HIGH, notes="normal note", approved=False)
    models.write_manifest(p, [row, plain])
    raw = p.read_text(encoding="utf-8-sig")
    assert "'+cmd" in raw and "'=HYPERLINK" in raw
    assert ",ext,high,normal note,N" in raw.lower() or "normal note" in raw
    back = models.read_manifest(p)
    assert back[0].rule == "+cmd" and back[0].notes == '=HYPERLINK("x")'
    assert back[0].approved is True and back[1].approved is False
    assert back[1].notes == "normal note" and back[1].rule == "ext"
    assert "'" not in raw.split("\n", 2)[2]
