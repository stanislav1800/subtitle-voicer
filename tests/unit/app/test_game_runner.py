"""Profile -> zones and utterance -> speech request (stage 5), on fakes."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from subvoicer.app.game_runner import GameRunner, ProfileRouter, zone_specs
from subvoicer.config.profile import Layout, Profile
from subvoicer.config.profile_store import ProfileStore
from subvoicer.config.resources import gender_exceptions, letter_names, translit_table
from subvoicer.core.events import GameActivated, GameClosed
from subvoicer.core.models import Gender, Priority, Utterance, UtteranceKind, Voice, WindowRef
from subvoicer.pipeline.speaker.characters import CharacterBook
from subvoicer.pipeline.speaker.gender import GenderGuesser
from subvoicer.pipeline.speaker.resolver import SpeakerMode
from subvoicer.pipeline.speech.cache import SpeechCache
from subvoicer.pipeline.speech.scheduler import SpeechScheduler
from subvoicer.pipeline.text.latin import LatinReader
from subvoicer.pipeline.text.preparer import TextPreparer
from subvoicer.services.speech_service import SpeechService
from tests.fakes.audio import FakeOutput, FakeTts
from tests.fakes.capture import RecordingBus
from tests.fakes.clock import FakeClock
from tests.fakes.stress import FirstVowelStress
from tests.unit.config.profiles import dialog_zone, profile_data

VOICES = [
    Voice("m1", Gender.MALE, "m1"),
    Voice("m2", Gender.MALE, "m2"),
    Voice("f1", Gender.FEMALE, "f1"),
    Voice("f2", Gender.FEMALE, "f2"),
]
WINDOW = WindowRef(10, 100, "Game.exe", "Game", 1920, 1080)


def objective_zone() -> dict[str, Any]:
    return {
        "id": "objective",
        "kind": "text",
        "rect": [0.78, 0.18, 0.98, 0.26],
        "trigger": "auto",
        "policy": {
            "priority": "low",
            "voice": {"mode": "narrator"},
            "repeat_cooldown_min": 10,
            "duck": False,
        },
        "mask": {"preset": "bright_on_dark"},
    }


def make_profile(**fields: Any) -> Profile:
    zones = [dialog_zone(voice={"mode": "speaker"}), objective_zone()]
    return Profile.model_validate(profile_data(layouts={"16:9": {"zones": zones}}, **fields))


def line(
    text: str, channel: str = "dialog", speaker: str | None = None, *, cont: bool = False
) -> Utterance:
    kind = UtteranceKind.CONTINUATION if cont else UtteranceKind.NEW
    return Utterance(channel, kind, text, text, speaker, 1.0, 1.0, id=1)


class Rig:
    def __init__(self, tmp_path: Path, **fields: Any) -> None:
        self.store = ProfileStore(tmp_path / "profiles")
        self.path = self.store.save(make_profile(**fields)).path
        gender = GenderGuesser(gender_exceptions())
        self.router = ProfileRouter(self.store, CharacterBook(VOICES, gender), gender)
        self.router.set_profile(self.path, self.store.load(self.path), "16:9")

    def saved(self) -> Profile:
        return self.store.load(self.path)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_policy_of_the_zone(rig: Rig) -> None:
    dialog = rig.router.request(line("Привет."))
    objective = rig.router.request(line("Найдите Джуди.", "objective"))
    assert dialog is not None and objective is not None
    assert (dialog.priority, dialog.duck) == (Priority.HIGH, True)
    assert (objective.priority, objective.duck, objective.voice_id) == (Priority.LOW, False, "n")
    assert rig.router.request(line("x", "unknown")) is None


def test_unnamed_line_uses_gender_defaults(rig: Rig) -> None:
    assert rig.router.request(line("Я пошла.")).voice_id == "f"  # type: ignore[union-attr]
    assert rig.router.request(line("Я пошёл.")).voice_id == "m"  # type: ignore[union-attr]
    assert rig.router.request(line("Поехали.")).voice_id == "n"  # type: ignore[union-attr]


def test_named_speaker_becomes_a_saved_character(rig: Rig) -> None:
    first = rig.router.request(line("Я сама разберусь.", speaker="Панам"))
    assert first is not None and first.voice_id in {"f1", "f2"}
    (panam,) = rig.saved().characters
    assert (panam.name, panam.gender, panam.voice) == ("Панам", Gender.FEMALE, first.voice_id)
    again = rig.router.request(line("Поехали.", speaker="Панан"))  # OCR error
    assert again is not None and again.voice_id == first.voice_id
    assert len(rig.saved().characters) == 1


def test_voice_survives_a_restart(rig: Rig, tmp_path: Path) -> None:
    first = rig.router.request(line("Ну что, идём?", speaker="Ривер"))
    other = Rig.__new__(Rig)
    other.store, other.path = rig.store, rig.path
    gender = GenderGuesser(gender_exceptions())
    other.router = ProfileRouter(rig.store, CharacterBook(VOICES, gender), gender)
    other.router.set_profile(rig.path, rig.store.load(rig.path), "16:9")
    second = other.router.request(line("Пошли.", speaker="Ривер"))
    assert first is not None and second is not None and first.voice_id == second.voice_id


def test_hand_edited_voice_is_not_overwritten(rig: Rig) -> None:
    rig.router.request(line("Привет.", speaker="Керри"))
    data = json.loads(rig.path.read_text(encoding="utf-8"))
    data["characters"][0].update(voice="m2", voice_locked=True)
    rig.path.write_text(json.dumps(data), encoding="utf-8")
    # a new character is saved while the router still holds the old copy in memory
    rig.router.request(line("Я пошла.", speaker="Джуди"))
    kerry = next(c for c in rig.saved().characters if c.name == "Керри")
    assert kerry.voice == "m2" and kerry.voice_locked
    rig.router.set_profile(rig.path, rig.saved(), "16:9")  # GameWatcher reloads on the edit
    assert rig.router.request(line("Я пошёл.", speaker="Керри")).voice_id == "m2"  # type: ignore[union-attr]


def test_continuation_keeps_the_voice(rig: Rig) -> None:
    first = rig.router.request(line("Я пошла.", speaker="Джуди"))
    tail = rig.router.request(line("и всё", cont=True))
    assert first is not None and tail is not None and tail.voice_id == first.voice_id


def test_zone_specs_skip_hotkey_and_off(tmp_path: Path) -> None:
    hotkey = dialog_zone()
    hotkey.update(id="note", trigger="hotkey", hotkey="<ctrl>+<alt>+1")
    off = objective_zone()
    off.update(id="off", trigger="off")
    speaker = {
        "id": "name",
        "kind": "speaker",
        "rect": [0, 0, 0.3, 0.1],
        "mask": {"preset": "bright_on_dark"},
    }
    named = dialog_zone(speaker={"mode": "zone", "zone_id": "name"})
    profile = Profile.model_validate(
        profile_data(layouts={"16:9": {"zones": [named, hotkey, off, speaker]}})
    )

    class Engines:
        def get(self, _engine: str) -> object:
            return object()

    specs = zone_specs(profile, "16:9", Engines())  # type: ignore[arg-type]
    assert [s.id for s in specs] == ["dialog"]
    assert specs[0].speaker.mode is SpeakerMode.ZONE and specs[0].name is not None
    assert zone_specs(profile, "4:3", Engines()) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------- switching games


class FakeSession:
    def __init__(self) -> None:
        self.started = self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class FakeSpeech:
    def __init__(self) -> None:
        self.games: list[int | None] = []

    def set_game(self, pid: int | None) -> None:
        self.games.append(pid)


def test_runner_rebuilds_only_when_zones_change(rig: Rig) -> None:
    built: list[FakeSession] = []

    def build(*_args: object) -> FakeSession:
        built.append(FakeSession())
        return built[-1]

    preparer = TextPreparer(FirstVowelStress(), LatinReader(letter_names(), translit_table()))
    speech = FakeSpeech()
    runner = GameRunner(rig.store, rig.router, speech, preparer, build)  # type: ignore[arg-type]
    event = GameActivated(str(rig.path), WINDOW, "16:9")
    runner.on_activated(event)
    runner.on_activated(event)  # e.g. a character was saved: same zones
    assert len(built) == 1 and built[0].started
    profile = rig.saved()
    zones = [*profile.layouts["16:9"].zones[:1]]
    rig.store.save(profile.model_copy(update={"layouts": {"16:9": Layout(zones=zones)}}), rig.path)
    runner.on_activated(event)
    assert len(built) == 2 and built[0].stopped
    runner.on_activated(GameActivated(str(rig.path), WINDOW, "4:3"))  # no layout
    assert len(built) == 2 and built[1].stopped
    runner.on_closed(GameClosed(WINDOW.pid))
    assert speech.games[-1] is None and rig.router.request(line("Привет.")) is None


# ---------------------------------------------------------------- two zones, two priorities


def test_objective_waits_while_the_dialog_speaks(rig: Rig) -> None:
    out = FakeOutput()
    clock = FakeClock(100.0)
    service = SpeechService(
        SpeechScheduler(),
        TextPreparer(FirstVowelStress(), LatinReader(letter_names(), translit_table())),
        FakeTts(),
        out,
        SpeechCache(10),
        clock,
        RecordingBus(),
    )
    dialog = rig.router.request(line("Держись рядом."))
    objective = rig.router.request(line("Следуйте за Джуди.", "objective"))
    assert dialog is not None and objective is not None
    objective = replace(objective, id=2)
    service.submit(dialog)
    service.step()
    service.submit(objective)
    clock.advance(0.5)
    service.step()
    assert out.log == [("start", 1)]  # the objective waits, nothing is cut
    out.line_ends(1)
    clock.advance(0.1)
    service.step()
    assert out.log == [("start", 1), ("start", 2)]
