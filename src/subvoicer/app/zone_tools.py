"""What the zone editor needs from below the UI: a frame of the game, a screenshot, colours
picked with the eyedropper, and a live preview of one zone (TZ 4.13)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from subvoicer.app.game_runner import OcrEngines, zone_spec
from subvoicer.config.profile import Profile, Zone
from subvoicer.core.models import Gender, Voice
from subvoicer.engines import registry
from subvoicer.pipeline.speaker.characters import find_character
from subvoicer.pipeline.speaker.gender import GenderGuesser
from subvoicer.pipeline.vision.masks import bgr_to_hsv, hsv_ranges_around
from subvoicer.sources.zone_ocr import read_once

log = logging.getLogger(__name__)

Image = npt.NDArray[np.uint8]
HsvRange = tuple[tuple[int, int, int], tuple[int, int, int]]
FRAME_TIMEOUT_S = 2.0


def grab_window(hwnd: int, timeout_s: float = FRAME_TIMEOUT_S) -> Image | None:
    """One frame of a window through Windows Graphics Capture (the editor «поверх игры»)."""
    source = registry.resolve("capture", "wgc")()
    try:
        source.start(hwnd)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            frame = source.latest()
            if frame is not None:
                return np.ascontiguousarray(frame.image[:, :, :3])
            time.sleep(0.05)
    except Exception:
        log.exception("Кадр окна %s не получен", hwnd)
    finally:
        source.stop()
    return None


def load_image(path: Path) -> Image | None:
    """A screenshot from any path (cv2.imread fails on non-ASCII paths on Windows)."""
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return None if image is None else np.ascontiguousarray(image)


def save_image(path: Path, image: Image) -> None:
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if ok:
        encoded.tofile(str(path))


def colors_around(image: Image, x: int, y: int) -> list[HsvRange]:
    """The eyedropper: the mean colour of a 3x3 spot -> HSV ranges ±(10, 80, 80) (TZ 1.9)."""
    height, width = image.shape[:2]
    spot = image[max(0, y - 1) : min(height, y + 2), max(0, x - 1) : min(width, x + 2), :3]
    b, g, r = (int(v) for v in spot.reshape(-1, 3).mean(axis=0))
    return hsv_ranges_around(bgr_to_hsv((b, g, r)))


@dataclass(frozen=True)
class ZonePreview:
    mask: Image | None
    text: str = ""
    confidence: float = 0.0
    elapsed_ms: float = 0.0
    speaker: str | None = None  # the first speaker of the text
    voice: str | None = None  # the voice the line would get
    voice_reason: str = ""
    error: str = ""


def preview_zone(
    frame: Image,
    zone: Zone,
    zones: list[Zone],
    profile: Profile,
    engines: OcrEngines,
    voices: list[Voice],
    gender: GenderGuesser,
) -> ZonePreview:
    """Mask, OCR text and who would speak it with which voice — without saving anything."""
    if zone.kind != "text" or zone.policy is None:
        return ZonePreview(None, error="Зона имени читается вместе со своей текстовой зоной")
    try:
        spec = zone_spec(profile, zone, zones, engines)
        reading = read_once(spec, frame, spec.runner)  # type: ignore[arg-type]
    except Exception as exc:
        log.exception("Предпросмотр зоны %s не удался", zone.id)
        return ZonePreview(None, error=str(exc))
    first = reading.lines[0] if reading.lines else None
    speaker = first.name if first else None
    text = " ".join(line.text for line in reading.lines) if reading.lines else reading.text
    voice, reason = _voice_for(zone, profile, speaker, text, gender)
    return ZonePreview(
        reading.mask,
        text,
        reading.confidence,
        reading.elapsed_ms,
        speaker,
        voice,
        reason,
    )


def _voice_for(
    zone: Zone, profile: Profile, speaker: str | None, text: str, gender: GenderGuesser
) -> tuple[str | None, str]:
    policy = zone.policy
    assert policy is not None
    if policy.voice.mode == "fixed":
        return policy.voice.id, "голос зоны"
    if policy.voice.mode == "narrator":
        return profile.narrator_voice, "рассказчик"
    if speaker:
        index = find_character(profile.characters, speaker)
        if index is not None:
            character = profile.characters[index]
            if character.voice:
                return character.voice, f"персонаж {character.name}"
        return None, "новый персонаж: голос назначится при первой реплике"
    guessed = gender.guess(text)
    if guessed is Gender.MALE:
        return profile.default_voices.male, "мужской по тексту"
    if guessed is Gender.FEMALE:
        return profile.default_voices.female, "женский по тексту"
    return profile.narrator_voice, "рассказчик (пол не определён)"


def as_rgb_bytes(image: Image) -> tuple[bytes, int, int, int]:
    """For QImage: contiguous RGB bytes, width, height, bytes per line."""
    rgb = (
        cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        if image.ndim == 3
        else cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    )
    rgb = np.ascontiguousarray(rgb)
    height, width = rgb.shape[:2]
    return rgb.tobytes(), width, height, width * 3
