"""Mask change and emptiness (TZ 4.2.1, steps 3-4). Compares masks, never raw pixels."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

Mask = npt.NDArray[np.uint8]

DEFAULT_CHANGE_RATIO = 0.005
DEFAULT_EMPTY_RATIO = 0.001


def changed_ratio(previous: Mask, current: Mask) -> float:
    """Share of zone pixels whose mask value differs; 1.0 if the zone size changed."""
    if previous.shape != current.shape:
        return 1.0
    return float(np.count_nonzero(previous != current)) / current.size


def mask_changed(previous: Mask, current: Mask, change_ratio: float = DEFAULT_CHANGE_RATIO) -> bool:
    return changed_ratio(previous, current) > change_ratio


def filled_ratio(mask: Mask) -> float:
    return float(np.count_nonzero(mask)) / mask.size


def mask_empty(mask: Mask, empty_ratio: float = DEFAULT_EMPTY_RATIO) -> bool:
    return filled_ratio(mask) < empty_ratio
