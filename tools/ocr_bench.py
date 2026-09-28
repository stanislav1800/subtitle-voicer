"""OCR benchmark on synthetic zone crops (TZ section 9, stage 1).

Run: uv run python tools/ocr_bench.py [--models cyrillic eslav] [--limit N] [--data DIR]

For every image, model, mask preset (``color`` from the known text colour and
``bright_on_dark``) and input variant (raw / masked / mask) it runs OCR once and records
text, mean confidence, junk-filter verdict and time. The "runner" rows simulate the
production path: a default variant plus retries of the other two on low confidence.

Output: a Markdown report and raw per-image JSON in ``reports/``.
CER = Levenshtein distance / reference length after whitespace normalisation, aggregated
over a category as sum(distances) / sum(lengths). CER is measured on the recognised text
before the junk filter; the filter's rejects are reported separately.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from rapidfuzz.distance import Levenshtein

from subvoicer.config.resources import short_words
from subvoicer.engines.ocr.rapid import MODELS, RapidOcrEngine
from subvoicer.pipeline.ocr.inputs import OcrInput
from subvoicer.pipeline.ocr.junk_filter import JunkFilter
from subvoicer.pipeline.ocr.runner import OcrRunner, OcrSettings
from subvoicer.pipeline.vision.masks import (
    MaskPreset,
    MaskSpec,
    bgr_to_hsv,
    build_mask,
    hsv_ranges_around,
)

ROOT = Path(__file__).resolve().parent.parent
PRESETS: tuple[MaskPreset, ...] = (MaskPreset.COLOR, MaskPreset.BRIGHT_ON_DARK)
VARIANTS: tuple[OcrInput, ...] = tuple(OcrInput)
BGS = ["dark", "light", "gradient", "noise", "motley", "neon"]
BACKINGS = ["none", "a50", "a80"]
LOW_CONFIDENCE = OcrSettings().low_confidence

Records = dict[tuple[str, str, str], "Record"]
Getter = Callable[["Sample"], "Record"]


@dataclass
class Sample:
    name: str
    image: np.ndarray
    truth: str
    meta: dict[str, object]


@dataclass
class Record:
    """One OCR attempt."""

    text: str
    confidence: float
    rejected: bool
    ms: float
    distance: int
    length: int


@dataclass
class Acc:
    distance: int = 0
    length: int = 0
    rejected: int = 0
    count: int = 0
    ms: list[float] = field(default_factory=list)

    def add(self, distance: int, length: int, rejected: bool, ms: float) -> None:
        self.distance += distance
        self.length += length
        self.rejected += int(rejected)
        self.count += 1
        self.ms.append(ms)

    @property
    def cer(self) -> float:
        return self.distance / self.length if self.length else 0.0


def normalize(text: str) -> str:
    return " ".join(text.split())


def load_samples(data: Path, limit: int | None) -> list[Sample]:
    samples = []
    for png in sorted(data.glob("*.png"))[:limit]:
        image = cv2.imdecode(np.fromfile(png, dtype=np.uint8), cv2.IMREAD_COLOR)
        truth = png.with_suffix(".txt").read_text(encoding="utf-8").strip()
        meta = json.loads(png.with_suffix(".json").read_text(encoding="utf-8"))
        samples.append(Sample(png.stem, image, truth, meta))
    return samples


def mask_spec(preset: MaskPreset, meta: dict[str, object]) -> MaskSpec:
    if preset is MaskPreset.COLOR:
        r, g, b = meta["color_rgb"]  # type: ignore[misc]
        ranges = tuple(hsv_ranges_around(bgr_to_hsv((b, g, r))))
        return MaskSpec(MaskPreset.COLOR, colors_hsv=ranges)
    return MaskSpec(preset)


def run_model(
    model: str,
    samples: list[Sample],
    junk: JunkFilter,
    presets: tuple[MaskPreset, ...],
    merge_rows: bool,
) -> Records:
    """(sample, preset, variant) -> Record."""
    engine = RapidOcrEngine(model=model, merge_rows=merge_rows)
    engine.load()
    runner = OcrRunner(engine)
    # warm-up: the first calls allocate onnxruntime buffers
    for _ in range(3):
        runner.attempt(
            samples[0].image, np.zeros(samples[0].image.shape[:2], np.uint8), OcrInput.RAW
        )

    records: Records = {}
    started = time.perf_counter()
    for index, sample in enumerate(samples, 1):
        truth = normalize(sample.truth)
        for preset in presets:
            mask = build_mask(sample.image, mask_spec(preset, sample.meta))
            for variant in VARIANTS:
                t0 = time.perf_counter()
                attempt = runner.attempt(sample.image, mask, variant)
                ms = (time.perf_counter() - t0) * 1000
                text = normalize(" ".join(line.text for line in attempt.lines))
                records[(sample.name, preset, variant)] = Record(
                    text=text,
                    confidence=attempt.mean_confidence,
                    rejected=not junk.check(text, attempt.mean_confidence).ok,
                    ms=ms,
                    distance=Levenshtein.distance(text, truth),
                    length=len(truth),
                )
        if index % 50 == 0 or index == len(samples):
            elapsed = time.perf_counter() - started
            print(f"  [{model}] {index}/{len(samples)} за {elapsed:.0f} с", flush=True)
    engine.close()
    return records


def runner_record(records: Records, name: str, preset: str, default: OcrInput) -> Record:
    """Production path: ``default`` first, the other variants only on low confidence."""
    first = records[(name, preset, default)]
    tried = [first]
    if first.confidence < LOW_CONFIDENCE:
        tried += [records[(name, preset, v)] for v in VARIANTS if v is not default]
    best = max(tried, key=lambda r: r.confidence)
    return Record(
        best.text,
        best.confidence,
        best.rejected,
        sum(r.ms for r in tried),
        best.distance,
        best.length,
    )


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def table(title: str, accs: dict[tuple[str, str], Acc]) -> list[str]:
    """CER by bg (rows) x backing (columns) plus totals."""
    out = [f"**{title}** — CER (отброшено)", "", "| фон | " + " | ".join(BACKINGS) + " | всего |"]
    out.append("|---" * (len(BACKINGS) + 2) + "|")
    for bg in BGS:
        row_acc = Acc()
        cells = []
        for backing in BACKINGS:
            acc = accs.get((bg, backing), Acc())
            cells.append(f"{pct(acc.cer)} ({acc.rejected}/{acc.count})")
            _merge(row_acc, acc)
        out.append(f"| {bg} | " + " | ".join(cells) + f" | {pct(row_acc.cer)} |")
    total = Acc()
    col_cells = []
    for backing in BACKINGS:
        col = Acc()
        for bg in BGS:
            _merge(col, accs.get((bg, backing), Acc()))
        col_cells.append(pct(col.cer))
        _merge(total, col)
    out.append("| **всего** | " + " | ".join(col_cells) + f" | **{pct(total.cer)}** |")
    rejected = total.rejected / total.count if total.count else 0.0
    out += ["", f"Отброшено фильтром мусора: {pct(rejected)}", ""]
    return out


def _merge(into: Acc, other: Acc) -> None:
    into.distance += other.distance
    into.length += other.length
    into.rejected += other.rejected
    into.count += other.count
    into.ms += other.ms


def fixed_input(records: Records, preset: str, variant: OcrInput) -> Getter:
    """One OCR attempt with a fixed input variant."""
    return lambda s: records[(s.name, preset, variant)]


def runner_path(records: Records, preset: str, default: OcrInput) -> Getter:
    """The production path with ``default`` input and low-confidence retries."""
    return lambda s: runner_record(records, s.name, preset, default)


def accumulate(samples: list[Sample], get: Getter) -> dict[tuple[str, str], Acc]:
    accs: dict[tuple[str, str], Acc] = defaultdict(Acc)
    for s in samples:
        r = get(s)
        accs[(str(s.meta["bg"]), str(s.meta["backing"]))].add(
            r.distance, r.length, r.rejected, r.ms
        )
    return accs


def targets(samples: list[Sample], get: Getter) -> list[str]:
    """TZ section 6 targets for the color preset."""
    backed, backed_no_neon, plain = Acc(), Acc(), Acc()
    for s in samples:
        r = get(s)
        if s.meta["backing"] != "none":
            backed.add(r.distance, r.length, r.rejected, r.ms)
            if s.meta["bg"] != "neon":
                backed_no_neon.add(r.distance, r.length, r.rejected, r.ms)
        elif s.meta["bg"] in ("noise", "motley"):
            plain.add(r.distance, r.length, r.rejected, r.ms)

    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    return [
        f"- с подложкой, все фоны: CER {pct(backed.cer)} (цель ≤ 2%) {mark(backed.cer <= 0.02)}",
        f"- с подложкой, без «неона»: CER {pct(backed_no_neon.cer)}",
        f"- без подложки, «шум» и «пёстрый»: CER {pct(plain.cer)} (цель ≤ 8%) "
        + mark(plain.cer <= 0.08),
    ]


def timing(samples: list[Sample], get: Getter) -> str:
    one_line = [get(s).ms for s in samples if s.meta["lines"] == 1]
    two_line = [get(s).ms for s in samples if s.meta["lines"] == 2]
    parts = [f"1200×120: среднее {np.mean(one_line):.0f} мс, медиана {np.median(one_line):.0f} мс"]
    if two_line:
        parts.append(f"1200×200: среднее {np.mean(two_line):.0f} мс")
    return "; ".join(parts)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace", line_buffering=True)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--data", type=Path, default=ROOT / "tests" / "fixtures" / "synthetic")
    parser.add_argument("--models", nargs="+", default=list(MODELS), choices=MODELS)
    parser.add_argument("--limit", type=int, default=None, help="взять только первые N изображений")
    parser.add_argument(
        "--presets",
        nargs="+",
        default=[p.value for p in PRESETS],
        choices=[p.value for p in PRESETS],
    )
    parser.add_argument(
        "--no-merge-rows", action="store_true", help="распознавать куски детектора по отдельности"
    )
    parser.add_argument("--out", type=Path, default=ROOT / "reports")
    args = parser.parse_args()

    presets = tuple(MaskPreset(p) for p in args.presets)
    samples = load_samples(args.data, args.limit)
    if not samples:
        print(f"Нет изображений в {args.data}. Сначала: uv run python tools/gen_synthetic.py")
        return 1
    junk = JunkFilter(short_words())
    print(f"Изображений: {len(samples)}; модели: {', '.join(args.models)}")

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    report = [
        f"# Бенчмарк OCR — {stamp}",
        "",
        f"Изображений: {len(samples)} ({args.data}); склейка строк: {not args.no_merge_rows}",
        "",
    ]
    raw: dict[str, dict[str, object]] = {}
    summary: list[str] = ["## Сводка: CER по вариантам входа (все изображения)", ""]
    summary.append("| модель | пресет | " + " | ".join(v.value for v in VARIANTS) + " |")
    summary.append("|---|---|" + "---|" * len(VARIANTS))

    for model in args.models:
        print(f"Модель {model}...")
        records = run_model(model, samples, junk, presets, merge_rows=not args.no_merge_rows)
        raw[model] = {"|".join(k): vars(r) for k, r in records.items()}
        report += [f"## Модель `{model}`", ""]
        for preset in presets:
            cers = []
            for variant in VARIANTS:
                get = fixed_input(records, preset, variant)
                accs = accumulate(samples, get)
                total = Acc()
                for acc in accs.values():
                    _merge(total, acc)
                cers.append(pct(total.cer))
                report += table(f"{model} / {preset} / вход {variant}", accs)
                report += ["Время: " + timing(samples, get), ""]
            summary.append(f"| {model} | {preset} | " + " | ".join(cers) + " |")
            for default in VARIANTS:
                get = runner_path(records, preset, default)
                report += table(
                    f"{model} / {preset} / runner, по умолчанию {default}",
                    accs=accumulate(samples, get),
                )
                if preset is MaskPreset.COLOR:
                    report += ["Цели раздела 6:", *targets(samples, get), ""]
                report += ["Время: " + timing(samples, get), ""]

    args.out.mkdir(parents=True, exist_ok=True)
    md = args.out / f"ocr_bench_{stamp}.md"
    md.write_text("\n".join([*summary, "", *report]) + "\n", encoding="utf-8")
    (args.out / f"ocr_bench_{stamp}.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )
    print("\n".join(summary))
    print(f"\nОтчёт: {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
