"""«Выделить и прочитать»: where the frozen frame is drawn and how a selection maps to it."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRectF

from subvoicer.ui.select_area import frame_target, to_fractions


def test_frame_goes_exactly_over_the_game_image() -> None:
    # windowed game at (100, 50) on a monitor at (1920, 0), scaling 150%
    target = frame_target(
        QRectF(0, 0, 1280, 720), (1280, 720), (2020, 50, 3300, 770), (1920, 0, 3840, 1080), 1.5
    )
    assert (target.x(), target.y()) == pytest.approx((100 / 1.5, 50 / 1.5))
    assert (target.width(), target.height()) == pytest.approx((1280 / 1.5, 720 / 1.5))


def test_frame_is_fitted_when_the_window_is_unknown() -> None:
    target = frame_target(QRectF(0, 0, 1000, 1000), (1920, 1080), None, None, 1.0)
    assert target.width() == pytest.approx(1000) and target.height() == pytest.approx(562.5)
    assert target.y() == pytest.approx((1000 - 562.5) / 2)


def test_selection_to_fractions_is_clipped_to_the_frame() -> None:
    target = QRectF(100, 100, 400, 200)
    assert to_fractions(QRectF(200, 150, 200, 100), target) == pytest.approx(
        (0.25, 0.25, 0.75, 0.75)
    )
    # dragged from bottom right to top left and past the frame edge
    backwards = QRectF(600, 400, -400, -250)  # x 200..600, y 150..400
    assert to_fractions(backwards, target) == pytest.approx((0.25, 0.25, 1.0, 1.0))
