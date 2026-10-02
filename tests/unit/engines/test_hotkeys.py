import pytest

from subvoicer.engines.system.hotkeys import ComboTracker, parse


def test_parse() -> None:
    assert parse("<ctrl>+<alt>+z") == (frozenset({"ctrl", "alt"}), 0x5A)
    assert parse("<ctrl>+<alt>+1") == (frozenset({"ctrl", "alt"}), 0x31)
    assert parse("<alt>+<f5>") == (frozenset({"alt"}), 0x74)
    with pytest.raises(ValueError):
        parse("<ctrl>+<alt>")


def test_layout_does_not_matter_and_fires_once() -> None:
    tracker = ComboTracker({parse("<ctrl>+<alt>+z"): "zone_editor"})
    assert tracker.press("ctrl", None) is None
    assert tracker.press("alt", None) is None
    assert tracker.press(None, 0x5A) == "zone_editor"  # «я» in the Russian layout: same key
    assert tracker.press(None, 0x5A) is None  # auto-repeat while held
    tracker.release(None, 0x5A)
    assert tracker.press(None, 0x5A) == "zone_editor"


def test_other_modifiers_do_not_fire() -> None:
    tracker = ComboTracker({parse("<ctrl>+<alt>+p"): "pause"})
    tracker.press("ctrl", None)
    assert tracker.press(None, 0x50) is None  # Ctrl+P only
    tracker.release(None, 0x50)
    tracker.press("alt", None)
    tracker.press("shift", None)
    assert tracker.press(None, 0x50) is None  # Ctrl+Alt+Shift+P is another combo
