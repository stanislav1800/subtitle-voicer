"""Library formats (TZ 4.16) on samples: Steam from the user's files, Epic and GOG from
open-source readers (TZ 1.18)."""

from __future__ import annotations

import json
from pathlib import Path

from subvoicer.engines.library.epic import EpicLibrary, parse_manifest
from subvoicer.engines.library.gog import parse_entry
from subvoicer.engines.library.steam import SteamLibrary, parse_library_folders

FIXTURES = Path(__file__).parents[2] / "fixtures" / "library"


def steam_tree(tmp_path: Path) -> tuple[Path, Path]:
    steam, lib2 = tmp_path / "Steam", tmp_path / "Games"
    for root in (steam, lib2):
        (root / "steamapps" / "common").mkdir(parents=True)
    text = (FIXTURES / "steam" / "libraryfolders.vdf").read_text(encoding="utf-8")
    text = text.replace("STEAM_DIR", str(steam).replace("\\", "\\\\"))
    text = text.replace("LIB2_DIR", str(lib2).replace("\\", "\\\\"))
    (steam / "steamapps" / "libraryfolders.vdf").write_text(text, encoding="utf-8")
    for appid, root, folder in (
        ("1091500", steam, "Cyberpunk 2077"),
        ("228980", steam, "Steamworks Shared"),
        ("2124490", lib2, None),  # manifest without the game folder: not installed
    ):
        name = f"appmanifest_{appid}.acf"
        (root / "steamapps" / name).write_bytes((FIXTURES / "steam" / name).read_bytes())
        if folder:
            (root / "steamapps" / "common" / folder).mkdir()
    icons = steam / "appcache" / "librarycache" / "1091500"
    icons.mkdir(parents=True)
    (icons / "header.jpg").write_bytes(b"x")
    (icons / ("6897c3848f3e0350d512f59d5bae174a1e3739f9.jpg")).write_bytes(b"x")
    return steam, lib2


def test_steam_libraries_and_games(tmp_path: Path) -> None:
    steam, lib2 = steam_tree(tmp_path)
    text = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8")
    assert parse_library_folders(text)[:2] == [steam, lib2]
    games = {g.title: g for g in SteamLibrary(steam).scan()}
    assert set(games) == {"Cyberpunk 2077", "Steamworks Common Redistributables"}
    cyberpunk = games["Cyberpunk 2077"]
    assert cyberpunk.exe is None  # bound on the first run (4.11)
    assert cyberpunk.install_dir == steam / "steamapps" / "common" / "Cyberpunk 2077"
    assert cyberpunk.icon_path is not None and cyberpunk.icon_path.name.startswith("6897")


def test_steam_old_library_format() -> None:
    text = '"LibraryFolders"\n{\n\t"TimeNextStatsReport"\t"1"\n\t"1"\t"D:\\Games"\n}\n'
    assert parse_library_folders(text) == [Path("D:/Games")]


def test_missing_launchers_are_skipped(tmp_path: Path) -> None:
    assert SteamLibrary(tmp_path / "nope").scan() == []
    assert EpicLibrary(tmp_path / "nope").scan() == []


def test_epic_manifests(tmp_path: Path) -> None:
    folder = tmp_path / "TheWitcher3"
    folder.mkdir()
    manifests = tmp_path / "Manifests"
    manifests.mkdir()
    for name in ("witcher.item", "dlc.item"):
        text = (FIXTURES / "epic" / name).read_text(encoding="utf-8")
        text = text.replace("INSTALL_DIR", str(folder).replace("\\", "\\\\"))
        (manifests / name.replace(".item", "-guid.item")).write_text(text, encoding="utf-8")
    (manifests / "broken.item").write_text("{", encoding="utf-8")
    (game,) = EpicLibrary(manifests).scan()
    assert (game.title, game.install_dir, game.exe) == (
        "The Witcher 3: Wild Hunt",
        folder,
        "witcher3.exe",
    )
    assert parse_manifest({"DisplayName": "x", "InstallLocation": "C:/x"}) is None


def test_gog_registry_values() -> None:
    entries = json.loads((FIXTURES / "gog" / "games.json").read_text(encoding="utf-8"))
    games = [parse_entry(e) for e in entries]
    assert games[1] is None  # dependsOn: DLC / extras
    disco = games[0]
    assert disco is not None
    assert (disco.title, disco.exe) == ("Disco Elysium", "disco.exe")
    assert disco.install_dir == Path("C:/GOG Games/Disco Elysium")
    old = games[2]
    assert old is not None and old.exe is None
