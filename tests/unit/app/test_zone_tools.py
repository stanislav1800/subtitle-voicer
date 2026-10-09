import numpy as np

from subvoicer.app import profile_commands as pc
from subvoicer.app.zone_tools import colors_around
from subvoicer.config.profile import Zone
from subvoicer.ui.zone_editor.editor import DEFAULT_RECTS


def test_every_template_makes_a_valid_zone() -> None:
    for key, data in pc.zone_templates().items():
        zone = Zone.model_validate(
            {
                **data,
                "id": key,
                "rect": list(DEFAULT_RECTS[key]),
                **({"hotkey": "<ctrl>+<alt>+1"} if data.get("trigger") == "hotkey" else {}),
            }
        )
        assert zone.id == key


def test_eyedropper_ranges() -> None:
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    image[:, :] = (255, 220, 100)  # light blue in BGR
    (lo, hi), *_ = colors_around(image, 5, 5)
    assert lo[0] <= 98 <= hi[0] and hi[2] == 255  # H around the blue, V up to the top
