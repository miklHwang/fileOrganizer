# PyInstaller spec for tidyup (onedir). Build: pyinstaller packaging/tidyup.spec --noconfirm
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH is provided by PyInstaller)
HERE = Path(SPECPATH).resolve()  # noqa: F821

# collect_submodules imports the package in a subprocess; make sure it can be found.
sys.path.insert(0, str(ROOT))
hidden = sorted(
    set(collect_submodules("tidyup"))
    | {f"tidyup.{p.stem}" for p in (ROOT / "tidyup").glob("*.py") if p.stem != "__init__"}
)
_required = ("tidyup.scan", "tidyup.plan", "tidyup.report", "tidyup.apply", "tidyup.undo",
             "tidyup.migrate", "tidyup.upkeep", "tidyup.gui", "tidyup.app")
_missing = [m for m in _required if m not in hidden]
assert not _missing, f"tidyup modules missing from the build (hiddenimports empty?): {_missing}"
datas = [(str(ROOT / "tidyup" / "default_config.yaml"), "tidyup")]
excludes = ["tests"]


def make_analysis(script):
    return Analysis(  # noqa: F821
        [str(HERE / script)],
        pathex=[str(ROOT)],
        binaries=[],
        datas=datas,
        hiddenimports=hidden,
        hookspath=[],
        runtime_hooks=[],
        excludes=excludes,
        noarchive=False,
    )


a_gui = make_analysis("tidyup_gui.py")
a_cli = make_analysis("tidyup_cli.py")

pyz_gui = PYZ(a_gui.pure)  # noqa: F821
pyz_cli = PYZ(a_cli.pure)  # noqa: F821

exe_gui = EXE(  # noqa: F821
    pyz_gui, a_gui.scripts, [],
    exclude_binaries=True, name="tidyup", console=False, upx=False,
)
exe_cli = EXE(  # noqa: F821
    pyz_cli, a_cli.scripts, [],
    exclude_binaries=True, name="tidyup-cli", console=True, upx=False,
)

coll = COLLECT(  # noqa: F821
    exe_gui, a_gui.binaries, a_gui.datas,
    exe_cli, a_cli.binaries, a_cli.datas,
    strip=False, upx=False, name="tidyup",
)
