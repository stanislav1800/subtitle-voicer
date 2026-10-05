"""library engine family: engine id -> 'module:Class' (see engines/registry.py)."""

LIBRARY: dict[str, str] = {
    "steam": "subvoicer.engines.library.steam:SteamLibrary",
    "epic": "subvoicer.engines.library.epic:EpicLibrary",
    "gog": "subvoicer.engines.library.gog:GogLibrary",
}
