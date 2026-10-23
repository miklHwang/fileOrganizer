"""Per-file classification into Proposals (stub; filled by P3-T2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from tidyup.config import Config
    from tidyup.models import Confidence, FileRecord

RULE_NAMES = ("atomic_folder", "exact_duplicate", "installer", "installer_old", "archive", "screenshot",
              "photo", "video", "pdf", "document", "unsorted", "already_in_place", "already_filed",
              "cloud_only_leave_onedrive", "scan_error", "migrate")


@dataclass
class RuleContext:
    cfg: Config
    now: datetime


@dataclass
class Proposal:
    kind: str
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


def classify(record: FileRecord, ctx: RuleContext) -> Proposal:
    raise NotImplementedError


def screenshot_score(record: FileRecord, ctx: RuleContext) -> tuple[int, list[str]]:
    raise NotImplementedError


def date_from_filename(name: str) -> datetime | None:
    raise NotImplementedError


def photo_date(record: FileRecord) -> tuple[datetime, str]:
    raise NotImplementedError


def match_categories(text: str, filename: str, cfg: Config) -> CategoryMatch:
    raise NotImplementedError


def date_from_text(text: str, order: str = "MDY", now: datetime | None = None) -> datetime | None:
    raise NotImplementedError


def slugify(text: str, max_len: int = 40) -> str:
    raise NotImplementedError


def pdf_filename(date: datetime, category: str, description: str, cfg: Config) -> str:
    raise NotImplementedError


def is_in_target_structure(path: Path, cfg: Config) -> bool:
    raise NotImplementedError
