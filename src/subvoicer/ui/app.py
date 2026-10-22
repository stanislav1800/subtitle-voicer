"""GUI entry point: ``uv run python -m subvoicer`` (TZ 4.15)."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from subvoicer.app import runtime, zone_tools
from subvoicer.app.facade import AppFacade
from subvoicer.app.log import setup_logging
from subvoicer.app.models_setup import ModelSetup
from subvoicer.config.paths import AppPaths
from subvoicer.core.events import (
    CaptureFailed,
    CaptureLooksBlack,
    DebugCaptured,
    EngineUnavailable,
    Event,
    GameActivated,
    GameClosed,
    LayoutMissing,
    LibraryScanned,
    ProfileBound,
    ProfileOffered,
    SettingsApplied,
)
from subvoicer.core.models import HistoryEntry, WindowRef
from subvoicer.ui import theme
from subvoicer.ui.bridge import Bridge
from subvoicer.ui.first_run import FirstRunWindow
from subvoicer.ui.line_window import LineWindow
from subvoicer.ui.notifications import Notification, Notifier, mono
from subvoicer.ui.overlay import Overlay
from subvoicer.ui.select_area import AreaSelector, Fractions
from subvoicer.ui.settings.general import GeneralPage
from subvoicer.ui.settings.history import HistoryPage
from subvoicer.ui.settings.hotkeys import HotkeysPage
from subvoicer.ui.settings.profiles import ProfilesPage
from subvoicer.ui.settings.window import SettingsWindow
from subvoicer.ui.tray import Tray, pretty
from subvoicer.ui.widgets.wheel import install_wheel_guard
from subvoicer.ui.zone_editor.editor import ZoneEditor

log = logging.getLogger("subvoicer.ui")
QUIT_GUARD_S = 10  # after «Выход» the process must be gone by then


def _force_exit() -> None:
    """The volume is restored and settings are saved by now; a stuck exit must not leave a
    copy that blocks the next start."""
    log.warning("Выход не завершился за %d с — завершаю процесс принудительно", QUIT_GUARD_S)
    logging.shutdown()
    os._exit(0)


FREEZE_DELAY_MS = 300  # the editor takes the focus from the game before it is paused
UI_BEAT_MS = 500  # the interface tells the facade it is alive (the paused-game watchdog)
SERVER_NAME = "SubtitleVoicer.Show"  # a second copy asks the first to show its window
STATUS_EVENTS = (GameActivated, GameClosed, LayoutMissing)


def tell_first_copy() -> None:
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(1000):
        socket.write(b"show")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()


class HotkeySignals(QObject):
    """Hotkeys fire in the pynput thread; windows open in the Qt one."""

    zone_editor = Signal()
    select_read = Signal()


class Loader(QObject):
    """Loads OCR and TTS off the GUI thread."""

    done = Signal(str)  # "" on success, else the error

    def __init__(self, facade: AppFacade) -> None:
        super().__init__()
        self._facade = facade

    def start(self) -> None:
        threading.Thread(target=self._run, name="Loader", daemon=True).start()

    def _run(self) -> None:
        try:
            self._facade.start()
        except Exception as exc:
            log.exception("Не удалось запустить озвучку")
            self.done.emit(str(exc))
            return
        self.done.emit("")


class Application:
    def __init__(self, qt: QApplication, facade: AppFacade) -> None:
        self.qt = qt
        self.facade = facade
        self.bridge = Bridge(facade)
        self.window = SettingsWindow(facade)
        self._build_pages()
        self.tray = Tray(
            facade,
            open_settings=self.window.bring_up,
            open_line=self.open_line,
            open_zone_editor=self.open_zone_editor,
            quit_app=self.quit,
        )
        self.bridge.event.connect(self._on_event)
        self.server = QLocalServer()
        QLocalServer.removeServer(SERVER_NAME)
        self.server.newConnection.connect(self._on_second_copy)
        self.server.listen(SERVER_NAME)
        self.notifier = Notifier()
        self._missing_shown: set[str] = set()  # games told «set up zones» in this run
        self.overlay = Overlay(facade)
        self.line_window: LineWindow | None = None
        self.editor: ZoneEditor | None = None
        self.hotkey_signals = HotkeySignals()
        self.hotkey_signals.zone_editor.connect(self.open_zone_editor)
        facade.set_hotkey_action("zone_editor", self.hotkey_signals.zone_editor.emit)
        self.hotkey_signals.select_read.connect(self.select_and_read)
        facade.set_hotkey_action("select_read", self.hotkey_signals.select_read.emit)
        self.selector: AreaSelector | None = None
        self.loader = Loader(facade)
        self.loader.done.connect(self._on_loaded)
        self._beat = QTimer()
        self._beat.timeout.connect(facade.ui_alive)
        self._beat.start(UI_BEAT_MS)

    def _build_pages(self) -> None:
        self.general = GeneralPage(self.facade)
        self.window.add_page("general", "Общие", "sliders-horizontal", self.general)
        self.profiles = ProfilesPage(self.facade, self.open_profile_editor)
        self.window.add_page("profiles", "Профили и голоса", "users", self.profiles)
        self.hotkeys = HotkeysPage(self.facade)
        self.window.add_page("hotkeys", "Горячие клавиши", "keyboard", self.hotkeys)
        self.history = HistoryPage(self.facade)
        self.window.add_page("history", "История", "history", self.history)
        self.window.set_page_visible("history", self.facade.settings.dev_mode)

    def run(self) -> None:
        self.tray.show()
        setup = ModelSetup(self.facade.paths)
        if setup.missing():  # first run: models before OCR and TTS load (TZ 4.14)
            self.first_run = FirstRunWindow(setup, self.loader.start, self.quit)
            self.first_run.start()
            return
        self.loader.start()

    def open_line(self, entry: HistoryEntry) -> None:
        if self.line_window is None:
            self.line_window = LineWindow(self.facade)
        current = self.facade.history.get(entry.id) or entry  # voice may be known by now
        self.line_window.show_entry(current)

    def open_profile_editor(self, path: Path, mode: str) -> None:
        """«Настроить в игре» / «По скриншоту» from the profile tab."""
        if mode == "game":
            status = self.facade.status()
            if status.profile_path != str(path):
                self.tray.showMessage(
                    "Subtitle Voicer", "Запустите эту игру — редактор откроется поверх неё"
                )
                return
            self.open_zone_editor()
            return
        start = str(self.facade.paths.captures_dir) if self.facade.settings.dev_mode else ""
        name, _ = QFileDialog.getOpenFileName(
            self.window, "Полный кадр игры", start, "Изображения (*.png *.jpg *.jpeg *.bmp)"
        )
        if name:
            self.open_screenshot(path, Path(name))

    def open_screenshot(self, path: Path, name: Path) -> None:
        """The zone editor «по скриншоту» of profile ``path`` on the picture ``name``."""
        image = zone_tools.load_image(name)
        if image is None:
            QMessageBox.warning(self.window, "Скриншот", "Не удалось открыть картинку")
            return
        warnings = []
        if name.suffix.lower() in (".jpg", ".jpeg"):
            warnings.append(
                "JPEG: цвета могут быть неточными (сжатие, кадр из видео) — проверьте маску в игре."
            )
        self._show_editor(ZoneEditor(self.facade, path, image, "screenshot", warnings))

    def open_zone_editor(self) -> None:
        """Ctrl+Alt+Z or «Настроить зоны»: a frozen frame of the active game."""
        status = self.facade.status()
        if status.profile_path is None:
            self.tray.showMessage("Subtitle Voicer", "Нет активной игры с профилем")
            return
        frame = self.facade.grab_game_frame()
        if frame is None:
            self.tray.showMessage("Subtitle Voicer", "Не удалось получить кадр игры")
            return
        editor = ZoneEditor(self.facade, Path(status.profile_path), frame, "game")
        self._show_editor(editor)
        # the game waits while its zones are set up (TZ 1.30) — paused only once the editor
        # is over it: showing a window over a paused game waits for an answer from the game
        QTimer.singleShot(FREEZE_DELAY_MS, lambda: self._freeze_for(editor))

    def _freeze_for(self, editor: ZoneEditor) -> None:
        if self.editor is not editor:
            return  # closed already
        if self.facade.freeze_game() is False:
            self.notifier.show(
                Notification(
                    "Игра не встала на паузу",
                    "Windows не дала остановить игру (так бывает у игр с античитом). "
                    "Поставьте паузу в самой игре или выключите паузу в профиле игры.",
                )
            )

    def select_and_read(self) -> None:
        """Ctrl+Alt+A: select an area on the frozen game frame, it is read once (TZ 4.12)."""
        status = self.facade.status()
        if status.window is None or status.profile_path is None:
            return  # available only with an active game
        frame = self.facade.game_frame()
        if frame is None:
            self.tray.showMessage("Subtitle Voicer", "Не удалось получить кадр игры")
            return
        if self.selector is not None:
            self.selector.close()

        def read(rect: Fractions) -> None:
            threading.Thread(
                target=self.facade.read_area, args=(frame, rect), name="ReadArea", daemon=True
            ).start()

        hwnd = status.window.hwnd
        self.selector = AreaSelector(
            frame, runtime.client_rect(hwnd), self.facade.game_monitor(), read
        )
        self.selector.destroyed.connect(lambda: setattr(self, "selector", None))
        self.selector.open()

    def _show_editor(self, editor: ZoneEditor) -> None:
        if self.editor is not None:
            self.editor.close()
        self.editor = editor
        # the game goes on before the editor hides: hiding gives the focus back to the game
        editor.closing.connect(lambda e=editor: self._editor_closed(e))
        editor.destroyed.connect(lambda _=None, e=editor: self._editor_closed(e))
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        editor.open()

    def _editor_closed(self, editor: ZoneEditor) -> None:
        if self.editor is editor:
            self.editor = None
            self.facade.thaw_game()  # the game paused for this editor goes on

    def quit(self) -> None:
        log.info("Выход")
        self.overlay.hide()
        self.tray.hide()
        self.facade.stop()  # restores the game's volume
        # exit(), not quit(): quit() asks the windows first and was seen not to end the loop,
        # leaving a copy without voicing that holds the single-instance lock
        self.qt.exit(0)
        guard = threading.Timer(QUIT_GUARD_S, _force_exit)
        guard.daemon = True
        guard.start()

    def _on_loaded(self, error: str) -> None:
        if error:
            self.tray.showMessage("Subtitle Voicer", f"Озвучка не запустилась: {error}")
        self.tray.refresh()
        self.window.refresh_status()
        # TZ 4.16: installed games are looked up at start (after OCR and TTS have loaded)
        threading.Thread(target=self.facade.scan_library, name="Library", daemon=True).start()

    def _on_event(self, event: Event) -> None:
        if isinstance(event, STATUS_EVENTS):
            self.tray.refresh()
            self.window.refresh_status()
        match event:
            case ProfileOffered(window=window):
                self.tray.offer(window, self._create_profile)
                self.notifier.show(
                    Notification(
                        "Похоже, это новая игра",
                        f"{mono(window.exe)} — создать профиль, чтобы озвучивать её субтитры? "
                        "Если не успели нажать — пункт есть в меню трея.",
                        ("Создать профиль", lambda: self._create_profile(window)),
                        ("Не спрашивать", lambda: self.facade.never_ask(window.exe)),
                        timeout_ms=20_000,
                    )
                )
            case LayoutMissing(profile_path=path, title=title):
                if path in self._missing_shown:
                    return  # once per game and run: resizing a window passes many sizes
                self._missing_shown.add(path)
                key = pretty(self.facade.settings.hotkeys["zone_editor"])
                self.notifier.show(
                    Notification(
                        "Настройте зоны для этой игры",
                        f"{title or 'Игра'}: зоны ещё не заданы. В игре нажмите {key} — "
                        "редактор откроется поверх неё.",
                        ("Настроить зоны", self.open_zone_editor),
                        None,
                        timeout_ms=20_000,
                    )
                )
            case ProfileBound(title=title):
                self.notifier.show(Notification("Игра привязана", f"{title} — профиль готов."))
                self.profiles.refresh()
            case EngineUnavailable(family="characters_cpu", reason=reason):
                self.notifier.show(
                    Notification(
                        "Голоса персонажей — на процессоре",
                        f"{reason.capitalize()}. Голоса звучат, но с задержкой в несколько секунд.",
                        timeout_ms=20_000,
                    )
                )
            case EngineUnavailable(family="characters", reason=reason):
                self.notifier.show(
                    Notification(
                        "Голоса персонажей недоступны",
                        f"{reason}. Пока звучат их голоса-основы (Xenia, Eugene).",
                        timeout_ms=15_000,
                    )
                )
            case LibraryScanned(added=added):
                self.profiles.library_scanned()
                if added:
                    names = ", ".join(added[:5]) + (
                        f" и ещё {len(added) - 5}" if len(added) > 5 else ""
                    )
                    self.notifier.show(
                        Notification(
                            "Найдены игры",
                            f"{names} — в «Профилях и голосах»; "
                            "зоны настраиваются при запуске игры.",
                        )
                    )
            case CaptureFailed():
                self.notifier.show(
                    Notification(
                        "Не удаётся захватить окно игры",
                        "Продолжаю пытаться, пока игра запущена.",
                    )
                )
            case CaptureLooksBlack():
                self.notifier.show(
                    Notification(
                        "Захват окна отдаёт чёрный кадр",
                        "Похоже, игра в эксклюзивном полноэкранном режиме. Включите в игре "
                        "оконный режим без рамки или выберите захват «Авто» или «Экран» "
                        "в профиле игры.",
                        timeout_ms=20_000,
                    )
                )
            case DebugCaptured(folder=folder):
                profile = self.facade.status().profile_path
                frame = Path(folder) / "frame.png"
                action = (
                    ("Открыть в редакторе", lambda: self.open_screenshot(Path(profile), frame))
                    if profile is not None
                    else None
                )
                self.notifier.show(Notification("Кадр сохранён", mono(folder), action))
            case _:
                pass
        if isinstance(event, SettingsApplied):
            self.tray.refresh()
            self.window.refresh_status()
            self.general.sync()
            self.window.set_page_visible("history", self.facade.settings.dev_mode)
            self.hotkeys.refresh()
        if isinstance(event, (*STATUS_EVENTS, SettingsApplied)):
            self.overlay.sync()

    def _create_profile(self, window: WindowRef) -> None:
        try:
            stored = self.facade.create_profile_for(window)
        except (OSError, ValueError, FileExistsError) as exc:
            self.notifier.show(Notification("Профиль не создан", str(exc)))
            return
        log.info("Создан профиль %s", stored.path.name)
        self.tray.offer(None, self._create_profile)
        self.profiles.refresh()

    def _on_second_copy(self) -> None:
        connection = self.server.nextPendingConnection()
        if connection is not None:
            connection.close()
        self.window.bring_up()


def main() -> int:
    paths = AppPaths.default()
    # first of all: a start that ends early (second copy, Qt error) must leave a trace
    log_file = setup_logging(paths.logs_dir, level=logging.INFO, console=False)
    autostart = runtime.AUTOSTART_FLAG in sys.argv
    log.info(
        "Subtitle Voicer запущен%s (pid %d), лог: %s",
        " с Windows" if autostart else "",
        os.getpid(),
        log_file,
    )
    runtime.prepare_process()
    qt = QApplication(sys.argv)
    qt.setApplicationName("Subtitle Voicer")
    qt.setQuitOnLastWindowClosed(False)
    instance = runtime.acquire_single_instance()
    if instance is None:
        log.info("Уже запущен другой экземпляр — показываю его окно и выхожу")
        tell_first_copy()
        return 0
    theme.apply(qt)
    install_wheel_guard(qt)  # the wheel scrolls the page, not the field under it
    facade = AppFacade(paths)
    if facade.settings.autostart:
        try:  # the command follows the environment (venv moved, new flags)
            runtime.set_autostart(True)
        except OSError:
            log.exception("Не удалось обновить автозапуск")
    app = Application(qt, facade)
    app.run()
    if not autostart:  # at logon: only the tray icon
        QTimer.singleShot(0, app.window.bring_up)
    try:
        code = qt.exec()
        log.info("Цикл событий завершён (%d)", code)
        return code
    finally:
        facade.stop()
        runtime.release_single_instance(instance)
