"""Synthetic subtitle zone crops for the OCR benchmark (TZ section 9).

Run: uv run python tools/gen_synthetic.py [--count 360] [--seed 1] [--out tests/fixtures/synthetic]

Each image NNNN.png imitates a subtitle zone crop (1200x120 for one line, 1200x200 for two)
and comes with NNNN.txt (the exact text) and NNNN.json (parameters; benchmark categories are
``bg``, ``backing``, ``outline``, ``color``). Backgrounds are procedural only. Categories
``bg`` x ``backing`` are filled evenly; everything else is random but deterministic by seed.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
PHRASES = ROOT / "tools" / "data" / "phrases_ru.txt"
FONTS_DIR = Path("C:/Windows/Fonts")
FONT_FILES = [
    "arial.ttf",
    "arialbd.ttf",
    "segoeui.ttf",
    "segoeuib.ttf",
    "tahoma.ttf",
    "verdana.ttf",
    "calibri.ttf",
    "trebuc.ttf",
]

WIDTH = 1200
HEIGHT_BY_LINES = {1: 120, 2: 200}
MARGIN_X = 40
SIZE_RANGE = (28, 48)
LINE_SPACING = 1.25

BACKGROUNDS = ["dark", "light", "gradient", "noise", "motley", "neon"]
BACKINGS = {"none": 0.0, "a50": 0.5, "a80": 0.8}
OUTLINES = ["none", "outline", "shadow"]
COLORS: dict[str, tuple[int, int, int]] = {  # RGB
    "white": (255, 255, 255),
    "yellow": (255, 216, 58),
    "lightblue": (154, 212, 255),
    "turquoise": (63, 224, 208),
}

RGB = tuple[int, int, int]


@dataclass
class Layout:
    font: ImageFont.FreeTypeFont
    font_name: str
    size: int
    lines: list[str]


def load_phrases() -> list[str]:
    lines = PHRASES.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


def available_fonts() -> list[str]:
    fonts = [name for name in FONT_FILES if (FONTS_DIR / name).exists()]
    if not fonts:
        raise SystemExit(f"Нет ни одного шрифта из {FONT_FILES} в {FONTS_DIR}")
    return fonts


def text_width(font: ImageFont.FreeTypeFont, text: str) -> float:
    return font.getlength(text)


def wrap_two(font: ImageFont.FreeTypeFont, text: str) -> list[str]:
    """Split at the word boundary that makes the longer of two lines the shortest."""
    words = text.split()
    best: list[str] = [text]
    best_width = math.inf
    for i in range(1, len(words)):
        lines = [" ".join(words[:i]), " ".join(words[i:])]
        width = max(text_width(font, line) for line in lines)
        if width < best_width:
            best, best_width = lines, width
    return best


def layout(text: str, rng: random.Random, fonts: list[str]) -> Layout | None:
    font_name = rng.choice(fonts)
    size = rng.randint(*SIZE_RANGE)
    max_width = WIDTH - 2 * MARGIN_X
    while size >= SIZE_RANGE[0]:
        font = ImageFont.truetype(str(FONTS_DIR / font_name), size)
        if text_width(font, text) <= max_width:
            return Layout(font, font_name, size, [text])
        lines = wrap_two(font, text)
        if len(lines) == 2 and max(text_width(font, line) for line in lines) <= max_width:
            return Layout(font, font_name, size, lines)
        size -= 2
    return None


# ------------------------------------------------------------------ backgrounds


def rand_color(rng: random.Random, low: int = 0, high: int = 255) -> RGB:
    return (rng.randint(low, high), rng.randint(low, high), rng.randint(low, high))


def background(kind: str, height: int, text_rgb: RGB, rng: random.Random) -> Image.Image:
    np_rng = np.random.default_rng(rng.getrandbits(32))
    if kind == "dark":
        return Image.new("RGB", (WIDTH, height), rand_color(rng, 0, 50))
    if kind == "light":
        return Image.new("RGB", (WIDTH, height), rand_color(rng, 200, 255))
    if kind == "gradient":
        c1 = np.array(rand_color(rng), dtype=np.float32)
        c2 = np.array(rand_color(rng), dtype=np.float32)
        angle = rng.uniform(0, math.pi)
        ys, xs = np.mgrid[0:height, 0:WIDTH].astype(np.float32)
        proj = xs * math.cos(angle) + ys * math.sin(angle)
        t = (proj - proj.min()) / max(float(proj.max() - proj.min()), 1.0)
        pixels = c1 * (1 - t[..., None]) + c2 * t[..., None]
        return Image.fromarray(pixels.astype(np.uint8), "RGB")
    if kind == "noise":
        base = np.array(rand_color(rng, 60, 190), dtype=np.float32)
        pixels = base + np_rng.normal(0, 45, size=(height, WIDTH, 3))
        return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")
    if kind == "motley":
        image = Image.new("RGB", (WIDTH, height), rand_color(rng))
        draw = ImageDraw.Draw(image)
        for _ in range(rng.randint(30, 60)):
            x, y = rng.randint(-100, WIDTH), rng.randint(-60, height)
            w, h = rng.randint(20, 300), rng.randint(10, 150)
            shape = draw.rectangle if rng.random() < 0.5 else draw.ellipse
            shape((x, y, x + w, y + h), fill=rand_color(rng))
        return image.filter(ImageFilter.GaussianBlur(rng.uniform(3, 6)))
    if kind == "neon":
        image = Image.new("RGB", (WIDTH, height), rand_color(rng, 0, 40))
        draw = ImageDraw.Draw(image)
        for _ in range(rng.randint(4, 8)):  # stripes of the text colour
            x1, x2 = rng.randint(-200, WIDTH), rng.randint(0, WIDTH + 200)
            draw.line(
                (x1, rng.randint(0, height), x2, rng.randint(0, height)),
                fill=text_rgb,
                width=rng.randint(4, 20),
            )
        for _ in range(rng.randint(3, 8)):  # blobs of the text colour
            x, y, r = rng.randint(0, WIDTH), rng.randint(0, height), rng.randint(8, 40)
            draw.ellipse((x - r, y - r, x + r, y + r), fill=text_rgb)
        return image.filter(ImageFilter.GaussianBlur(1.5))
    raise ValueError(kind)


# ------------------------------------------------------------------ rendering


def render(
    lay: Layout, bg: str, backing: float, outline: str, color: RGB, rng: random.Random
) -> Image.Image:
    height = HEIGHT_BY_LINES[len(lay.lines)]
    image = background(bg, height, color, rng)
    line_height = round(lay.size * LINE_SPACING)
    block_height = line_height * len(lay.lines)
    top = (height - block_height) // 2 + rng.randint(-5, 5)
    centre_x = WIDTH // 2 + rng.randint(-20, 20)

    positions = []
    for i, line in enumerate(lay.lines):
        x = centre_x - text_width(lay.font, line) / 2
        positions.append((x, top + i * line_height, line))

    if backing > 0:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        for x, y, line in positions:
            left, t, right, bottom = odraw.textbbox((x, y), line, font=lay.font)
            odraw.rectangle(
                (left - 12, t - 6, right + 12, bottom + 6), fill=(0, 0, 0, round(255 * backing))
            )
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(image)
    for x, y, line in positions:
        if outline == "shadow":
            draw.text((x + 2, y + 2), line, font=lay.font, fill=(0, 0, 0))
        if outline == "outline":
            draw.text(
                (x, y), line, font=lay.font, fill=color, stroke_width=2, stroke_fill=(0, 0, 0)
            )
        else:
            draw.text((x, y), line, font=lay.font, fill=color)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--count", type=int, default=360, help="сколько изображений (по умолчанию 360)"
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "fixtures" / "synthetic")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    phrases = load_phrases()
    fonts = available_fonts()
    args.out.mkdir(parents=True, exist_ok=True)
    for old in [*args.out.glob("*.png"), *args.out.glob("*.txt"), *args.out.glob("*.json")]:
        old.unlink()

    backing_names = list(BACKINGS)
    for i in range(args.count):
        bg = BACKGROUNDS[i % len(BACKGROUNDS)]
        backing_name = backing_names[(i // len(BACKGROUNDS)) % len(backing_names)]
        while True:
            phrase_index = rng.randrange(len(phrases))
            lay = layout(phrases[phrase_index], rng, fonts)
            if lay is not None:
                break
        outline = rng.choice(OUTLINES)
        color_name = rng.choice(list(COLORS))
        image = render(lay, bg, BACKINGS[backing_name], outline, COLORS[color_name], rng)

        stem = args.out / f"{i:04d}"
        image.save(stem.with_suffix(".png"))
        stem.with_suffix(".txt").write_text(phrases[phrase_index] + "\n", encoding="utf-8")
        meta = {
            "bg": bg,
            "backing": backing_name,
            "outline": outline,
            "color": color_name,
            "color_rgb": list(COLORS[color_name]),
            "font": lay.font_name,
            "size": lay.size,
            "lines": len(lay.lines),
            "width": image.width,
            "height": image.height,
            "phrase": phrase_index,
            "seed": args.seed,
        }
        stem.with_suffix(".json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    print(
        f"Создано {args.count} изображений в {args.out} (seed {args.seed}, шрифтов: {len(fonts)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
