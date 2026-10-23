# PyInstaller spec for tidyup (onedir). Build: pyinstaller packaging/tidyup.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH is provided by PyInstaller)
HERE = Path(SPECPATH).resolve()  # noqa: F821

hidden = collect_submodules("tidyup")
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
