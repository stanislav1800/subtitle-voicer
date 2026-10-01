from pathlib import Path

from subvoicer.app.facade import AppFacade, tuning
from subvoicer.config.paths import AppPaths
from subvoicer.config.settings import load_settings
from subvoicer.core.events import SettingsApplied
from subvoicer.pipeline.speech.scheduler import OverlapMode


def test_apply_settings_saves_and_announces(tmp_path: Path) -> None:
    facade = AppFacade(AppPaths(tmp_path))
    seen: list[object] = []
    facade.subscribe(SettingsApplied, seen.append)
    new = facade.settings.model_copy(update={"base_rate": 1.3, "history_size": 2})
    facade.apply_settings(new)
    assert load_settings(facade.paths.settings_file).base_rate == 1.3
    assert facade.history.size == 2 and len(seen) == 1
    facade.toggle_enabled()
    assert facade.settings.enabled is False
    assert load_settings(facade.paths.settings_file).enabled is False


def test_tuning_from_settings(tmp_path: Path) -> None:
    settings = AppFacade(AppPaths(tmp_path)).settings.model_copy(
        update={"overlap_mode": "queue", "duck_fade_ms": 250}
    )
    t = tuning(settings)
    assert t.overlap is OverlapMode.QUEUE and t.duck_fade_s == 0.25


def test_status_without_a_game(tmp_path: Path) -> None:
    facade = AppFacade(AppPaths(tmp_path))
    assert not facade.ready and facade.status().title is None
    facade.say("Привет", "v1")  # not started: ignored, no crash
