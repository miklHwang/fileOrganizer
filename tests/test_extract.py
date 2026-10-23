from __future__ import annotations

from datetime import datetime

import pytest

from tidyup import extract, winutil


def _hd(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def test_photo_info(fake_tree):
    info = extract.read_image_info(fake_tree.files["photo_exif"], is_cloud_only=False)
    assert info.camera_make == "Canon" and info.camera_model == "EOS 80D"
    assert info.exif_datetime == datetime(2024, 7, 14, 10, 10, 10)
    assert info.width > 0 and info.height > 0
    assert len(info.phash) == 16


def test_screenshot_no_camera(fake_tree):
    info = extract.read_image_info(fake_tree.files["screenshot_desktop"], is_cloud_only=False, want_phash=False)
    assert (info.width, info.height) == (1920, 1080)
    assert info.camera_make is None and info.camera_model is None
    assert info.exif_datetime is None and info.phash is None


def test_near_duplicate_phash(fake_tree):
    a = extract.read_image_info(fake_tree.files["photo_exif2"], is_cloud_only=False)
    b = extract.read_image_info(fake_tree.files["photo_near"], is_cloud_only=False)
    assert _hd(a.phash, b.phash) <= 6


def test_pdf_text(fake_tree):
    assert "Invoice number" in extract.pdf_text(fake_tree.files["invoice_pdf"], is_cloud_only=False)
    assert extract.pdf_text(fake_tree.files["scanned_pdf"], is_cloud_only=False) == ""


def test_docx_text(fake_tree):
    assert "Project kickoff" in extract.docx_text(fake_tree.files["agenda_docx"], is_cloud_only=False)


def test_thumbnail(fake_tree):
    uri = extract.thumbnail_data_uri(fake_tree.files["photo_exif"], is_cloud_only=False)
    assert uri.startswith("data:image/jpeg;base64,")


def test_corrupt_files(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not a jpeg")
    badpdf = tmp_path / "bad.pdf"
    badpdf.write_bytes(b"%PDF-1.4 garbage")
    baddocx = tmp_path / "bad.docx"
    baddocx.write_bytes(b"nope")
    assert extract.read_image_info(bad, is_cloud_only=False) is None
    assert extract.thumbnail_data_uri(bad, is_cloud_only=False) is None
    assert extract.pdf_text(badpdf, is_cloud_only=False) == ""
    assert extract.docx_text(baddocx, is_cloud_only=False) == ""
    assert extract.pdf_text(tmp_path / "missing.pdf", is_cloud_only=False) == ""


def _call_all(p, flag):
    assert extract.read_image_info(p, is_cloud_only=flag) is None
    assert extract.thumbnail_data_uri(p, is_cloud_only=flag) is None
    assert extract.pdf_text(p, is_cloud_only=flag) == ""
    assert extract.docx_text(p, is_cloud_only=flag) == ""


def test_refuses_cloud_only(fake_tree, open_spy, mock_attrs):
    paths = list(fake_tree.cloud_only)
    assert paths
    for p in paths:
        _call_all(p, True)   # explicit flag
        _call_all(p, False)  # flag false, caught by mocked attributes
    cloud_keys = {winutil.norm_key(p) for p in paths}
    assert not cloud_keys & set(open_spy)


def test_spy_sees_local_open(fake_tree, open_spy):
    p = fake_tree.files["photo_exif"]
    extract.read_image_info(p, is_cloud_only=False)
    assert winutil.norm_key(p) in open_spy
