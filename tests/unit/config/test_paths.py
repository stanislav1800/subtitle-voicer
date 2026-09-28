import sys
from pathlib import Path

import pytest

from subvoicer.config.paths import AppPaths


def test_layout_under_root(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path)
    assert paths.settings_file == tmp_path / "settings.toml"
    assert paths.profiles_dir == tmp_path / "profiles"
    assert paths.logs_dir == tmp_path / "logs"
    assert paths.models_dir == tmp_path / "models"
    assert paths.captures_dir == tmp_path / "captures"
    assert paths.icons_dir == tmp_path / "icons"
    assert paths.ducking_state_file == tmp_path / "ducking_state.json"


@pytest.mark.skipif(sys.platform != "win32", reason="%APPDATA% есть только в Windows")
def test_default_is_roaming_appdata() -> None:
    root = AppPaths.default().root
    assert root.name == "SubtitleVoicer"
    assert root.parent.name.lower() == "roaming"
