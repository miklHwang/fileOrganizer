"""Shared pytest fixtures. ``tidyup`` imports happen inside fixture bodies."""
from __future__ import annotations

import builtins
import functools
import io
import os

import pytest

from fixtures.fake_tree import FakeTree, build_fake_tree

CLOUD = 0x400000 | 0x1000
HIDDEN = 0x2


@pytest.fixture(autouse=True)
def _trusted_tmp_prefix(monkeypatch, tmp_path_factory):
    from tidyup import safety

    monkeypatch.setattr(safety, "TRUSTED_PREFIXES", (tmp_path_factory.getbasetemp().resolve(),))


@pytest.fixture
def fake_tree(tmp_path) -> FakeTree:
    return build_fake_tree(tmp_path)


@pytest.fixture
def cfg(fake_tree):
    from tidyup import config

    return config.load_config(None, overrides=fake_tree.config_overrides())


@pytest.fixture
def mock_attrs(monkeypatch, fake_tree):
    from tidyup import winutil

    real = winutil.get_file_attributes
    cloud = {winutil.norm_key(p) for p in fake_tree.cloud_only}
    hidden = {winutil.norm_key(p) for p in fake_tree.hidden}

    def fake(path, st=None):
        key = winutil.norm_key(path)
        if key in cloud:
            return CLOUD
        if key in hidden:
            return HIDDEN
        return real(path, st)

    monkeypatch.setattr(winutil, "get_file_attributes", fake)


@pytest.fixture
def cross_volume(monkeypatch):
    from tidyup import winutil

    monkeypatch.setattr(winutil, "same_volume", lambda a, b: False)


@pytest.fixture
def open_spy(monkeypatch) -> list[str]:
    from tidyup import winutil

    opened: list[str] = []

    def record(target) -> None:
        if isinstance(target, int) or hasattr(target, "read"):
            return
        try:
            opened.append(winutil.norm_key(os.fsdecode(target)))
        except Exception:
            pass

    def wrap(orig):
        @functools.wraps(orig, updated=())
        def inner(*a, **kw):
            if a:
                record(a[0])
            else:
                for k in ("file", "fp", "stream", "path", "docx"):
                    if k in kw:
                        record(kw[k])
                        break
            return orig(*a, **kw)
        return inner

    monkeypatch.setattr(builtins, "open", wrap(builtins.open))
    monkeypatch.setattr(io, "open", wrap(io.open))

    import PIL.Image
    import docx
    import pypdf

    monkeypatch.setattr(PIL.Image, "open", wrap(PIL.Image.open))
    monkeypatch.setattr(pypdf, "PdfReader", wrap(pypdf.PdfReader))
    monkeypatch.setattr(docx, "Document", wrap(docx.Document))
    return opened
