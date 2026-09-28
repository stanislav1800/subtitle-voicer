import argparse

import pytest

from subvoicer.cli import build_parser, parse_hsv_range, parse_rect


def test_parse_rect() -> None:
    rect = parse_rect("0.2,0.82,0.8,0.95")
    assert (rect.x1, rect.y1, rect.x2, rect.y2) == (0.2, 0.82, 0.8, 0.95)


@pytest.mark.parametrize("value", ["0.2,0.8,0.1,0.9", "1,2,3", "a,b,c,d", "0,0,1.5,1"])
def test_parse_rect_errors(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_rect(value)


def test_parse_hsv_range() -> None:
    assert parse_hsv_range("80,80,150:100,255,255") == ((80, 80, 150), (100, 255, 255))


@pytest.mark.parametrize("value", ["80,80:100,255,255", "80,80,150", "a,b,c:d,e,f"])
def test_parse_hsv_range_errors(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_hsv_range(value)


def test_watch_arguments_from_tz() -> None:
    args = build_parser().parse_args(
        [
            "watch",
            "--exe",
            "chrome.exe",
            "--title",
            "YouTube",
            "--rect",
            "0.2,0.82,0.8,0.95",
            "--preset",
            "bright_on_dark",
            "--speaker",
            "prefix",
            "--debug",
        ]
    )
    assert args.exe == "chrome.exe"
    assert args.preset == "bright_on_dark"
    assert args.speaker == "prefix"
    assert args.debug


def test_color_hsv_repeats() -> None:
    args = build_parser().parse_args(
        [
            "watch",
            "--exe",
            "x.exe",
            "--rect",
            "0,0,1,1",
            "--preset",
            "color",
            "--color-hsv",
            "0,0,175:10,80,255",
            "--color-hsv",
            "170,0,175:179,80,255",
        ]
    )
    assert len(args.color_hsv) == 2
