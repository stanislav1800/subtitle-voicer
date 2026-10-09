"""Library scan -> profiles; deleted ones stay hidden; manual add by exe (TZ 4.16, 1.18)."""

from __future__ import annotations

from pathlib import Path

import pytest

from subvoicer.app import library
from subvoicer.app.facade import AppFacade
from subvoicer.config.paths import AppPaths
from subvoicer.config.settings import load_settings
from subvoicer.core.events import LibraryScanned
from subvoicer.core.models import FoundGame


class Source:
    def __init__(self, games: list[FoundGame]) -> None:
        self.games = games

    def scan(self) -> list[FoundGame]:
        return list(self.games)


class Broken:
    def scan(self) -> list[FoundGame]:
        raise OSError("нет доступа")


def saver(source: Path, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"png")
    return True


def test_scan_adds_profiles_with_icons_once(tmp_path: Path) -> None:
    facade = AppFacade(AppPaths(tmp_path))
    steam_dir = tmp_path / "Steam" / "common" / "Cyberpunk 2077"
    sources = {
        "steam": Source(
            [
                FoundGame("Cyberpunk 2077", steam_dir, None, tmp_path / "icon.jpg"),
                FoundGame("Steamworks Common Redistributables", tmp_path / "S", None, None),
            ]
        ),
        "epic": Broken(),  # a launcher that cannot be read is skipped
        "gog": Source([]),
    }
    result = library.scan_library(facade.store, facade.paths, facade.settings, sources, saver)
    assert [s.profile.title for s in result.added] == ["Cyberpunk 2077"]
    assert result.failed == ["epic"] and result.found == 2
    profile = result.added[0].profile
    assert profile.exe is None and profile.install_dir == str(steam_dir)
    assert profile.layout.zones == []  # «не настроено»
    assert profile.icon is not None and facade.icon_path(profile) is not None
    again = library.scan_library(facade.store, facade.paths, facade.settings, sources, saver)
    assert again.added == []


def test_deleted_library_game_stays_hidden_until_added_by_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facade = AppFacade(AppPaths(tmp_path))
    folder = tmp_path / "Games" / "Hades"
    folder.mkdir(parents=True)
    sources = {"steam": Source([FoundGame("Hades", folder, None, None)])}
    (stored,) = library.scan_library(facade.store, facade.paths, facade.settings, sources).added
    facade.delete_profile(stored.path)
    assert load_settings(facade.paths.settings_file).library_hidden == [str(folder)]
    assert library.scan_library(facade.store, facade.paths, facade.settings, sources).added == []

    monkeypatch.setattr("subvoicer.app.runtime.product_name", lambda _exe: "Hades II")
    monkeypatch.setattr(library, "default_icon_saver", lambda: saver)
    manual = facade.add_game(str(folder / "Hades.exe"))
    assert (manual.profile.title, manual.profile.exe) == ("Hades II", "Hades.exe")
    assert manual.profile.icon is not None
    assert facade.settings.library_hidden == []


def test_facade_scan_announces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    facade = AppFacade(AppPaths(tmp_path))
    game = FoundGame("Hades", tmp_path / "Hades", None, None)
    monkeypatch.setattr(
        library,
        "scan_library",
        lambda store, paths, settings: library.LibraryScan(
            added=[facade.store.save(_profile(game))]
        ),
    )
    seen: list[object] = []
    facade.subscribe(LibraryScanned, seen.append)
    facade.scan_library()
    assert seen == [LibraryScanned(("Hades",))]


def _profile(game: FoundGame):  # type: ignore[no-untyped-def]
    from subvoicer.config.profile import DefaultVoices, Profile

    return Profile(
        install_dir=str(game.install_dir),
        title=game.title,
        narrator_voice="n",
        default_voices=DefaultVoices(male="m", female="f"),
    )
