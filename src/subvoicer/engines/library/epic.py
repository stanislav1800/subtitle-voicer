"""Epic Games (TZ 4.16): JSON manifests ``*.item`` in
``%ProgramData%\\Epic\\EpicGamesLauncher\\Data\\Manifests``.

The user has no Epic launcher: the fields follow open-source readers (Playnite
``InstalledManifiest``, Steam ROM Manager ``epic.parser``), not files checked here (TZ 1.18).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from subvoicer.core.models import FoundGame

log = logging.getLogger(__name__)


def default_manifests_dir() -> Path:
    program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    return Path(program_data) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"


def parse_manifest(data: dict[str, Any]) -> FoundGame | None:
    """One ``.item``; add-ons and entries without a launch exe are skipped."""
    title = data.get("DisplayName")
    folder = data.get("InstallLocation")
    launch = data.get("LaunchExecutable")
    if not (title and folder and launch):
        return None
    main = data.get("MainGameAppName")
    if main and main != data.get("AppName"):
        return None  # DLC of another game
    if "addons" in (data.get("AppCategories") or []):
        return None
    return FoundGame(str(title), Path(folder), Path(str(launch)).name, None)


def scan_epic(manifests_dir: Path) -> list[FoundGame]:
    found: list[FoundGame] = []
    for path in sorted(manifests_dir.glob("*.item")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            log.warning("Epic: не прочитан %s", path, exc_info=True)
            continue
        game = parse_manifest(data) if isinstance(data, dict) else None
        if game is not None and game.install_dir.is_dir():
            found.append(game)
    return found


class EpicLibrary:
    name = "epic"

    def __init__(self, manifests_dir: Path | None = None) -> None:
        self._dir = manifests_dir or default_manifests_dir()

    def scan(self) -> list[FoundGame]:
        if not self._dir.is_dir():
            log.info("Epic Games не найден — пропускаю")
            return []
        return scan_epic(self._dir)
