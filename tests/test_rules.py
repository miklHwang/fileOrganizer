from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tidyup import extract, rules, winutil
from tidyup.models import EntryType, FileRecord, Confidence

NOW = datetime(2026, 10, 23, 12, 0, 0)


def make_record(path: Path, cfg, *, cloud: bool = False, image: bool = True) -> FileRecord:
    st = os.stat(winutil.long_path(path))
    mod = datetime.fromtimestamp(st.st_mtime).replace(microsecond=0)
    rec = FileRecord(path=path, entry_type=EntryType.FILE, source_root="x", size=st.st_size,
                     extension=path.suffix.lower(), created=mod, modified=mod, is_cloud_only=cloud)
    if image and path.suffix.lower() in extract.IMAGE_EXTS and not cloud:
        info = extract.read_image_info(path, is_cloud_only=False, want_phash=False)
        if info:
            rec.image_width, rec.image_height = info.width, info.height
            rec.camera_make, rec.camera_model = info.camera_make, info.camera_model
            rec.exif_datetime = info.exif_datetime
    return rec


@pytest.fixture
def ctx(cfg):
    return rules.RuleContext(cfg, datetime.now() + timedelta(seconds=5))


def cls(fake_tree, cfg, ctx, handle, **kw):
    return rules.classify(make_record(fake_tree.files[handle], cfg, **kw), ctx)


@pytest.mark.parametrize("handle", ["screenshot_desktop", "screenshot_capture", "screenshot_phone"])
def test_screenshots_high(fake_tree, cfg, ctx, handle):
    p = cls(fake_tree, cfg, ctx, handle)
    assert p.rule == "screenshot" and p.confidence == Confidence.HIGH
    assert p.logical_dir == "Pictures/Screenshots_Review"
    assert p.notes[0].startswith("screenshot score ")
    score, _ = rules.screenshot_score(make_record(fake_tree.files[handle], cfg), ctx)
    assert score >= 5


def test_drawing_not_screenshot(fake_tree, cfg, ctx):
    rec = make_record(fake_tree.files["drawing_png"], cfg)
    assert rules.screenshot_score(rec, ctx)[0] < cfg.rules["screenshot"]["min_score"]
    assert rules.classify(rec, ctx).rule == "unsorted"


def test_photo_with_camera_not_screenshot(fake_tree, cfg, ctx):
    p = cls(fake_tree, cfg, ctx, "photo_exif")
    assert p.rule == "photo" and p.confidence == Confidence.HIGH
    assert p.logical_dir == "Pictures/Photos/2024/2024-07"
    assert p.notes == ["date from exif"]


def test_photo_dates(fake_tree, cfg):
    r = make_record(fake_tree.files["photo_exif2"], cfg)
    assert rules.photo_date(r) == (datetime(2023, 1, 2, 9), "exif")
    r.exif_datetime = None
    r.path = Path("PXL_20240714_101010.jpg")
    assert rules.photo_date(r) == (datetime(2024, 7, 14), "filename")
    r.path = Path("holiday.jpg")
    assert rules.photo_date(r) == (r.modified, "modified")


def test_photo_modified_medium(fake_tree, cfg, ctx):
    rec = make_record(fake_tree.files["photo_exif2"], cfg)
    rec.exif_datetime = None
    p = rules.classify(rec, ctx)
    assert p.confidence == Confidence.MEDIUM and p.notes == ["date from modified"]


def test_video(fake_tree, cfg, ctx):
    p = cls(fake_tree, cfg, ctx, "video")
    assert (p.rule, p.logical_dir, p.confidence) == ("video", "Videos/2023", Confidence.HIGH)
    p = cls(fake_tree, cfg, ctx, "video_nodate")
    assert p.confidence == Confidence.MEDIUM and p.logical_dir.startswith("Videos/")


def test_installers_and_archives(fake_tree, cfg, ctx):
    old = cls(fake_tree, cfg, ctx, "installer_old")
    assert (old.kind, old.rule, old.confidence, old.logical_dir) == (
        "review_delete", "installer_old", Confidence.LOW, "_Review_Delete")
    new = cls(fake_tree, cfg, ctx, "installer_new")
    assert (new.kind, new.rule, new.confidence, new.logical_dir) == (
        "file", "installer", Confidence.HIGH, "Installers")
    assert cls(fake_tree, cfg, ctx, "archive_zip").logical_dir == "Archives"
    assert cls(fake_tree, cfg, ctx, "archive_7z").rule == "archive"


def test_installer_archive_only_in_downloads(fake_tree, cfg, ctx, tmp_path):
    for name, group in (("x.exe", "Installers"), ("x.zip", "Archives")):
        f = fake_tree.folders["documents"] / name
        f.write_bytes(b"abc")
        p = rules.classify(make_record(f, cfg), ctx)
        assert p.rule == "unsorted" and p.logical_dir == f"_Unsorted/{group}"


@pytest.mark.parametrize("handle,cat", [("invoice_pdf", "Receipts"), ("statement_pdf", "Finance"),
                                         ("syllabus_pdf", "School")])
def test_pdf_high(fake_tree, cfg, ctx, handle, cat):
    p = cls(fake_tree, cfg, ctx, handle)
    assert p.rule == "pdf" and p.confidence == Confidence.HIGH
    assert p.logical_dir == f"Documents/{cat}"
    assert re.match(r"^\d{4}-\d{2}-\d{2}_(Receipts|Finance|School)_[A-Za-z0-9-]+\.pdf$", p.new_name)
    assert p.new_name.split("_")[1] == cat


def test_invoice_date_from_text(fake_tree, cfg, ctx):
    assert cls(fake_tree, cfg, ctx, "invoice_pdf").new_name.startswith("2024-03-15_Receipts_")
    assert cls(fake_tree, cfg, ctx, "statement_pdf").new_name.startswith("2024-01-01_Finance_")


def test_scanned_pdf(fake_tree, cfg, ctx):
    p = cls(fake_tree, cfg, ctx, "scanned_pdf")
    assert (p.logical_dir, p.confidence, p.new_name) == ("Documents/Housing", Confidence.MEDIUM, None)
    assert "no text layer (scanned?)" in p.notes


def test_plain_pdf(fake_tree, cfg, ctx):
    p = cls(fake_tree, cfg, ctx, "plain_pdf")
    assert (p.logical_dir, p.confidence, p.new_name) == ("_Unsorted/PDFs", Confidence.LOW, None)


def test_docx(fake_tree, cfg, ctx):
    p = cls(fake_tree, cfg, ctx, "agenda_docx")
    assert (p.rule, p.logical_dir, p.confidence, p.new_name) == ("document", "Documents/Work", Confidence.HIGH, None)
    p = cls(fake_tree, cfg, ctx, "version_1")
    assert p.logical_dir == "_Unsorted/Documents" and p.confidence == Confidence.LOW


def test_txt_and_xlsx(fake_tree, cfg, ctx):
    p = cls(fake_tree, cfg, ctx, "notes_txt_desktop")
    assert (p.logical_dir, p.confidence) == ("_Unsorted/Documents", Confidence.LOW)
    p = cls(fake_tree, cfg, ctx, "budget_xlsx")
    assert p.rule == "document"


def test_fallback(fake_tree, cfg, ctx):
    p = cls(fake_tree, cfg, ctx, "misc")
    assert (p.rule, p.logical_dir, p.confidence) == ("unsorted", "_Unsorted/Other", Confidence.LOW)


def test_cloud_only_never_opened(fake_tree, cfg, ctx, open_spy, mock_attrs):
    out = {}
    for h in ("cloud_pdf", "cloud_photo", "cloud_screenshot"):
        out[h] = cls(fake_tree, cfg, ctx, h, cloud=True)
    out["od_docx_cloud"] = rules.classify(
        make_record(fake_tree.files["onedrive_letter"], cfg, cloud=True), ctx)
    cloud_keys = {winutil.norm_key(p) for p in fake_tree.cloud_only}
    cloud_keys.add(winutil.norm_key(fake_tree.files["onedrive_letter"]))
    assert not cloud_keys & set(open_spy)
    pdf = out["cloud_pdf"]
    assert pdf.logical_dir == "Documents/Taxes" and pdf.confidence == Confidence.MEDIUM
    assert "cloud-only: classified by name only" in pdf.notes and "cloud-only" in pdf.notes
    assert out["cloud_photo"].rule == "photo" and out["cloud_photo"].logical_dir == "Pictures/Photos/2022/2022-01"
    assert out["cloud_screenshot"].rule == "screenshot" and out["cloud_screenshot"].confidence == Confidence.MEDIUM
    assert out["od_docx_cloud"].logical_dir == "Documents/Personal"
    assert all("cloud-only" in p.notes for p in out.values())


def test_non_file_rejected(fake_tree, cfg, ctx):
    rec = make_record(fake_tree.files["misc"], cfg)
    rec.entry_type = EntryType.ATOMIC_FOLDER
    with pytest.raises(ValueError):
        rules.classify(rec, ctx)


@pytest.mark.parametrize("name,expected", [
    ("IMG_20240714_101010.jpg", datetime(2024, 7, 14)),
    ("PXL_20240714_101010.jpg", datetime(2024, 7, 14)),
    ("VID_20230505_101010.mp4", datetime(2023, 5, 5)),
    ("Screenshot_20240601-120000.png", datetime(2024, 6, 1)),
    ("WhatsApp Image 2024-07-14 at 10.jpg", datetime(2024, 7, 14)),
    ("IMG_20241345.jpg", None),
    ("photo.jpg", None),
    ("x1234567890123.jpg", None),
])
def test_date_from_filename(name, expected):
    assert rules.date_from_filename(name) == expected


@pytest.mark.parametrize("text,order,expected", [
    ("Date: 2024-03-15", "MDY", datetime(2024, 3, 15)),
    ("Period 01/02/2024", "MDY", datetime(2024, 1, 2)),
    ("Period 01/02/2024", "DMY", datetime(2024, 2, 1)),
    ("on 15 March 2024.", "MDY", datetime(2024, 3, 15)),
    ("March 15, 2024", "MDY", datetime(2024, 3, 15)),
    ("Mar 15 2024", "MDY", datetime(2024, 3, 15)),
    ("1850-01-01 then 2024-05-06", "MDY", datetime(2024, 5, 6)),
    ("13/45/2024", "MDY", None),
    ("2099-01-01", "MDY", None),
    ("no date here", "MDY", None),
])
def test_date_from_text(text, order, expected):
    assert rules.date_from_text(text, order, NOW) == expected


def test_slugify():
    assert rules.slugify("Hello, World!") == "Hello-World"
    assert rules.slugify("Café Menü 2024") == "Cafe-Menu-2024"
    assert rules.slugify("") == ""
    assert rules.slugify("one two three four", 10) == "one-two"
    assert rules.slugify("abcdefghijkl", 5) == "abcde"


def test_match_categories(cfg):
    m = rules.match_categories("Invoice number 5. Amount paid. Subtotal", "scan_lease_2022.pdf", cfg)
    assert m.best == "Receipts"
    assert m.filename_hits["Housing"] == ["lease"]
    assert rules.match_categories("nothing", "x.pdf", cfg).best is None
    assert rules.match_categories("rental", "x.pdf", cfg).best is None  # word boundaries


def test_pdf_filename(cfg):
    assert rules.pdf_filename(datetime(2024, 3, 5), "Finance", "My Bank", cfg) == "2024-03-05_Finance_My-Bank.pdf"
    assert rules.pdf_filename(datetime(2024, 3, 5), "Finance", "", cfg).endswith("_Document.pdf")


def test_generic_stem_uses_text(cfg, tmp_path):
    assert rules._pdf_description("scan_0012", "\n12\nFirst National Bank statement of things extra words") \
        == "First-National-Bank-statement-of-things"
    assert rules._pdf_description("scan_0012", "") == "Document"
    assert rules._pdf_description("my report", "x") == "my-report"


def test_is_in_target_structure(fake_tree, cfg):
    f = fake_tree.folders
    assert rules.is_in_target_structure(f["documents"] / "Finance" / "a.pdf", cfg)
    assert rules.is_in_target_structure(f["pictures"] / "Photos" / "2024" / "2024-07" / "a.jpg", cfg)
    assert rules.is_in_target_structure(f["pictures"] / "Screenshots_Review" / "a.png", cfg)
    assert rules.is_in_target_structure(f["videos"] / "2023" / "a.mp4", cfg)
    assert not rules.is_in_target_structure(f["videos"] / "a.mp4", cfg)
    assert not rules.is_in_target_structure(f["videos"] / "clips" / "a.mp4", cfg)
    assert rules.is_in_target_structure(f["downloads"] / "Installers" / "a.exe", cfg)
    assert rules.is_in_target_structure(f["downloads"] / "Archives" / "a.zip", cfg)
    assert rules.is_in_target_structure(f["home"] / "_Unsorted" / "PDFs" / "a.pdf", cfg)
    assert rules.is_in_target_structure(f["home"] / "_Duplicates" / "a", cfg)
    assert rules.is_in_target_structure(f["home"] / "_Review_Delete" / "a", cfg)
    assert not rules.is_in_target_structure(f["documents"] / "random" / "a.pdf", cfg)
    assert not rules.is_in_target_structure(f["downloads"] / "a.zip", cfg)
