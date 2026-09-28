"""Mask change / emptiness, including the stage 1 criterion: a moving background alone
must not count as a change."""

from itertools import pairwise

import numpy as np
import pytest

from subvoicer.pipeline.vision.change import (
    changed_ratio,
    filled_ratio,
    mask_changed,
    mask_empty,
)
from subvoicer.pipeline.vision.masks import (
    MaskPreset,
    MaskSpec,
    bgr_to_hsv,
    build_mask,
    hsv_ranges_around,
)
from tests.fakes.images import ZONE_W, dark_noise, draw_text, motley

WHITE = (255, 255, 255)
BRIGHT = MaskSpec(MaskPreset.BRIGHT_ON_DARK)
WHITE_COLOR = MaskSpec(MaskPreset.COLOR, colors_hsv=tuple(hsv_ranges_around(bgr_to_hsv(WHITE))))


def test_empty_zone_gives_empty_mask(rng: np.random.Generator) -> None:
    background = dark_noise(rng)[:, :ZONE_W]
    mask = build_mask(background, BRIGHT)
    assert mask_empty(mask)


def test_zone_with_text_is_not_empty(rng: np.random.Generator) -> None:
    image = draw_text(dark_noise(rng)[:, :ZONE_W], "SUBTITLE", WHITE)
    assert not mask_empty(build_mask(image, BRIGHT))


def test_empty_threshold() -> None:
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[0, :9] = 255  # 0.09% < 0.1%
    assert mask_empty(mask)
    mask[0, 9] = 255  # 0.1%
    assert not mask_empty(mask)
    assert filled_ratio(mask) == pytest.approx(0.001)


@pytest.mark.parametrize(
    ("make_background", "spec"),
    [(dark_noise, BRIGHT), (motley, WHITE_COLOR), (motley, BRIGHT)],
)
def test_moving_background_with_same_text_is_not_a_change(
    rng: np.random.Generator, make_background, spec: MaskSpec
) -> None:
    wide = make_background(rng)
    masks = []
    for shift in range(0, 100, 10):  # the scene pans under a still subtitle
        frame = draw_text(np.ascontiguousarray(wide[:, shift : shift + ZONE_W]), "SUBTITLE", WHITE)
        masks.append(build_mask(frame, spec))
    for previous, current in pairwise(masks):
        assert not mask_changed(previous, current), changed_ratio(previous, current)


def test_new_text_is_a_change(rng: np.random.Generator) -> None:
    background = dark_noise(rng)[:, :ZONE_W]
    first = build_mask(draw_text(background, "SUBTITLE", WHITE), BRIGHT)
    second = build_mask(draw_text(background, "ANOTHER ONE", WHITE), BRIGHT)
    assert mask_changed(first, second)


def test_size_change_is_a_change() -> None:
    assert mask_changed(np.zeros((10, 10), np.uint8), np.zeros((10, 12), np.uint8))


def test_change_threshold() -> None:
    a = np.zeros((100, 100), dtype=np.uint8)
    b = a.copy()
    b[0, :50] = 255  # 0.5% — not more than change_ratio
    assert not mask_changed(a, b)
    b[0, 50] = 255
    assert mask_changed(a, b)
