import sys

import pytest
from PySide6.QtWidgets import QApplication

from subvoicer.config.profile import Character, Profile, Zone
from subvoicer.core.models import Gender, Voice
from subvoicer.ui.settings.dialogs import CharacterDialog, gender_text, voice_text
from subvoicer.ui.settings.profiles import (
    assigned_chip,
    initials,
    profile_status,
    zone_behaviour,
)
from tests.unit.config.profiles import dialog_zone, profile_data

VOICES = [Voice("m1", Gender.MALE, "Bogdan"), Voice("f1", Gender.FEMALE, "Aigul")]


@pytest.fixture(scope="module", autouse=True)
def qt() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    assert isinstance(app, QApplication)
    return app


def test_texts() -> None:
    assert initials("Cyberpunk 2077") == "C2"
    assert initials("firefox.exe") == "FI"
    profile = Profile.model_validate(profile_data())
    assert profile_status(profile) == "Настроено · зон: 1"
    empty = Profile.model_validate(profile_data(layout={"zones": []}))
    assert profile_status(empty) == "Не настроено"
    objective = Zone.model_validate(
        {**dialog_zone(priority="low", repeat_cooldown_min=10, duck=False), "id": "o"}
    )
    assert (
        zone_behaviour(objective)
        == "Авто · низкий приоритет · не повторять 10 мин · без приглушения"
    )
    assert voice_text("f1", VOICES) == "Aigul · жен." and voice_text(None, VOICES) == "—"
    assert gender_text(Gender.UNKNOWN) == "—"


def test_assigned_chip() -> None:
    assert assigned_chip(Character(name="А", voice="m1")).text() == "уточнить пол"
    assert assigned_chip(Character(name="А", voice="m1", gender=Gender.MALE)).text() == "авто"
    assert assigned_chip(Character(name="А", source="import")).text() == "из списка"
    assert assigned_chip(Character(name="А", voice_locked=True)).text() == "вручную"


def test_character_dialog_locks_what_was_changed() -> None:
    old = Character(name="Керри", gender=Gender.UNKNOWN, voice="m1")
    dialog = CharacterDialog(None, VOICES, lambda _v, _n: None, old)
    dialog._voice.setCurrentIndex(dialog._voice.findData("f1"))
    result = dialog.result_character()
    assert result is not None
    assert result.voice == "f1" and result.voice_locked and not result.gender_locked
    new = CharacterDialog(None, VOICES, lambda _v, _n: None)
    new._name.setText("Ривер")
    new._gender.setCurrentIndex(new._gender.findData("male"))
    created = new.result_character()
    assert created is not None and created.source == "manual" and created.gender_locked
    assert created.voice is None and not created.voice_locked


def test_a_change_keeps_the_page_where_it_was(qt: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Choosing in a drop-down redraws the profile: the page jumped to the bottom after the
    focus, which went to the last button when the changed field went away."""
    from PySide6.QtWidgets import QComboBox

    from subvoicer.app.facade import AppFacade
    from subvoicer.config.paths import AppPaths
    from subvoicer.ui.settings.profiles import ProfilesPage

    facade = AppFacade(AppPaths(tmp_path))
    facade.store.save(Profile.model_validate(profile_data()))
    page = ProfilesPage(facade, lambda *_: None)
    page.resize(900, 400)
    page.show()
    page.refresh()
    for _ in range(5):
        qt.processEvents()
    bar = page.verticalScrollBar()
    assert bar.maximum() > 100
    bar.setValue(100)
    box = next(b for b in page.findChildren(QComboBox) if b.accessibleName() == "Рассказчик")
    box.setFocus()
    box.setCurrentIndex((box.currentIndex() + 1) % box.count())
    for _ in range(10):
        qt.processEvents()
    assert bar.value() == 100
    page.close()
