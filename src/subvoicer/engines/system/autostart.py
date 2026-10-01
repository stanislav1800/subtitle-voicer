"""Start with Windows (TZ 4.14): the ``HKCU\\...\\Run`` registry value."""

from __future__ import annotations

import logging
import winreg

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SubtitleVoicer"


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool, command: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
            log.info("Автозапуск включён: %s", command)
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
                log.info("Автозапуск выключен")
            except FileNotFoundError:
                pass
