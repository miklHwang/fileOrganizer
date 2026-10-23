"""Deterministic fake user file tree used by the whole test suite.

Must not import ``tidyup`` at module level (it has its own long-path helper).
"""
from __future__ import annotations

import hashlib
import io
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_MTIME = datetime(2024, 1, 10, 12, 0, 0)


def _lp(p: str | os.PathLike) -> str:
    """Add the long-path prefix on Windows (no tidyup import)."""
    s = os.path.abspath(os.fspath(p))
    if sys.platform != "win32" or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


@dataclass
class FakeTree:
    root: Path
    folders: dict[str, Path] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)
    cloud_only: list[Path] = field(default_factory=list)
    hidden: list[Path] = field(default_factory=list)
    protected: list[Path] = field(default_factory=list)
    excluded: list[Path] = field(default_factory=list)
    atomic_dirs: list[Path] = field(default_factory=list)
    duplicate_groups: list[list[Path]] = field(default_factory=list)
    version_chain: list[Path] = field(default_factory=list)
    collision_pair: tuple[Path, Path] = None  # type: ignore[assignment]
    long_path_file: Path = None  # type: ignore[assignment]

    def config_overrides(self) -> dict:
        return {
            "folders": {k: str(v) for k, v in self.folders.items()},
            "sources": ["documents", "desktop", "downloads", "pictures", "videos", "onedrive"],
            "work_dir": str(self.root / "tidyup_data"),
            "destination": {"root": None},
        }

    def all_files(self) -> list[Path]:
        out: list[Path] = []
        for dirpath, _dirs, names in os.walk(_lp(self.folders["home"])):
            base = Path(dirpath[4:] if dirpath.startswith("\\\\?\\") else dirpath)
            out.extend(base / n for n in names)
        return sorted(out)


# ---------------------------------------------------------------- helpers

def _write(path: Path, data: bytes, mtime: datetime | None = None) -> Path:
    os.makedirs(_lp(path.parent), exist_ok=True)
    with open(_lp(path), "wb") as fh:
        fh.write(data)
    ts = (mtime or DEFAULT_MTIME).timestamp()
    os.utime(_lp(path), (ts, ts))
    return path


def _text(text: str) -> bytes:
    return text.encode("utf-8")


def _pdf_text(lines: list[str]) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    y = 740
    for line in lines:
        c.drawString(72, y, line)
        y -= 24
    c.save()
    return buf.getvalue()


def _scanned_pdf() -> bytes:
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    img = Image.new("RGB", (300, 400), "white")
    for x in range(40, 260, 6):
        for y in range(60, 340, 40):
            img.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    c.drawImage(ImageReader(img), 50, 100, width=500, height=600)
    c.save()
    return buf.getvalue()


def _docx(lines: list[str]) -> bytes:
    import docx

    d = docx.Document()
    for line in lines:
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _png(size: tuple[int, int], seed: int) -> bytes:
    from PIL import Image, ImageDraw

    rnd = random.Random(seed)
    img = Image.new("RGB", size, (240, 240, 240))
    d = ImageDraw.Draw(img)
    for _ in range(12):
        x0, y0 = rnd.randrange(size[0]), rnd.randrange(size[1])
        d.rectangle([x0, y0, x0 + rnd.randrange(20, 300), y0 + rnd.randrange(20, 200)],
                    fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _photo_image(seed: int):
    from PIL import Image

    rnd = random.Random(seed)
    small = Image.new("RGB", (8, 6))
    small.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(48)])
    return small.resize((640, 480), Image.BICUBIC)


def _jpeg(img, make: str | None = None, model: str | None = None, dt: str | None = None) -> bytes:
    from PIL import Image

    exif = Image.Exif()
    if make:
        exif[0x010F] = make
    if model:
        exif[0x0110] = model
    if dt:
        exif.get_ifd(0x8769)[0x9003] = dt
    buf = io.BytesIO()
    if make or model or dt:
        img.save(buf, "JPEG", quality=90, exif=exif)
    else:
        img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ---------------------------------------------------------------- builder

def build_fake_tree(root: Path, now: datetime | None = None) -> FakeTree:
    now = now or datetime.now().replace(microsecond=0)
    root = Path(root)
    home = root / "home"
    f = {
        "home": home,
        "documents": home / "Documents",
        "desktop": home / "Desktop",
        "downloads": home / "Downloads",
        "pictures": home / "Pictures",
        "videos": home / "Videos",
        "onedrive": home / "OneDrive",
    }
    f["screenshots"] = f["pictures"] / "Screenshots"
    for p in f.values():
        os.makedirs(_lp(p), exist_ok=True)
    os.makedirs(_lp(root / "tidyup_data"), exist_ok=True)
    docs, desk, down, pics, vids, od, shots = (
        f["documents"], f["desktop"], f["downloads"], f["pictures"], f["videos"],
        f["onedrive"], f["screenshots"])
    t = FakeTree(root=root, folders=f)
    files = t.files

    def put(handle: str | None, path: Path, data: bytes, mtime: datetime | None = None) -> Path:
        _write(path, data, mtime)
        if handle:
            files[handle] = path
        return path

    # PDFs
    put("invoice_pdf", docs / "invoice_march.pdf", _pdf_text([
        "INVOICE", "Invoice number: INV-10023", "Invoice date: March 15, 2024",
        "Subtotal: $100.00", "Amount paid: $120.00", "Thank you for your purchase"]))
    put("statement_pdf", down / "estatement.pdf", _pdf_text([
        "First National Bank", "Statement period: 01/01/2024 - 01/31/2024",
        "Account number: ****1234", "Closing balance: $5,000.00", "Interest paid: $1.20"]))
    put("syllabus_pdf", desk / "CS101.pdf", _pdf_text([
        "CS 101 Course Syllabus", "Fall semester 2023", "Professor: Dr. Smith",
        "Assignments and grading", "Lecture schedule"]))
    put("scanned_pdf", docs / "scan_lease_2022.pdf", _scanned_pdf())
    put("plain_pdf", docs / "notes.pdf", _pdf_text(["Lorem ipsum dolor sit amet"]))

    # Office docs
    put("agenda_docx", docs / "Meeting agenda.docx",
        _docx(["Agenda", "Project kickoff meeting", "Action items"]))
    chain = [
        ("report v1.docx", "Report draft one", datetime(2024, 1, 1, 12)),
        ("report v2.docx", "Report draft two with edits", datetime(2024, 1, 2, 12)),
        ("report final_FINAL.docx", "Report final version", datetime(2024, 1, 3, 12)),
    ]
    for i, (name, body, mt) in enumerate(chain, start=1):
        p = put(f"version_{i}", desk / name, _docx([body]), mt)
        t.version_chain.append(p)
    files["version_chain"] = t.version_chain[0]

    # collision pair
    a = put("notes_txt_desktop", desk / "notes.txt", _text("desktop notes\n"))
    b = put("notes_txt_downloads", down / "notes.txt", _text("downloads notes, different\n"))
    t.collision_pair = (a, b)

    # screenshots
    put("screenshot_desktop", desk / "Screenshot 2024-05-01 101500.png", _png((1920, 1080), 1))
    put("screenshot_capture", shots / "Capture_001.png", _png((2560, 1440), 2))
    put("screenshot_phone", shots / "Screenshot_20240601-120000.png", _png((1080, 2400), 3))
    put("existing_review_screenshot",
        pics / "Screenshots_Review" / "Screenshot 2024-05-01 101500.png", _png((1920, 1080), 99))

    # photos
    base1 = _photo_image(11)
    put("photo_exif", down / "IMG_20240714_101010.jpg",
        _jpeg(base1, "Canon", "EOS 80D", "2024:07:14 10:10:10"))
    from PIL import Image

    base2 = _photo_image(22)
    data2 = _jpeg(base2, "NIKON", "D3500", "2023:01:02 09:00:00")
    p2 = put("photo_exif2", pics / "DSC_0001.jpg", data2)
    p3 = put("photo_dup", down / "DSC_0001 (1).jpg", data2)
    t.duplicate_groups.append([p2, p3])
    small = base2.resize((576, 432), Image.LANCZOS)
    put("photo_near", pics / "DSC_0001_small.jpg",
        _jpeg(small, "NIKON", "D3500", "2023:01:02 09:00:00"))
    put("drawing_png", pics / "drawing.png", _png((500, 500), 5))

    # budgets (duplicate group)
    xl = b"PK\x03\x04 fake xlsx budget contents\n" * 20
    x1 = put("budget_xlsx", docs / "budget.xlsx", xl)
    x2 = put("budget_copy", desk / "budget (1).xlsx", xl)
    t.duplicate_groups.append([x1, x2])

    # installers / archives
    put("installer_old", down / "setup_tool.exe", b"MZ" + b"\0" * 2000, now - timedelta(days=200))
    put("installer_new", down / "new_app.msi", b"\xd0\xcf\x11\xe0" + b"\1" * 2000, now - timedelta(days=5))
    put("archive_zip", down / "photos_backup.zip", b"PK\x05\x06" + b"\0" * 18)
    put("archive_7z", down / "old.7z", b"7z\xbc\xaf\x27\x1c" + b"\2" * 100)

    # videos
    rnd = random.Random(7)
    put("video", vids / "VID_20230505_101010.mp4", rnd.randbytes(4096))
    put("video_nodate", vids / "clip.mov", rnd.randbytes(2048))
    put("misc", down / "data.bin", rnd.randbytes(512))

    # atomic dirs
    git = docs / "Code" / "myapp"
    put(None, git / ".git" / "HEAD", _text("ref: refs/heads/main\n"))
    put(None, git / ".git" / "config", _text("[core]\n\trepositoryformatversion = 0\n"))
    put(None, git / "src" / "main.py", _text("print('hello')\n"))
    put(None, git / "README.md", _text("# myapp\n"))
    put(None, git / "notes.txt", _text("project notes\n"))
    node = desk / "webapp"
    put(None, node / "package.json", _text('{"name": "webapp"}\n'))
    put(None, node / "node_modules" / "x" / "index.js", _text("module.exports = 1;\n"))
    game = docs / "My Games" / "SomeGame"
    put(None, game / "save.dat", rnd.randbytes(256))
    files["git_project"], files["node_project"] = git, node
    files["app_folder"] = docs / "My Games"
    t.atomic_dirs = [git, node, docs / "My Games"]

    # OneDrive
    t.cloud_only = [
        put("cloud_pdf", od / "Taxes 2023.pdf", _pdf_text(["Tax return 2023"])),
        put("cloud_photo", od / "IMG_20220101_120000.jpg",
            _jpeg(_photo_image(33), "Apple", "iPhone", "2022:01:01 12:00:00")),
        put("cloud_screenshot", od / "Screenshot_cloud.png", _png((1920, 1080), 8)),
    ]
    put("onedrive_letter", od / "Personal letter.docx", _docx(["Dear friend,", "Hello."]))

    # hidden
    t.hidden = [
        put("hidden_dot", docs / ".hidden_notes.txt", _text("hidden\n")),
        put("hidden_attr", desk / "secret.txt", _text("secret\n")),
    ]

    # protected
    t.protected = [
        put("protected_appdata", docs / "AppData" / "cache.txt", _text("cache\n")),
        put("protected_recycle", down / "$Recycle.Bin" / "deleted.txt", _text("deleted\n")),
        put("protected_programfiles", down / "Program Files (x86)" / "app" / "tool.exe", b"MZ\0\0"),
    ]

    # excluded
    t.excluded = [
        put("excluded_desktop_ini", desk / "desktop.ini", _text("[.ShellClassInfo]\n")),
        put("excluded_crdownload", down / "movie.crdownload", rnd.randbytes(300)),
    ]

    # long path
    lp = docs
    for ch in "abcd":
        lp = lp / (ch * 60)
    t.long_path_file = put("long_path_file", lp / "long name document.txt", _text("long path\n"))
    return t


def snapshot(root: Path) -> dict[str, tuple[str, int] | None]:
    """relative POSIX path -> (sha256, int(mtime)) for files, None for directories."""
    root = Path(root)
    out: dict[str, tuple[str, int] | None] = {}

    def walk(d: Path) -> None:
        with os.scandir(_lp(d)) as it:
            entries = sorted(it, key=lambda e: e.name)
        for e in entries:
            p = d / e.name
            rel = p.relative_to(root).as_posix()
            if e.is_dir(follow_symlinks=False):
                out[rel] = None
                walk(p)
            else:
                h = hashlib.sha256()
                with open(_lp(p), "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                out[rel] = (h.hexdigest(), int(os.stat(_lp(p)).st_mtime))

    walk(root)
    return out
