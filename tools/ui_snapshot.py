"""Pictures of the GUI without loading OCR/TTS, to compare with docs/design/.

Run: uv run python tools/ui_snapshot.py [--out captures/ui]

Builds the real windows on an AppFacade that is never started (profiles and settings are
read from %APPDATA% as usual) and saves PNGs of every settings page and the tray menu.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication

from subvoicer.app import runtime
from subvoicer.app.facade import AppFacade
from subvoicer.core.models import HistoryEntry
from subvoicer.ui import theme
from subvoicer.ui.app import Application

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "captures" / "ui")
    parser.add_argument("--page", help="только эта вкладка")
    parser.add_argument("--frame", type=Path, help="кадр игры: снимок редактора зон")
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
        page = window.current_page()
        bar = page.verticalScrollBar() if page is not None else None
        if bar is not None and bar.maximum() > 0:
            bar.setValue(bar.maximum())
            qt.processEvents()
            bottom = args.out / f"{key}_bottom.png"
            window.grab().save(str(bottom))
            print(bottom)
            bar.setValue(0)
    entry = HistoryEntry(
        1,
        datetime.now(),
        "dialog",
        "Джонни",
        "Арасака ещё хуже, чем Милитех, пацан.",
        voice="ru_eduard",
    )
    app.open_line(entry)
    line = app.line_window
    assert line is not None
    line._fix_button.setChecked(True)
    first = line._words.itemAt(0)
    if first is not None and first.widget() is not None:
        first.widget().click()
    for _ in range(3):
        qt.processEvents()
    line.grab().save(str(args.out / "line.png"))
    print(args.out / "line.png")
    line.hide()
    if args.frame is not None:
        from subvoicer.app import zone_tools
        from subvoicer.ui.zone_editor.editor import ZoneEditor

        image = zone_tools.load_image(args.frame)
        profile = next(iter(app.facade.profiles()), None)
        if image is not None and profile is not None:
            editor = ZoneEditor(app.facade, profile.path, image, "screenshot")
            editor.resize(1400, 860)
            editor.show()
            for _ in range(5):
                qt.processEvents()
            editor.grab().save(str(args.out / "zone_editor.png"))
            print(args.out / "zone_editor.png")
            editor.hide()
    from subvoicer.ui.notifications import Notification, mono

    note = Notification(
        "Похоже, это новая игра",
        f"{mono('stalker2.exe')} — создать профиль, чтобы озвучивать её субтитры?",
        ("Создать профиль", lambda: None),
        ("Не спрашивать", lambda: None),
        timeout_ms=None,
    )
    note.popup()
    for _ in range(3):
        qt.processEvents()
    note.grab().save(str(args.out / "notification.png"))
    print(args.out / "notification.png")
    note.close()
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
