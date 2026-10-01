import pytest

from subvoicer.pipeline.games.aspect import aspect_key
from subvoicer.pipeline.games.matching import OfferPolicy, covers_monitor


@pytest.mark.parametrize(
    ("size", "key"),
    [
        ((1920, 1080), "16:9"),
        ((2560, 1600), "16:10"),
        ((3440, 1440), "21:9"),
        ((5120, 1440), "32:9"),
        ((1024, 768), "4:3"),
        ((1366, 768), "16:9"),
        ((1280, 1024), "1280x1024"),
    ],
)
def test_aspect(size: tuple[int, int], key: str) -> None:
    assert aspect_key(*size) == key


def test_covers_monitor() -> None:
    assert covers_monitor((0, 0, 1920, 1080), (0, 0, 1920, 1080))
    assert covers_monitor((1919, -1, 3841, 1081), (1920, 0, 3840, 1080))
    assert not covers_monitor((0, 0, 1900, 1080), (0, 0, 1920, 1080))


def test_offer_policy() -> None:
    policy = OfferPolicy(["Firefox.exe"], ["skip.exe"])
    assert not policy.should_offer("firefox.exe", True)
    assert not policy.should_offer("SKIP.EXE", True)
    assert not policy.should_offer("game.exe", False)
    assert policy.should_offer("game.exe", True)
    assert not policy.should_offer("Game.exe", True)  # asked once per run
