"""Content readers, cloud-only guarded (stub; filled by P2-T3)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"})


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    camera_make: str | None
    camera_model: str | None
    exif_datetime: datetime | None
    phash: str | None


def read_image_info(path: Path, *, is_cloud_only: bool, want_phash: bool = True) -> ImageInfo | None:
    raise NotImplementedError


def pdf_text(path: Path, *, is_cloud_only: bool, max_pages: int = 2, max_chars: int = 20000) -> str:
    raise NotImplementedError


def docx_text(path: Path, *, is_cloud_only: bool, max_chars: int = 20000) -> str:
    raise NotImplementedError


def thumbnail_data_uri(path: Path, *, is_cloud_only: bool, max_px: int = 160) -> str | None:
    raise NotImplementedError
