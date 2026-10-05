"""Which found games become profiles (TZ 4.16, 1.18)."""

from __future__ import annotations

from pathlib import Path

from subvoicer.config.resources import read_list
from subvoicer.core.models import FoundGame
from subvoicer.pipeline.games.library import folder_key, is_ignored, new_games

IGNORE = read_list("library_ignore.txt")


def game(title: str, folder: str, exe: str | None = None) -> FoundGame:
    return FoundGame(title, Path(folder), exe, None)


def test_service_entries_and_programs_are_ignored() -> None:
    for title in (
        "Steamworks Common Redistributables",
        "Steam Linux Runtime 3.0 (sniper)",
        "Proton 9.0",
        "Wallpaper Engine",
        "Half-Life 2: Lost Coast Dedicated Server",
        "Cyberpunk 2077 Soundtrack",
    ):
        assert is_ignored(title, IGNORE), title
    for title in ("Cyberpunk 2077", "SILENT HILL 2", "Protonaut Shooter"):
        assert not is_ignored(title, IGNORE), title


def test_folder_key_ignores_case_and_separators() -> None:
    assert folder_key("C:/Games/Witcher 3/") == folder_key(r"c:\games\witcher 3")


def test_new_games_skip_known_hidden_and_duplicates() -> None:
    found = [
        game("Cyberpunk 2077", r"C:\Steam\common\Cyberpunk 2077"),
        game("Disco Elysium", r"C:\GOG Games\Disco Elysium", "disco.exe"),
        game("Old", r"D:\Old"),
        game("Witcher", r"C:\Epic\Witcher"),
        game("Witcher", r"c:\epic\witcher\\"),  # the same folder from another source
        game("Hades", r"C:\Epic\Hades", "Hades.exe"),
        game("Steamworks Common Redistributables", r"C:\Steam\common\Steamworks Shared"),
    ]
    added = new_games(
        found,
        known_dirs=[r"c:\steam\common\cyberpunk 2077"],
        known_exes=["hades.exe"],  # made from the running game, no install_dir
        hidden_dirs=[r"D:\Old"],
        ignore=IGNORE,
    )
    assert [g.title for g in added] == ["Disco Elysium", "Witcher"]
