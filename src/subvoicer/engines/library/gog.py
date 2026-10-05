"""GOG (TZ 4.16): registry ``HKLM\\SOFTWARE\\WOW6432Node\\GOG.com\\Games\\<id>``.

The user has no GOG: the values follow open-source readers (Steam ROM Manager
``gog-galaxy.parser``), not entries checked here (TZ 1.18).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from subvoicer.core.models import FoundGame

log = logging.getLogger(__name__)

GAMES_KEY = r"SOFTWARE\WOW6432Node\GOG.com\Games"


def parse_entry(values: Mapping[str, Any]) -> FoundGame | None:
    """One game's values; DLC (``dependsOn``) and entries without a folder are skipped."""
    title = values.get("gameName")
    folder = values.get("path") or values.get("workingDir")
    if not (title and folder) or values.get("dependsOn"):
        return None
    exe_value = values.get("exe") or values.get("launchCommand") or ""
    exe = Path(str(exe_value).strip('"')).name if exe_value else None
    return FoundGame(str(title), Path(str(folder)), exe or None, None)


def read_registry() -> list[dict[str, Any]]:
    import winreg

    entries: list[dict[str, Any]] = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, GAMES_KEY) as games:
        for index in range(winreg.QueryInfoKey(games)[0]):
            name = winreg.EnumKey(games, index)
            with winreg.OpenKey(games, name) as game:
                values: dict[str, Any] = {}
                for j in range(winreg.QueryInfoKey(game)[1]):
                    key, value, _ = winreg.EnumValue(game, j)
                    values[key] = value
                entries.append(values)
    return entries


class GogLibrary:
    name = "gog"

    def scan(self) -> list[FoundGame]:
        try:
            entries = read_registry()
        except OSError:
            log.info("GOG не найден — пропускаю")
            return []
        found = [parse_entry(values) for values in entries]
        return [g for g in found if g is not None and g.install_dir.is_dir()]
