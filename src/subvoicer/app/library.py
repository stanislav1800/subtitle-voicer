"""Game library (TZ 4.16): installed games from Steam / Epic / GOG become profiles without
zones; their icons are cached in ``icons/`` as PNG. A missing launcher is skipped."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from subvoicer.app import profile_commands as pc
from subvoicer.config.paths import AppPaths
from subvoicer.config.profile_store import ProfileStore, StoredProfile
from subvoicer.config.resources import read_list
from subvoicer.config.settings import Settings
from subvoicer.core.models import FoundGame
from subvoicer.engines import registry
from subvoicer.pipeline.games.library import folder_key, new_games

log = logging.getLogger(__name__)

IconSaver = Callable[[Path, Path], bool]  # source (exe or picture) -> PNG target


@dataclass
class LibraryScan:
    added: list[StoredProfile] = field(default_factory=list)
    found: int = 0
    failed: list[str] = field(default_factory=list)  # sources that raised


def library_ignore() -> list[str]:
    return read_list("library_ignore.txt")


def icon_name(title: str, install_dir: str) -> str:
    """``icons/<slug>-<sha1(dir)[:8]>.png``: readable and unique per install folder."""
    slug = re.sub(r"[^\w]+", "-", title.casefold()).strip("-")[:40] or "game"
    digest = hashlib.sha1(folder_key(install_dir).encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}.png"


def default_icon_saver() -> IconSaver:
    from subvoicer.engines.system.icons import save_icon

    return save_icon


def cache_icon(
    paths: AppPaths, title: str, install_dir: str, source: Path | None, saver: IconSaver
) -> str | None:
    """The profile's ``icon`` value (relative to the data folder), or None."""
    if source is None:
        return None
    name = icon_name(title, install_dir)
    if saver(source, paths.icons_dir / name):
        return f"icons/{name}"
    return None


def scan_library(
    store: ProfileStore,
    paths: AppPaths,
    settings: Settings,
    sources: Mapping[str, Any] | None = None,
    saver: IconSaver | None = None,
) -> LibraryScan:
    """Find installed games and add the new ones as profiles (status «не настроено»)."""
    result = LibraryScan()
    found: list[FoundGame] = []
    for name in settings.library_sources:
        try:
            source = sources[name] if sources is not None else registry.resolve("library", name)()
            games = source.scan()
        except Exception:
            log.warning("Библиотека %s не прочитана — пропускаю", name, exc_info=True)
            result.failed.append(name)
            continue
        log.info("Библиотека %s: найдено %d", name, len(games))
        found += games
    result.found = len(found)
    profiles = store.load_all()
    known_dirs = [p.profile.install_dir for p in profiles if p.profile.install_dir]
    known_exes = [p.profile.exe for p in profiles if p.profile.exe]
    saver = saver or default_icon_saver()
    for game in new_games(found, known_dirs, known_exes, settings.library_hidden, library_ignore()):
        install_dir = str(game.install_dir)
        icon = cache_icon(paths, game.title, install_dir, game.icon_path, saver)
        try:
            stored = pc.new_profile(store, game.exe, game.title, install_dir, icon=icon)
        except (OSError, ValueError) as exc:
            log.warning("Профиль для %s не создан: %s", game.title, exc)
            continue
        log.info("Игра из библиотеки добавлена: %s (%s)", game.title, install_dir)
        result.added.append(stored)
    return result
