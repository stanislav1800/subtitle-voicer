"""Text and speaker by colour on the user's real frames (TZ 4.3, 4.5, 1.37).

42 zone crops of a Cyberpunk 2077 letsplay, light and dark scenes, two with text of other
colours (a yellow answer option, a yellow «15M»), one without a subtitle. Labels and the
zone's colours: ``tests/fixtures/real/cyberpunk/colour.json``; the crops (~18 MB) live only on
the user's machine in ``tests/fixtures/colour/cyberpunk/`` and the OCR models in the app's
``models/`` — without them the test is skipped. Measured 2026-10-23 (DECISIONS): text 0.956,
no read worse than 0.8, no empty read, 38 of 41 names; before the fix 0.827, 7, 5 and 30.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest
from rapidfuzz.distance import Levenshtein

from subvoicer.app import game_runner
from subvoicer.app.models_setup import engine_options
from subvoicer.config.paths import AppPaths
from subvoicer.config.profile import Character
from subvoicer.config.resources import speaker_stoplist
from subvoicer.core.models import Rect
from subvoicer.pipeline.normalize import normalize
from subvoicer.pipeline.ocr.runner import OcrRunner
from subvoicer.pipeline.speaker.characters import find_character
from subvoicer.pipeline.speaker.resolver import SpeakerMode, SpeakerResolver, clean_name
from subvoicer.pipeline.vision.masks import MaskPreset, MaskSpec
from subvoicer.sources.zone_ocr import NameSource, ZoneSpec, read_once

ROOT = Path(__file__).resolve().parents[1] / "fixtures"
CROPS = ROOT / "colour" / "cyberpunk"
LABELS = ROOT / "real" / "cyberpunk" / "colour.json"


def ranges(raw: list) -> tuple:  # type: ignore[type-arg]
    return tuple((tuple(low), tuple(high)) for low, high in raw)


@pytest.fixture(scope="module")
def spec() -> ZoneSpec:
    if not CROPS.is_dir():
        pytest.skip(f"нет кропов {CROPS} (только у пользователя)")
    options, _ = engine_options(AppPaths.default())
    engines = game_runner.OcrEngines({"rapidocr": options}, None)
    try:
        engine = engines.get("rapidocr")
    except Exception as exc:
        pytest.skip(f"нет моделей OCR: {exc}")
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    return ZoneSpec(
        id="dialog",
        rect=Rect(0.0, 0.0, 1.0, 1.0),
        mask=MaskSpec(MaskPreset.COLOR, colors_hsv=ranges(labels["text_hsv"])),
        speaker=SpeakerResolver(SpeakerMode.COLOR, speaker_stoplist()),
        name=NameSource(MaskSpec(MaskPreset.COLOR, colors_hsv=ranges(labels["name_hsv"]))),
        runner=OcrRunner(engine),
    )


def accuracy(read: str, truth: str) -> float:
    a, b = normalize(read), normalize(truth)
    if not b:
        return 1.0 if not a else 0.0
    return max(0.0, 1 - Levenshtein.distance(a, b) / len(b))


def test_text_and_speaker_by_colour_on_light_and_dark_frames(spec: ZoneSpec) -> None:
    frames = json.loads(LABELS.read_text(encoding="utf-8"))["frames"]
    scores, names, empty, report = [], 0, 0, []
    for name, (speaker, truth) in frames.items():
        image = cv2.cvtColor(cv2.imread(str(CROPS / f"{name}.png")), cv2.COLOR_BGR2BGRA)
        reading = read_once(spec, image, spec.runner)  # type: ignore[arg-type]
        text = " ".join(line.text for line in reading.lines)
        said = reading.lines[0].name if reading.lines else None
        score = accuracy(text, truth)
        if not truth:
            assert not normalize(text), f"{name}: субтитра нет, прочитано {text!r}"
            continue
        scores.append(score)
        empty += not normalize(text)
        found = said and find_character([Character(name=speaker)], clean_name(said)) == 0
        names += bool(found)
        if score < 0.8 or not found:
            report.append(f"{name}: {score:.2f} {said!r} {text[:60]!r}")
    mean = sum(scores) / len(scores)
    assert empty == 0, report
    assert mean >= 0.93 and sum(s < 0.8 for s in scores) <= 1, (mean, report)
    assert names >= 36, (names, report)
