"""The speaker name in front of the text (TZ 4.5, mode «по цвету»)."""

import numpy as np

from subvoicer.pipeline.vision.regions import enlarged, name_beside_text


def test_the_name_is_taken_in_front_of_the_text_not_from_the_scene() -> None:
    """Cyberpunk: neon of the name colour elsewhere stretched the name cut-out over the
    scene and the line, and the name came out empty or glued to words of the line."""
    image = np.zeros((120, 600, 3), np.uint8)
    name = np.zeros((120, 600), np.uint8)
    text = np.zeros((120, 600), np.uint8)
    name[60:80, 100:140] = 255  # «Ви:»
    text[60:80, 150:500] = 255  # the line
    name[0:30, 400:600] = 255  # a neon sign of the same colour
    name[95:120, 0:50] = 255
    part = name_beside_text(image, name, text)
    assert part is not None and part.shape[:2] == (20 + 8, 40 + 8)  # the name and a margin
    assert name_beside_text(image, name, np.zeros_like(text)) is None  # no text: no idea


def test_a_short_name_is_enlarged_for_ocr() -> None:
    small = np.zeros((32, 42, 3), np.uint8)
    assert enlarged(small).shape[:2] == (96, 126)
    big = np.zeros((120, 300, 3), np.uint8)
    assert enlarged(big) is big


def test_the_name_on_its_own_row_above_the_line() -> None:
    """TZ 1.36 «Над текстом»: the row right above; a HUD label of the colour further up is not
    the name, and a tall name is taken whole even past the search band."""
    from subvoicer.pipeline.vision.regions import name_above

    image = np.zeros((200, 600, 3), np.uint8)
    name = np.zeros((200, 600), np.uint8)
    name[10:30, 300:500] = 255  # a HUD label far up
    name[85:118, 150:260] = 255  # the name, 33 px tall, its bottom 12 px above the line
    line = (150, 130, 550, 150)  # the first text row, 20 px tall
    part = name_above(image, name, line)
    assert part is not None and part.shape[:2] == (33 + 8, 110 + 8)
    assert name_above(image, np.zeros_like(name), line) is None
    far = np.zeros_like(name)
    far[10:30, 150:260] = 255  # only the label: too far above the line
    assert name_above(image, far, line) is None


def test_patches_of_the_scene_are_not_letters() -> None:
    """TZ 1.37: what the stabiliser of a colour zone compares."""
    from subvoicer.pipeline.vision.regions import letters_only

    mask = np.zeros((120, 600), np.uint8)
    for x in range(20, 400, 9):
        mask[50:70, x : x + 5] = 255  # letters: 20 px tall, solid but narrow strokes
        mask[52:66, x + 5 : x + 7] = 255
    mask[0:110, 450:520] = 255  # a tall lit patch of the scene
    mask[85:100, 100:300] = 255  # a solid wide patch, line-tall
    clean = letters_only(mask)
    assert np.count_nonzero(clean[50:70, 20:400]) == np.count_nonzero(mask[50:70, 20:400])
    assert not clean[:, 450:520].any() and not clean[85:100, 100:300].any()
    assert letters_only(np.zeros((10, 10), np.uint8)).sum() == 0
