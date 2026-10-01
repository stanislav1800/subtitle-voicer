"""Pictures of the GUI without loading OCR/TTS, to compare with docs/design/.

Run: uv run python tools/ui_snapshot.py [--out captures/ui]

Builds the real windows on an AppFacade that is never started (profiles and settings are
read from %APPDATA% as usual) and saves PNGs of every settings page and the tray menu.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from subvoicer.app import runtime
from subvoicer.app.facade import AppFacade
from subvoicer.ui import theme
from subvoicer.ui.app import Application

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "captures" / "ui")
    parser.add_argument("--page", help="только эта вкладка")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    runtime.prepare_process()
    qt = QApplication(sys.argv)
    theme.apply(qt)
    app = Application(qt, AppFacade())
    window = app.window
    window.resize(1100, 720)
    window.show()
    for key in window.page_keys():
        if args.page and key != args.page:
            continue
        window.show_page(key)
        for _ in range(3):
            qt.processEvents()
        path = args.out / f"{key}.png"
        window.grab().save(str(path))
        print(path)
    menu = app.tray.contextMenu()
    menu.popup(window.mapToGlobal(window.rect().center()))
    qt.processEvents()
    menu.grab().save(str(args.out / "tray.png"))
    print(args.out / "tray.png")
    menu.hide()
    window.hide()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
