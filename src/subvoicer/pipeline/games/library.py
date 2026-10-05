"""Which found games become new profiles (TZ 4.16): not service entries, not hidden by the
user, not already in the list. Pure: paths are compared as strings."""

from __future__ import annotations

import fnmatch
import ntpath
from collections.abc import Iterable, Sequence

from subvoicer.core.models import FoundGame


def folder_key(path: str) -> str:
    """Windows paths compare without case and trailing separators."""
    return ntpath.normcase(ntpath.normpath(path)).rstrip("\\")


def is_ignored(title: str, patterns: Iterable[str]) -> bool:
    """``library_ignore.txt``: a whole title, ``*`` for any start or end, no case."""
    name = title.casefold().strip()
    return any(fnmatch.fnmatchcase(name, p.casefold()) for p in patterns)


def new_games(
    found: Sequence[FoundGame],
    known_dirs: Iterable[str],
    known_exes: Iterable[str],
    hidden_dirs: Iterable[str],
    ignore: Sequence[str],
) -> list[FoundGame]:
    """Games to add, each install folder once (two sources may report the same game)."""
    taken = {folder_key(d) for d in known_dirs} | {folder_key(d) for d in hidden_dirs}
    exes = {e.casefold() for e in known_exes}
    result: list[FoundGame] = []
    for game in found:
        key = folder_key(str(game.install_dir))
        if key in taken or is_ignored(game.title, ignore):
            continue
        if game.exe is not None and game.exe.casefold() in exes:
            continue  # a profile made from the running game, without install_dir
        taken.add(key)
        result.append(game)
    return result
