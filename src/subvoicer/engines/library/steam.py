"""Steam (TZ 4.16): library folders from ``steamapps/libraryfolders.vdf``, games from
``appmanifest_*.acf``. Steam does not keep the exe name: games come with ``exe = None`` and are
bound on their first run (4.11). Checked on the user's files (2026-10-05)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from subvoicer.core.models import FoundGame

log = logging.getLogger(__name__)

STEAM_KEYS = (  # (hive, key, value): where the Steam client is installed
    ("HKCU", r"Software\Valve\Steam", "SteamPath"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
)
ICON_NAME = re.compile(r"^[0-9a-f]{40}\.(jpg|png|ico)$")  # the small icon in librarycache


def parse_library_folders(text: str) -> list[Path]:
    """``libraryfolders.vdf`` -> library roots (the Steam folder itself is one of them)."""
    import vdf

    data: dict[str, Any] = vdf.loads(text)
    folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
    roots: list[Path] = []
    for key, value in folders.items():
        if isinstance(value, dict) and value.get("path"):
            roots.append(Path(value["path"]))
        elif isinstance(value, str) and key.isdigit():  # the old format: "1" "D:\\Games"
            roots.append(Path(value))
    return roots


def parse_app_manifest(text: str) -> tuple[str, str, str] | None:
    """``appmanifest_<id>.acf`` -> (appid, name, installdir)."""
    import vdf

    state = vdf.loads(text).get("AppState") or {}
    appid, name, folder = state.get("appid"), state.get("name"), state.get("installdir")
    if not (appid and name and folder):
        return None
    return str(appid), str(name), str(folder)


def cached_icon(steam_dir: Path, appid: str) -> Path | None:
    """The game's small icon from Steam's local cache (no internet, TZ 4.16)."""
    folder = steam_dir / "appcache" / "librarycache" / appid
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if ICON_NAME.match(path.name):
                return path
    flat = steam_dir / "appcache" / "librarycache" / f"{appid}_icon.jpg"  # older clients
    return flat if flat.is_file() else None


def scan_steam(steam_dir: Path) -> list[FoundGame]:
    library_file = steam_dir / "steamapps" / "libraryfolders.vdf"
    roots = [steam_dir]
    if library_file.is_file():
        roots = parse_library_folders(library_file.read_text(encoding="utf-8", errors="replace"))
    found: list[FoundGame] = []
    seen: set[str] = set()
    for root in roots:
        apps = root / "steamapps"
        if not apps.is_dir():
            log.info("Steam: библиотеки %s нет — пропускаю", root)
            continue
        for manifest in sorted(apps.glob("appmanifest_*.acf")):
            try:
                parsed = parse_app_manifest(manifest.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                log.warning("Steam: не прочитан %s", manifest, exc_info=True)
                continue
            if parsed is None:
                continue
            appid, name, folder = parsed
            install_dir = apps / "common" / folder
            if appid in seen or not install_dir.is_dir():
                continue
            seen.add(appid)
            found.append(FoundGame(name, install_dir, None, cached_icon(steam_dir, appid)))
    return found


def steam_dir_from_registry() -> Path | None:
    import winreg

    hives = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    for hive, key, value in STEAM_KEYS:
        try:
            with winreg.OpenKey(hives[hive], key) as handle:
                path = Path(str(winreg.QueryValueEx(handle, value)[0]))
        except OSError:
            continue
        if path.is_dir():
            return path
    return None


class SteamLibrary:
    name = "steam"

    def __init__(self, steam_dir: Path | None = None) -> None:
        self._steam_dir = steam_dir

    def scan(self) -> list[FoundGame]:
        steam_dir = self._steam_dir or steam_dir_from_registry()
        if steam_dir is None:
            log.info("Steam не найден — пропускаю")
            return []
        return scan_steam(steam_dir)
