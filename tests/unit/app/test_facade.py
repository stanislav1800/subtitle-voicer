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


def test_fixed_stress_applies_to_the_active_game_at_once(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from subvoicer.config.profile import Profile, PronunciationRule
    from subvoicer.core.events import GameActivated
    from subvoicer.core.models import WindowRef
    from tests.unit.config.profiles import profile_data

    facade = AppFacade(AppPaths(tmp_path))
    stored = facade.store.save(Profile.model_validate(profile_data()))
    preparer = SimpleNamespace(dictionary=None)
    facade._parts = SimpleNamespace(speech=SimpleNamespace(preparer=preparer))  # type: ignore[assignment]
    window = WindowRef(1, 2, "Game.exe", "Game", 1920, 1080)
    facade.bus.publish(GameActivated(str(stored.path), window, "16:9"))
    rule = PronunciationRule.model_validate({"from": "Арасак*", "to": "Арас+ак*"})
    facade.update_profile(stored.path, lambda p: p.model_copy(update={"pronunciation": [rule]}))
    assert preparer.dictionary is not None
    assert preparer.dictionary.apply("Мы против Арасаки.") == "Мы против Арас+аки."  # type: ignore[attr-defined]
    facade._parts = None
