import pytest

from subvoicer.engines.system.hotkeys import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, flags, parse


def test_parse() -> None:
    assert parse("<ctrl>+<alt>+z") == (frozenset({"ctrl", "alt"}), 0x5A)
    assert parse("<ctrl>+<alt>+1") == (frozenset({"ctrl", "alt"}), 0x31)
    assert parse("<alt>+<f5>") == (frozenset({"alt"}), 0x74)
    with pytest.raises(ValueError):
        parse("<ctrl>+<alt>")


def test_register_flags_without_key_repeat() -> None:
    modifiers, _ = parse("<ctrl>+<alt>+z")
    assert flags(modifiers) == MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
