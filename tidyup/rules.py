"""Per-file classification into Proposals.

Cloud-only (OneDrive online-only) files are never passed to ``extract``; they are
classified by name, extension, size and dates only.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from tidyup import extract, winutil
from tidyup.models import TYPE_GROUPS, Confidence, EntryType, type_group

if TYPE_CHECKING:
    from tidyup.config import Config
    from tidyup.models import FileRecord

RULE_NAMES = ("atomic_folder", "exact_duplicate", "installer", "installer_old", "archive", "screenshot",
              "photo", "video", "pdf", "document", "unsorted", "already_in_place", "already_filed",
              "cloud_only_leave_onedrive", "scan_error", "migrate")


@dataclass
class RuleContext:
    cfg: Config
    now: datetime


@dataclass
class Proposal:
    kind: str                     # "file" | "review_delete" | "skip"
    logical_dir: str | None
    new_name: str | None
    rule: str
    confidence: Confidence
    notes: list[str] = field(default_factory=list)


@dataclass
class CategoryMatch:
    best: str | None
    text_hits: dict[str, list[str]]
    filename_hits: dict[str, list[str]]


_FILENAME_DATE = re.compile(r"(?<!\d)(20\d{2}|19\d{2})[-_]?(\d{2})[-_]?(\d{2})(?!\d)")
_GENERIC_STEM = re.compile(r"^(scan|scanned|document|doc|file|untitled|img|image|pdf)?[\s_-]*\d*$", re.I)

_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
           "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
           "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
           "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_MON = r"(?P<mon>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")"
_TEXT_DATE_PATTERNS = (
    ("iso", re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")),
    ("num", re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")),
    ("dmy", re.compile(r"(?<![A-Za-z0-9])(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r"\.?,?\s+(\d{4})(?!\d)", re.I)),
    ("mdy", re.compile(r"(?<![A-Za-z0-9])" + _MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)", re.I)),
)


# --------------------------------------------------------------------------- helpers

def date_from_filename(name: str) -> datetime | None:
    for m in _FILENAME_DATE.finditer(name):
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
    return None


def date_from_text(text: str, order: str = "MDY", now: datetime | None = None) -> datetime | None:
    now = now or datetime.now()
    best: tuple[int, datetime] | None = None
    for kind, rx in _TEXT_DATE_PATTERNS:
        for m in rx.finditer(text or ""):
            try:
                if kind == "iso":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif kind == "num":
                    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    mo, d = (b, a) if order.upper() == "DMY" else (a, b)
                elif kind == "dmy":
                    d, mo, y = int(m.group(1)), _MONTHS[m.group("mon").lower()], int(m.group(3))
                else:
                    mo, d, y = _MONTHS[m.group("mon").lower()], int(m.group(2)), int(m.group(3))
                if not (1990 <= y <= now.year + 1):
                    continue
                dt = datetime(y, mo, d)
            except ValueError:
                continue
            if best is None or m.start() < best[0]:
                best = (m.start(), dt)
            break  # first plausible match of this pattern is enough
    return best[1] if best else None


def slugify(text: str, max_len: int = 40) -> str:
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[A-Za-z0-9]+", ascii_text)
    out = ""
    for w in words:
        cand = w if not out else out + "-" + w
        if len(cand) > max_len:
            if not out:
                out = w[:max_len]
            break
        out = cand
    return out


def pdf_filename(date: datetime, category: str, description: str, cfg: Config) -> str:
    naming = cfg.naming
    max_len = int(naming.get("description_max_len", 40))
    desc = slugify(description, max_len) or "Document"
    cat = slugify(category, 40) or "Other"
    fmt = naming.get("date_format", "%Y-%m-%d")
    pattern = naming.get("pdf_pattern", "{date}_{category}_{description}")
    try:
        stem = pattern.format(date=date.strftime(fmt), category=cat, description=desc)
    except (KeyError, IndexError, ValueError):
        stem = f"{date.strftime(fmt)}_{cat}_{desc}"
    return stem + ".pdf"


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s.casefold()).strip()


def match_categories(text: str, filename: str, cfg: Config) -> CategoryMatch:
    t = _collapse(text or "")
    base, ext = Path(filename or "").stem, Path(filename or "").suffix
    if not (ext[1:].isalnum() and 1 <= len(ext) - 1 <= 5):
        base = filename or ""
    f = _collapse(re.sub(r"[_\-.]+", " ", base))
    text_hits: dict[str, list[str]] = {}
    name_hits: dict[str, list[str]] = {}
    best: str | None = None
    best_key = (0, 0)
    for cat, body in cfg.categories.items():
        if cat.casefold() == "other":
            continue
        th: list[str] = []
        fh: list[str] = []
        for kw in body.get("keywords") or []:
            k = _collapse(str(kw))
            if not k:
                continue
            rx = re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])")
            if rx.search(t) and k not in th:
                th.append(k)
            if rx.search(f) and k not in fh:
                fh.append(k)
        text_hits[cat], name_hits[cat] = th, fh
        key = (len(th), len(fh))
        if key > best_key:
            best, best_key = cat, key
    return CategoryMatch(best, text_hits, name_hits)


def photo_date(record: FileRecord) -> tuple[datetime, str]:
    if record.exif_datetime is not None:
        return record.exif_datetime, "exif"
    d = date_from_filename(record.path.name)
    if d is not None:
        return d, "filename"
    return record.modified, "modified"


def _folder_path(cfg: Config, key: str) -> Path | None:
    return cfg.folders.get(key)


def _in_listed_folders(record: FileRecord, keys, cfg: Config) -> bool:
    for key in keys or []:
        folder = _folder_path(cfg, str(key))
        if folder is not None and winutil.is_within(record.path, folder):
            return True
    return False


def screenshot_score(record: FileRecord, ctx: RuleContext) -> tuple[int, list[str]]:
    cfg = ctx.cfg
    sc = cfg.rules["screenshot"]
    w = sc["weights"]
    score = 0
    reasons: list[str] = []

    def add(key: str, why: str) -> None:
        nonlocal score
        pts = int(w.get(key, 0))
        score += pts
        reasons.append(f"{why} ({pts:+d})")

    name = record.path.name.casefold()
    if any(name.startswith(str(p).casefold()) for p in sc["name_prefixes"]):
        add("name_prefix", "name looks like a screenshot")
    shots = cfg.folders.get("screenshots")
    if shots is not None and winutil.is_within(record.path, shots):
        add("in_screenshots_folder", "in the Screenshots folder")
    if record.extension.lower() == ".png":
        add("png", "PNG file")
    has_camera = bool(record.camera_make or record.camera_model)
    readable = (not record.is_cloud_only) and record.image_width is not None and record.image_height is not None
    if readable and not has_camera:
        add("no_camera_exif", "no camera info")
    if has_camera:
        add("has_camera_exif", "camera info present")
    if readable:
        width, height = int(record.image_width), int(record.image_height)
        resolutions = set()
        for r in sc["resolutions"]:
            try:
                a, b = str(r).lower().split("x")
                resolutions.add((int(a), int(b)))
            except ValueError:
                continue
        if (width, height) in resolutions or (height, width) in resolutions:
            add("screen_resolution", f"{width}x{height} is a screen size")
        elif height > width > 0:
            ratio = height / width
            tol = float(sc["aspect_tolerance"])
            if any(abs(ratio - float(r)) <= tol for r in sc["phone_aspect_ratios"]):
                add("phone_aspect", "phone screen shape")
    return score, reasons


def _structure_dir(cfg: Config, key: str, default: str) -> str:
    return cfg.structure.get(key, default)


def _category_dir(cfg: Config, category: str) -> str:
    return f"{_structure_dir(cfg, 'categories_root', 'Documents')}/{category}"


def _logical_root(logical: str) -> str:
    parts = []
    for p in logical.replace("\\", "/").split("/"):
        if "{" in p:
            break
        parts.append(p)
    return "/".join(parts)


def is_in_target_structure(path: Path, cfg: Config) -> bool:
    outside = Path("/__tidyup_outside__")
    sources = (path, outside)

    def targets(logical: str):
        seen = set()
        for src in sources:
            try:
                t = cfg.resolve_destination(logical, src)
            except Exception:
                continue
            k = winutil.norm_key(t)
            if k not in seen:
                seen.add(k)
                yield t

    for cat in cfg.categories:
        for t in targets(_category_dir(cfg, cat)):
            if winutil.is_within(path, t):
                return True
    for key in ("photos", "screenshots", "installers", "archives", "unsorted", "duplicates", "review_delete"):
        root = _logical_root(cfg.structure.get(key, ""))
        if not root:
            continue
        for t in targets(root):
            if winutil.is_within(path, t):
                return True
    vroot = _logical_root(cfg.structure.get("videos", ""))
    if vroot:
        for t in targets(vroot):
            if winutil.is_within(path, t) and winutil.norm_key(path) != winutil.norm_key(t):
                rel = Path(winutil.norm_key(path)).relative_to(Path(winutil.norm_key(t)))
                if re.fullmatch(r"\d{4}", rel.parts[0]):
                    return True
    return False


# --------------------------------------------------------------------------- classify

def _high_match(m: CategoryMatch, cfg: Config) -> bool:
    if m.best is None:
        return False
    need = int(cfg.rules["pdf"].get("high_min_hits", 2))
    n = len(m.text_hits[m.best])
    others = [len(v) for k, v in m.text_hits.items() if k != m.best]
    runner = max(others) if others else 0
    return n >= need and n >= 2 * runner


def _pdf_description(stem: str, text: str) -> str:
    if _GENERIC_STEM.match(stem):
        for line in (text or "").splitlines():
            if len(re.findall(r"[A-Za-z]", line)) >= 3:
                words = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", line).encode("ascii", "ignore").decode())
                d = slugify(" ".join(words[:6]))
                if d:
                    return d
        return "Document"
    return slugify(stem) or "Document"


def _classify_pdf(record: FileRecord, ctx: RuleContext) -> Proposal:
    cfg = ctx.cfg
    unsorted_pdf = _structure_dir(cfg, "unsorted", "_Unsorted") + "/PDFs"
    name = record.path.name
    text = ""
    if not record.is_cloud_only:
        text = extract.pdf_text(record.path, is_cloud_only=False,
                                max_pages=int(cfg.rules["pdf"].get("max_pages", 2)))
    m = match_categories(text, name, cfg)
    if text.strip():
        if _high_match(m, cfg):
            best = m.best
            date = date_from_text(text, str(cfg.rules["pdf"].get("date_order", "MDY")), ctx.now)
            note_date = "date from text" if date else "date from modified time"
            date = date or record.modified
            desc = _pdf_description(record.path.stem, text)
            hits = ", ".join(m.text_hits[best])
            return Proposal("file", _category_dir(cfg, best), pdf_filename(date, best, desc, cfg), "pdf",
                            Confidence.HIGH, [f"category {best}: text matched {hits}", note_date])
        if m.best is not None:
            return Proposal("file", unsorted_pdf, None, "pdf", Confidence.MEDIUM,
                            [f"guessed category {m.best} but not confident enough to rename"])
        return Proposal("file", unsorted_pdf, None, "pdf", Confidence.LOW, ["no category keywords found"])
    why = "cloud-only: classified by name only" if record.is_cloud_only else "no text layer (scanned?)"
    if m.best is not None and m.filename_hits[m.best]:
        return Proposal("file", _category_dir(cfg, m.best), None, "pdf", Confidence.MEDIUM,
                        [why, f"filename matched {', '.join(m.filename_hits[m.best])} ({m.best})"])
    return Proposal("file", unsorted_pdf, None, "pdf", Confidence.LOW, [why])


def _classify_document(record: FileRecord, ctx: RuleContext, ext: str) -> Proposal:
    cfg = ctx.cfg
    docs = cfg.rules["documents"]
    text = ""
    if ext in docs.get("text_extensions", []) and not record.is_cloud_only:
        text = extract.docx_text(record.path, is_cloud_only=False)
    m = match_categories(text, record.path.name, cfg)
    if text.strip() and _high_match(m, cfg):
        return Proposal("file", _category_dir(cfg, m.best), None, "document", Confidence.HIGH,
                        [f"category {m.best}: text matched {', '.join(m.text_hits[m.best])}"])
    if m.best is not None:
        parts = []
        if m.text_hits[m.best]:
            parts.append(f"text matched {', '.join(m.text_hits[m.best])}")
        if m.filename_hits[m.best]:
            parts.append(f"filename matched {', '.join(m.filename_hits[m.best])}")
        return Proposal("file", _category_dir(cfg, m.best), None, "document", Confidence.MEDIUM,
                        [f"category {m.best}: " + "; ".join(parts)])
    return Proposal("file", _structure_dir(cfg, "unsorted", "_Unsorted") + "/Documents", None,
                    "document", Confidence.LOW, ["no category keywords found"])


KEPT_ARCHIVE_NOTE = "archive: kept in its folder"


def is_kept_archive(record: FileRecord, cfg: Config) -> bool:
    """True when this file is an archive that must stay where it is (rules.archives.keep_in_place)."""
    arc = cfg.rules.get("archives", {})
    return bool(arc.get("keep_in_place", True)) and record.extension.lower() in arc.get("extensions", [])


def _classify(record: FileRecord, ctx: RuleContext) -> Proposal:
    cfg, now = ctx.cfg, ctx.now
    ext = record.extension.lower()
    rules = cfg.rules

    inst = rules["installers"]
    if ext in inst["extensions"] and _in_listed_folders(record, inst.get("only_in"), cfg):
        days = int(inst["review_delete_after_days"])
        age = now - record.modified
        if age > timedelta(days=days):
            return Proposal("review_delete", _structure_dir(cfg, "review_delete", "_Review_Delete"), None,
                            "installer_old", Confidence.LOW,
                            [f"installer last modified {age.days} days ago (over {days})"])
        return Proposal("file", _structure_dir(cfg, "installers", "Installers"), None, "installer",
                        Confidence.HIGH, [f"installer, modified {max(age.days, 0)} days ago"])
    arc = rules["archives"]
    if is_kept_archive(record, cfg):
        return Proposal("skip", None, None, "archive", Confidence.HIGH, [KEPT_ARCHIVE_NOTE])
    if (not arc.get("keep_in_place", True) and ext in arc["extensions"]
            and _in_listed_folders(record, arc.get("only_in"), cfg)):
        return Proposal("file", _structure_dir(cfg, "archives", "Archives"), None, "archive",
                        Confidence.HIGH, ["archive file"])

    if ext in TYPE_GROUPS["Images"]:
        score, reasons = screenshot_score(record, ctx)
        sc = rules["screenshot"]
        if score >= int(sc["min_score"]):
            conf = Confidence.HIGH if score >= int(sc["high_score"]) else Confidence.MEDIUM
            return Proposal("file", _structure_dir(cfg, "screenshots", "Pictures/Screenshots_Review"), None,
                            "screenshot", conf, [f"screenshot score {score}: " + ", ".join(reasons)])

    has_camera = bool(record.camera_make or record.camera_model)
    if ext in rules["photos"]["extensions"] or (ext in TYPE_GROUPS["Images"] and has_camera):
        d, source = photo_date(record)
        logical = _structure_dir(cfg, "photos", "Pictures/Photos/{yyyy}/{yyyy}-{mm}").format(
            yyyy=f"{d.year:04d}", mm=f"{d.month:02d}")
        conf = Confidence.MEDIUM if source == "modified" else Confidence.HIGH
        return Proposal("file", logical, None, "photo", conf, [f"date from {source}"])

    if ext in rules["videos"]["extensions"]:
        d = date_from_filename(record.path.name)
        source = "filename"
        if d is None:
            d, source = record.modified, "modified"
        logical = _structure_dir(cfg, "videos", "Videos/{yyyy}").format(yyyy=f"{d.year:04d}")
        return Proposal("file", logical, None, "video",
                        Confidence.MEDIUM if source == "modified" else Confidence.HIGH,
                        [f"date from {source}"])

    if ext == ".pdf":
        return _classify_pdf(record, ctx)

    docs = rules["documents"]
    if ext in docs.get("text_extensions", []) or ext in docs.get("filename_only_extensions", []):
        return _classify_document(record, ctx, ext)

    return Proposal("file", f"{_structure_dir(cfg, 'unsorted', '_Unsorted')}/{type_group(ext)}", None,
                    "unsorted", Confidence.LOW, ["no rule matched"])


def classify(record: FileRecord, ctx: RuleContext) -> Proposal:
    if record.entry_type != EntryType.FILE:
        raise ValueError("classify() handles FILE records only")
    p = _classify(record, ctx)
    if record.is_cloud_only:
        p.notes.append("cloud-only")
    return p
