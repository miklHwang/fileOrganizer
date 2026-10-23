from __future__ import annotations

import importlib
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import tidyup

ROOT = Path(__file__).resolve().parent.parent


def _modules() -> list[str]:
    return [m.name for m in pkgutil.iter_modules(tidyup.__path__) if m.name != "__main__"]


@pytest.mark.parametrize("name", _modules())
def test_imports(name):
    importlib.import_module(f"tidyup.{name}")


def test_no_schedule_module():
    assert "schedule" not in _modules()
    assert not (ROOT / "tidyup" / "schedule.py").exists()


def test_version_via_subprocess():
    out = subprocess.run([sys.executable, "-m", "tidyup", "--version"], cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0
    assert out.stdout.strip() == "tidyup 0.1.0"
