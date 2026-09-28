import logging
import sys
import threading
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from subvoicer.app import log as app_log


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    logger = logging.getLogger("subvoicer")
    handlers, level = list(logger.handlers), logger.level
    excepthook, thread_hook = sys.excepthook, threading.excepthook
    yield
    for handler in logger.handlers:
        if handler not in handlers:
            handler.close()
    logger.handlers[:] = handlers
    logger.setLevel(level)
    sys.excepthook, threading.excepthook = excepthook, thread_hook


def _own_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger("subvoicer").handlers if getattr(h, app_log._HANDLER_MARK)]


def test_writes_utf8_rotating_log(tmp_path: Path) -> None:
    log_file = app_log.setup_logging(tmp_path / "logs")
    logging.getLogger("subvoicer.test").info("Привет, лог")
    for handler in _own_handlers():
        handler.flush()
    assert "Привет, лог" in log_file.read_text(encoding="utf-8")

    (rotating,) = [h for h in _own_handlers() if isinstance(h, RotatingFileHandler)]
    assert rotating.maxBytes == 2 * 1024 * 1024
    assert rotating.backupCount + 1 == 5


def test_repeated_setup_does_not_duplicate_handlers(tmp_path: Path) -> None:
    app_log.setup_logging(tmp_path, console=True)
    app_log.setup_logging(tmp_path, console=True)
    assert len(_own_handlers()) == 2


def test_uncaught_thread_exception_is_logged(tmp_path: Path) -> None:
    log_file = app_log.setup_logging(tmp_path)

    def boom() -> None:
        raise RuntimeError("поток упал")

    thread = threading.Thread(target=boom, name="Worker")
    thread.start()
    thread.join()
    for handler in _own_handlers():
        handler.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "Необработанное исключение в потоке Worker" in text
    assert "поток упал" in text
