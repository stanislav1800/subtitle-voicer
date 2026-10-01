"""Profile files (TZ 4.11): names, atomic writes, backups, migrations, matching a process.

``profiles/<exe>.json``; when several profiles share an exe, the others are
``profiles/<exe>--<sha1(install_dir)[:8]>.json``. Before every save the current file is copied
to ``profiles/backup/<name>.<date-time>.json`` (5 kept per profile); before a migrated
profile is written, the old file is kept as ``profiles/backup/<name>.v<N>.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath

from pydantic import ValidationError

from subvoicer.config.migrations import (
    MIGRATIONS,
    Migration,
    ProfileFormatError,
    migrate,
    schema_version,
)
from subvoicer.config.profile import SCHEMA_VERSION, Profile

log = logging.getLogger(__name__)

BACKUPS_KEPT = 5


@dataclass(frozen=True, slots=True)
class StoredProfile:
    path: Path
    profile: Profile

    @property
    def name(self) -> str:
        return self.path.stem


def same_exe(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.lower() == b.lower()


def is_inside(path: str, folder: str) -> bool:
    """Windows paths, case-insensitive: ``path`` lies in ``folder`` or its subfolders."""
    p, f = PureWindowsPath(path.lower()), PureWindowsPath(folder.lower())
    return p == f or f in p.parents


def dir_hash(install_dir: str | None) -> str:
    key = (install_dir or "").lower().rstrip("\\/")
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def format_error(error: Exception) -> str:
    """A readable one-liner per problem instead of pydantic's wall of text."""
    if isinstance(error, ValidationError):
        parts = []
        for item in error.errors():
            where = ".".join(str(p) for p in item["loc"]) or "профиль"
            parts.append(f"{where}: {item['msg']}")
        return "; ".join(parts)
    return str(error)


class ProfileStore:
    def __init__(
        self,
        directory: Path,
        migrations: Mapping[int, Migration] = MIGRATIONS,
        current_version: int = SCHEMA_VERSION,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.directory = directory
        self.backup_dir = directory / "backup"
        self._migrations = migrations
        self._current = current_version
        self._clock = clock

    # ------------------------------------------------------------------ reading

    def load(self, path: Path) -> Profile:
        """Read, migrate (backing up the old file) and validate one profile file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ProfileFormatError("Профиль должен быть JSON-объектом")
        version = schema_version(data)
        migrated = migrate(data, self._current, self._migrations)
        profile = Profile.model_validate(migrated)
        if version < self._current:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, self.backup_dir / f"{path.stem}.v{version}.json")
            self._write(path, profile)
            log.info("Профиль %s обновлён с формата %d до %d", path.name, version, self._current)
        return profile

    def load_all(self) -> list[StoredProfile]:
        """Every readable profile; broken and too-new files are reported and skipped."""
        found = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                found.append(StoredProfile(path, self.load(path)))
            except (OSError, ValueError) as exc:  # ValidationError and JSON errors too
                log.error("Профиль %s не загружен: %s", path.name, format_error(exc))
        return found

    # ------------------------------------------------------------------ matching (4.11)

    def match(
        self, profiles: list[StoredProfile], exe: str, exe_path: str | None
    ) -> StoredProfile | None:
        """Rule 1: a profile of this exe whose install_dir holds the exe, else the one without
        install_dir."""
        same = [p for p in profiles if same_exe(p.profile.exe, exe)]
        if exe_path:
            for stored in same:
                folder = stored.profile.install_dir
                if folder and is_inside(exe_path, folder):
                    return stored
        return next((p for p in same if not p.profile.install_dir), None)

    def bindable(self, profiles: list[StoredProfile], exe_path: str | None) -> StoredProfile | None:
        """Rule 2: a profile without exe whose install_dir holds the exe (the caller checks
        that the window covers the monitor before binding)."""
        if not exe_path:
            return None
        for stored in profiles:
            folder = stored.profile.install_dir
            if stored.profile.exe is None and folder and is_inside(exe_path, folder):
                return stored
        return None

    def bind(self, stored: StoredProfile, exe: str) -> StoredProfile:
        profile = stored.profile.model_copy(update={"exe": exe})
        stored.path.unlink(missing_ok=True)
        return self.save(profile)

    # ------------------------------------------------------------------ writing

    def path_for(self, profile: Profile, existing: list[StoredProfile] | None = None) -> Path:
        """Where ``profile`` lives: its current file if it has one, else a free name."""
        existing = self.load_all() if existing is None else existing
        for stored in existing:
            if self._same_identity(stored.profile, profile):
                return stored.path
        if profile.exe is None:
            return self.directory / f"game--{dir_hash(profile.install_dir)}.json"
        plain = self.directory / f"{profile.exe.lower()}.json"
        if not plain.exists():
            return plain
        return self.directory / f"{profile.exe.lower()}--{dir_hash(profile.install_dir)}.json"

    def save(self, profile: Profile, path: Path | None = None) -> StoredProfile:
        path = path or self.path_for(profile)
        if path.exists():
            self._backup(path)
        self._write(path, profile)
        return StoredProfile(path, profile)

    def update(self, path: Path, change: Callable[[Profile], Profile | None]) -> StoredProfile:
        """Re-read the file (it may have been edited by hand), apply ``change``, save if it
        returned a new profile. Hand edits are never overwritten by a stale copy."""
        current = self.load(path)
        changed = change(current)
        if changed is None:
            return StoredProfile(path, current)
        return self.save(changed, path)

    def export(self, path: Path, target: Path) -> None:
        profile = self.load(path)
        self._write(target, profile)

    def import_file(self, source: Path) -> StoredProfile:
        """A profile from one JSON file: migrated, validated, saved under its own name
        (replacing the profile with the same identity, which goes to backup)."""
        data = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ProfileFormatError("Профиль должен быть JSON-объектом")
        profile = Profile.model_validate(migrate(data, self._current, self._migrations))
        return self.save(profile)

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _same_identity(a: Profile, b: Profile) -> bool:
        if (a.exe is None) != (b.exe is None):
            return False
        if a.exe is not None and not same_exe(a.exe, b.exe):
            return False
        return dir_hash(a.install_dir) == dir_hash(b.install_dir)

    def _backup(self, path: Path) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = self._clock().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, self.backup_dir / f"{path.stem}.{stamp}.json")
        dated = sorted(
            p
            for p in self.backup_dir.glob(f"{path.stem}.*.json")
            if not p.stem.removeprefix(f"{path.stem}.").startswith("v")
        )
        for old in dated[:-BACKUPS_KEPT]:
            old.unlink(missing_ok=True)

    @staticmethod
    def _write(path: Path, profile: Profile) -> None:
        """Atomic: a temporary file next to the target, then a replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(profile.to_json(), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
