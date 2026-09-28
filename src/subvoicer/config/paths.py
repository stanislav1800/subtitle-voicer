"""Application data locations: %APPDATA%/SubtitleVoicer/ (TZ 4.14)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

APP_DIR_NAME = "SubtitleVoicer"


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path

    @classmethod
    def default(cls) -> AppPaths:
        # roaming=True -> %APPDATA% (Roaming) on Windows
        return cls(user_data_path(APP_DIR_NAME, appauthor=False, roaming=True))

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.toml"

    @property
    def profiles_dir(self) -> Path:
        return self.root / "profiles"

    @property
    def icons_dir(self) -> Path:
        return self.root / "icons"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def captures_dir(self) -> Path:
        return self.root / "captures"

    @property
    def ducking_state_file(self) -> Path:
        return self.root / "ducking_state.json"
