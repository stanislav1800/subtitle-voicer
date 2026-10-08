# PyInstaller spec of the installed app (TZ 1.24): a folder dist/SubtitleVoicer with
# SubtitleVoicer.exe; tools/build.py runs it and then makes the installer.
# ruff: noqa
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent.parent
SRC = ROOT / "src"

datas = [
    *collect_data_files("subvoicer"),  # resources, fonts, icons
    # the character-voice helper runs as a script in its own environment (TZ 1.21)
    (str(SRC / "subvoicer" / "engines" / "tts" / "convert_host.py"), "subvoicer/engines/tts"),
    *collect_data_files("silero"),
    *collect_data_files("silero_stress"),
    *collect_data_files("rapidocr"),
    *collect_data_files("_sounddevice_data"),
    *collect_data_files("num2words"),
]
hiddenimports = [
    *collect_submodules("subvoicer"),  # engines are imported by name from the registries
    *collect_submodules("silero"),
    *collect_submodules("silero_stress"),
    *collect_submodules("winrt"),
    *collect_submodules("pynput"),
    "pycaw.pycaw",
    "comtypes.stream",
]

a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(SRC)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "tkinter", "matplotlib", "IPython", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore",
        # development tools of the project, not the app
        "pytest", "_pytest", "mypy", "mypyc", "ruff", "importlinter", "grimp", "PyInstaller",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SubtitleVoicer",
    console=False,
    icon=str(Path(SPECPATH) / "app.ico"),
    version=str(Path(SPECPATH) / "version.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="SubtitleVoicer")
