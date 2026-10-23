"""Data models, enums, type groups and inventory/manifest CSV I/O."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from tidyup import winutil
from tidyup.errors import ManifestError, TidyupError


class Action(StrEnum):
    MOVE = "move"
    RENAME = "rename"
    MOVE_RENAME = "move_rename"
    REVIEW_DELETE = "review_delete"
    DUPLICATE = "duplicate"
    SKIP = "skip"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EntryType(StrEnum):
    FILE = "file"
    ATOMIC_FOLDER = "atomic_folder"


TYPE_GROUPS: dict[str, frozenset[str]] = {
    "Images": frozenset(".jpg .jpeg .png .gif .bmp .webp .heic .heif .tif .tiff .svg .ico .dng .cr2 .nef .arw .raw".split()),
    "Videos": frozenset(".mp4 .mov .avi .mkv .wmv .m4v .3gp .webm .mts .m2ts .flv".split()),
    "Audio": frozenset(".mp3 .wav .flac .m4a .aac .ogg .wma".split()),
    "PDFs": frozenset({".pdf"}),
    "Documents": frozenset(".doc .docx .xls .xlsx .ppt .pptx .txt .rtf .odt .ods .odp .md .csv .pages .numbers .key .epub".split()),
    "Installers": frozenset(".exe .msi .msix .appx".split()),
    "Archives": frozenset(".zip .7z .rar .tar .gz .tgz .bz2 .xz .iso".split()),
}


def type_group(extension: str) -> str:
    ext = (extension or "").lower()
    for group, exts in TYPE_GROUPS.items():
        if ext in exts:
            return group
    return "Other"


@dataclass
class FileRecord:
    path: Path
    entry_type: EntryType
    source_root: str
    size: int
    extension: str
    created: datetime
    modified: datetime
    is_cloud_only: bool
    sha256: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    exif_datetime: datetime | None = None
    phash: str | None = None
    atomic_root: Path | None = None
    file_count: int = 1
    error: str = ""


INVENTORY_COLUMNS = ["path", "entry_type", "source_root", "size", "extension", "created", "modified",
                     "is_cloud_only", "sha256", "image_width", "image_height", "camera_make", "camera_model",
                     "exif_datetime", "phash", "atomic_root", "file_count", "error"]


@dataclass
class ManifestRow:
    id: int
    action: Action
    source: Path
    destination: Path | None
    rule: str
    confidence: Confidence
    notes: str
    approved: bool


MANIFEST_COLUMNS = ["id", "action", "source", "destination", "rule", "confidence", "notes", "approved"]

_DT_FORMAT = "%Y-%m-%d %H:%M:%S"
_TRUE = {"y", "yes", "true", "1", "x"}


def format_dt(dt: datetime | None) -> str:
    return "" if dt is None else dt.strftime(_DT_FORMAT)


def parse_dt(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in (_DT_FORMAT, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        raise ValueError(f"Unrecognised date: {s!r}") from None


def _ps(path: Path | str | None) -> str:
    """Path to string without any long-path prefix; None -> empty."""
    if path is None:
        return ""
    s = str(path)
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[8:]
    if s.startswith("\\\\?\\"):
        return s[4:]
    return s


def _yn(b: bool) -> str:
    return "Y" if b else "N"


def _opt(v: object) -> str:
    return "" if v is None else str(v)


_FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _neutralise(v: object) -> object:
    """Prefix text cells that Excel would treat as formulas with an apostrophe."""
    if isinstance(v, str) and v.startswith(_FORMULA_CHARS):
        return "'" + v
    return v


def _unneutralise(v: str) -> str:
    if v.startswith("'") and v[1:2] and v[1] in _FORMULA_CHARS:
        return v[1:]
    return v


def _write_csv(path: Path, columns: list[str], rows: Iterable[list[str]], delimiter: str) -> int:
    path = Path(path)
    Path(winutil.long_path(path.parent)).mkdir(parents=True, exist_ok=True)
    n = 0
    with open(winutil.long_path(path), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
        w.writerow(columns)
        for r in rows:
            w.writerow([_neutralise(c) for c in r])
            n += 1
    return n


def write_inventory(path: Path, records: Iterable[FileRecord], delimiter: str = ",") -> int:
    def gen():
        for r in records:
            yield [_ps(r.path), r.entry_type.value, r.source_root, r.size, r.extension,
                   format_dt(r.created), format_dt(r.modified), _yn(r.is_cloud_only), _opt(r.sha256),
                   _opt(r.image_width), _opt(r.image_height), _opt(r.camera_make), _opt(r.camera_model),
                   format_dt(r.exif_datetime), _opt(r.phash), _ps(r.atomic_root), r.file_count, r.error]
    return _write_csv(path, INVENTORY_COLUMNS, gen(), delimiter)


def _read_text(path: Path) -> str:
    data = Path(winutil.long_path(path)).read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _read_table(path: Path) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """Returns (normalised header, [(row_number, {col: value})]). Row numbers count the header as row 1."""
    try:
        text = _read_text(path)
    except OSError as exc:
        raise TidyupError(f"Cannot read {path}: {exc.strerror or exc}") from exc
    first = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t"), key=lambda d: first.count(d)) if first else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header: list[str] | None = None
    out: list[tuple[int, dict[str, str]]] = []
    for row in reader:
        rownum = reader.line_num
        if header is None:
            header = [h.strip().lower() for h in row]
            continue
        if not row or all(not c.strip() for c in row):
            continue
        out.append((rownum, {h: (_unneutralise(row[i]) if i < len(row) else "") for i, h in enumerate(header)}))
    return header or [], out


def _int(v: str, row: int, col: str, what: type[TidyupError]) -> int:
    try:
        return int(float(v.strip()))
    except ValueError:
        raise what(f"Row {row}: column '{col}' is not a number: {v!r}") from None


def read_inventory(path: Path) -> list[FileRecord]:
    header, rows = _read_table(path)
    missing = [c for c in INVENTORY_COLUMNS if c not in header]
    if missing:
        raise TidyupError(f"Inventory {path} is missing column(s): {', '.join(missing)}")
    recs: list[FileRecord] = []
    for n, d in rows:
        def oint(c: str) -> int | None:
            return _int(d[c], n, c, TidyupError) if d[c].strip() else None
        try:
            created, modified = parse_dt(d["created"]), parse_dt(d["modified"])
            exif = parse_dt(d["exif_datetime"])
            etype = EntryType(d["entry_type"].strip())
        except ValueError as exc:
            raise TidyupError(f"Inventory row {n}: {exc}") from None
        recs.append(FileRecord(
            path=Path(d["path"]), entry_type=etype, source_root=d["source_root"],
            size=_int(d["size"], n, "size", TidyupError) if d["size"].strip() else 0,
            extension=d["extension"], created=created or datetime.min, modified=modified or datetime.min,
            is_cloud_only=d["is_cloud_only"].strip().lower() in _TRUE,
            sha256=d["sha256"] or None, image_width=oint("image_width"), image_height=oint("image_height"),
            camera_make=d["camera_make"] or None, camera_model=d["camera_model"] or None,
            exif_datetime=exif, phash=d["phash"] or None,
            atomic_root=Path(d["atomic_root"]) if d["atomic_root"] else None,
            file_count=oint("file_count") or 1 if d["file_count"].strip() else 1, error=d["error"]))
    return recs


def write_manifest(path: Path, rows: Iterable[ManifestRow], delimiter: str = ",") -> int:
    def gen():
        for r in rows:
            yield [r.id, Action(r.action).value, _ps(r.source), _ps(r.destination), r.rule,
                   Confidence(r.confidence).value, r.notes, _yn(r.approved)]
    return _write_csv(path, MANIFEST_COLUMNS, gen(), delimiter)


def read_manifest(path: Path) -> list[ManifestRow]:
    header, rows = _read_table(path)
    missing = [c for c in MANIFEST_COLUMNS if c not in header]
    if missing:
        raise ManifestError(f"Manifest {path} is missing column(s): {', '.join(missing)}")
    out: list[ManifestRow] = []
    for n, d in rows:
        try:
            action = Action(d["action"].strip().lower())
        except ValueError:
            raise ManifestError(f"Row {n}: invalid action {d['action']!r}") from None
        try:
            conf = Confidence(d["confidence"].strip().lower())
        except ValueError:
            raise ManifestError(f"Row {n}: invalid confidence {d['confidence']!r}") from None
        src = d["source"].strip()
        if not src:
            raise ManifestError(f"Row {n}: source is empty")
        dst = d["destination"].strip()
        out.append(ManifestRow(
            id=_int(d["id"], n, "id", ManifestError), action=action, source=Path(src),
            destination=Path(dst) if dst else None, rule=d["rule"], confidence=conf,
            notes=d["notes"], approved=d["approved"].strip().lower() in _TRUE))
    return out
