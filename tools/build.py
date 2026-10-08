"""Build the installer (stage 12, TZ 1.24).

    uv run --group build python tools/build.py [--no-installer]

1. the app icon (``tools/build/app.ico``) and the exe version info, from the app's own icon;
2. PyInstaller: ``dist/SubtitleVoicer`` — ``SubtitleVoicer.exe`` and ``_internal``;
3. ``uv.exe`` next to it (installs the character voices on a computer without uv);
4. Inno Setup: ``dist/SubtitleVoicer-Setup-<version>.exe``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "tools" / "build"
DIST = ROOT / "dist"
APP_DIR = DIST / "SubtitleVoicer"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041904B0', [
      StringStruct('ProductName', 'Subtitle Voicer'),
      StringStruct('FileDescription', 'Subtitle Voicer — озвучка субтитров в играх'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('ProductVersion', '{version}'),
      StringStruct('OriginalFilename', 'SubtitleVoicer.exe')])]),
    VarFileInfo([VarStruct('Translation', [0x0419, 1200])])
  ]
)
"""


def version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def make_icon() -> None:
    """The tray/window icon of the app, as a multi-size .ico for the exe and the installer."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtWidgets import QApplication

    from subvoicer.ui import theme

    app = QApplication.instance() or QApplication([])
    icon = theme.app_icon()
    images = []
    for size in ICON_SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        icon.pixmap(size, size).toImage().save(buffer, "PNG")
        from io import BytesIO

        images.append(Image.open(BytesIO(bytes(buffer.data()))).convert("RGBA"))
    images[-1].save(
        BUILD / "app.ico", sizes=[(s, s) for s in ICON_SIZES], append_images=images[:-1]
    )
    del app


def write_version_info(text: str) -> None:
    nums = tuple(int(x) for x in [*text.split("."), "0", "0", "0"][:4])
    (BUILD / "version.txt").write_text(
        VERSION_INFO.format(nums=nums, version=text), encoding="utf-8"
    )


def iscc() -> Path:
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES(X86)", "")):
        candidate = (
            Path(base) / ("Programs" if "Local" in base else "") / "Inno Setup 6" / "ISCC.exe"
        )
        if candidate.is_file():
            return candidate
    raise SystemExit("Не найден Inno Setup 6 (winget install --id JRSoftware.InnoSetup -e)")


def folder_size(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--no-installer", action="store_true", help="только папка dist/SubtitleVoicer"
    )
    args = parser.parse_args()
    text = version()
    print(f"Subtitle Voicer {text}")
    make_icon()
    write_version_info(text)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(BUILD / "subvoicer.spec"),
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST),
            "--workpath",
            str(ROOT / "build" / "pyinstaller"),
        ],
        check=True,
    )
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("Не найден uv — его нужно положить рядом с программой")
    shutil.copy2(uv, APP_DIR / "uv.exe")
    print(f"Папка: {APP_DIR} ({folder_size(APP_DIR) / 2**20:.0f} МБ)")
    if args.no_installer:
        return 0
    subprocess.run(
        [
            str(iscc()),
            f"/DAppVersion={text}",
            f"/DSourceDir={APP_DIR}",
            f"/DOutputDir={DIST}",
            str(BUILD / "installer.iss"),
        ],
        check=True,
    )
    setup = DIST / f"SubtitleVoicer-Setup-{text}.exe"
    print(f"Установщик: {setup} ({setup.stat().st_size / 2**20:.0f} МБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
