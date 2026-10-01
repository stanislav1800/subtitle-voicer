"""OCR on real game frames: ``tests/fixtures/real/<game>/name.png`` + ``name.txt``.

Run: uv run python tools/real_bench.py cyberpunk --hsv 84,58,144:104,218,255

For every model, upscale factor and input variant prints CER (Levenshtein distance / length,
whitespace normalised, summed over the frames), mean confidence and time per frame.
``--show`` prints the recognised text of the best configuration next to the reference.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
from rapidfuzz.distance import Levenshtein

from subvoicer.engines.ocr.rapid import MODELS, RapidOcrEngine
from subvoicer.pipeline.ocr.inputs import OcrInput
from subvoicer.pipeline.ocr.runner import OcrRunner, OcrSettings
from subvoicer.pipeline.vision.masks import MaskPreset, MaskSpec, build_mask

ROOT = Path(__file__).resolve().parent.parent


def squash(text: str) -> str:
    return " ".join(text.split())


def parse_hsv(value: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    lo, hi = value.split(":")
    a = tuple(int(v) for v in lo.split(","))
    b = tuple(int(v) for v in hi.split(","))
    return (a[0], a[1], a[2]), (b[0], b[1], b[2])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("game", help="папка в tests/fixtures/real")
    parser.add_argument("--hsv", required=True, type=parse_hsv, help="цвет текста для маски")
    parser.add_argument("--models", nargs="+", default=list(MODELS), choices=MODELS)
    parser.add_argument("--upscale", nargs="+", type=float, default=[1.0, 1.5, 2.0, 3.0])
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    folder = ROOT / "tests" / "fixtures" / "real" / args.game
    frames = []
    for image_path in sorted(folder.glob("*.png")):
        reference = squash(image_path.with_suffix(".txt").read_text(encoding="utf-8"))
        image = cv2.imread(str(image_path))
        mask = build_mask(image, MaskSpec(MaskPreset.COLOR, colors_hsv=(args.hsv,)))
        frames.append((image_path.stem, image, mask, reference))
    print(f"{len(frames)} кадров из {folder}\n")
    print(f"{'модель':<10}{'увелич.':>8}  {'вход':<8}{'CER %':>7}{'увер.':>7}{'мс':>7}")
    best: tuple[float, str, float, OcrInput] | None = None
    engines = {}
    for model in args.models:
        engine = RapidOcrEngine(model=model)
        engine.load()
        engines[model] = engine
        for upscale in args.upscale:
            # a huge min_line_height makes the upscale apply to every frame
            runner = OcrRunner(engine, OcrSettings(upscale=upscale, min_line_height=10_000))
            for kind in (OcrInput.RAW, OcrInput.MASKED):
                distance = length = 0
                confidence = elapsed = 0.0
                for _, image, mask, reference in frames:
                    started = time.perf_counter()
                    attempt = runner.attempt(image, mask, kind)
                    elapsed += time.perf_counter() - started
                    text = squash(" ".join(line.text for line in attempt.lines))
                    distance += Levenshtein.distance(text, reference)
                    length += len(reference)
                    confidence += attempt.mean_confidence
                cer = 100 * distance / length
                n = len(frames)
                print(
                    f"{model:<10}{upscale:>8.1f}  {kind:<8}{cer:>7.1f}"
                    f"{confidence / n:>7.2f}{elapsed / n * 1000:>7.0f}"
                )
                if best is None or cer < best[0]:
                    best = (cer, model, upscale, kind)
    assert best is not None
    print(f"\nЛучшее: {best[1]}, увеличение {best[2]}, вход {best[3]}: CER {best[0]:.1f}%")
    if args.show:
        runner = OcrRunner(engines[best[1]], OcrSettings(upscale=best[2], min_line_height=10_000))
        for name, image, mask, reference in frames:
            attempt = runner.attempt(image, mask, best[3])
            recognised = " ".join(line.text for line in attempt.lines)
            print(f"\n{name}\n  надо: {reference}\n  есть: {recognised}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
