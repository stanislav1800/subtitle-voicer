"""Latency report (TZ 6): subtitle appeared -> voice started, total and step by step.

    uv run python tools/latency_report.py [LOG ...] [--last N]

Reads the log (by default %APPDATA%/SubtitleVoicer/logs/subvoicer.log*). Every voiced line
leaves timing points (logger ``subvoicer.latency``, written at any log level):

    LATENCY change id=N t=...  detected=...  ocr=MS   the zone mask first changed; OCR done
                               ... kind=new text='...' (what was recognized)
    LATENCY submit id=N t=...                         the line reached the speech service
    LATENCY synth  id=N t=...  prep=MS synth=MS       text preparation and the first piece
    LATENCY play   id=N t=...                         the first audio buffer of the line

and the steps are the gaps between them. A run of the app writes ids from 1 again, so points
are matched within each run. Logs from before the step points give only the total.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

from subvoicer.config.paths import AppPaths

POINT = re.compile(r"LATENCY (change|submit|synth|play) id=(\d+) t=([\d.]+)(.*)")
FIELD = re.compile(r"(\w+)=([\d.]+)")
TARGET_MEDIAN_MS = 700
TARGET_P95_MS = 1200
STEPS = (
    ("wait", "Ожидание, пока текст перестанет меняться"),
    ("ocr", "Распознавание (OCR)"),
    ("handover", "Передача в озвучку (говорящий, голос)"),
    ("queue", "Очередь: ждёт другую реплику"),
    ("prep", "Подготовка текста (ударения, числа)"),
    ("synth", "Синтез первого куска"),
    ("output", "Вывод звука до первого буфера"),
)


@dataclass
class Line:
    times: dict[str, float] = field(default_factory=dict)  # point -> seconds
    values: dict[str, float] = field(default_factory=dict)  # detected, ocr, prep, synth

    @property
    def total_ms(self) -> float | None:
        if "change" in self.times and "play" in self.times:
            return (self.times["play"] - self.times["change"]) * 1000
        return None

    def steps(self) -> dict[str, float] | None:
        """Step -> ms, when the line has every point (a newer log)."""
        t, v = self.times, self.values
        if not {"change", "submit", "synth", "play"} <= t.keys() or "ocr" not in v:
            return None
        ocr_start = v["detected"] - v["ocr"] / 1000
        synth_end = t["synth"] + (v["prep"] + v["synth"]) / 1000
        return {
            "wait": (ocr_start - t["change"]) * 1000,
            "ocr": v["ocr"],
            "handover": (t["submit"] - v["detected"]) * 1000,
            "queue": (t["synth"] - t["submit"]) * 1000,
            "prep": v["prep"],
            "synth": v["synth"],
            "output": (t["play"] - synth_end) * 1000,
        }


def default_logs() -> list[Path]:
    logs = AppPaths.default().logs_dir
    return sorted(logs.glob("subvoicer.log*"), key=lambda p: p.stat().st_mtime)


def collect(paths: list[Path]) -> list[Line]:
    """Voiced lines (those with a ``play`` point), in log order."""
    lines: list[Line] = []
    run: dict[int, Line] = {}
    for path in paths:
        for text in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = POINT.search(text)
            if m is None:
                continue
            point, line_id, t = m.group(1), int(m.group(2)), float(m.group(3))
            if point == "change" and line_id == 1:
                run = {}  # a new run of the app
            line = run.setdefault(line_id, Line())
            line.times[point] = t
            fields = m.group(4).split(" text=", 1)[0]  # the recognized text may hold "a=1"
            line.values.update({k: float(x) for k, x in FIELD.findall(fields)})
            if point == "play" and "change" in line.times:
                lines.append(run.pop(line_id))
    return lines


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "logs", nargs="*", type=Path, help="файлы лога (по умолчанию — лог приложения)"
    )
    parser.add_argument("--last", type=int, default=0, help="только последние N реплик")
    args = parser.parse_args()
    paths = args.logs or default_logs()
    if not paths:
        print("Лог не найден.")
        return 1
    lines = collect(paths)[-args.last :] if args.last else collect(paths)
    totals = [t for line in lines if (t := line.total_ms) is not None]
    if not totals:
        print("В логе нет озвученных реплик с отметками LATENCY.")
        return 1
    median = statistics.median(totals)
    p95 = percentile(totals, 0.95)
    print(f"Реплик: {len(totals)}")
    print(
        f"Медиана: {median:.0f} мс (цель ≤ {TARGET_MEDIAN_MS}) {mark(median <= TARGET_MEDIAN_MS)}"
    )
    print(f"95-й перцентиль: {p95:.0f} мс (цель ≤ {TARGET_P95_MS}) {mark(p95 <= TARGET_P95_MS)}")
    print(f"Минимум / максимум: {min(totals):.0f} / {max(totals):.0f} мс")
    detailed = [s for line in lines if (s := line.steps()) is not None]
    if not detailed:
        print("\nПо шагам: в логе нет новых отметок — запустите приложение заново и поиграйте.")
        return 0
    print(f"\nПо шагам ({len(detailed)} реплик), мс:")
    print(f"  {'шаг':<44}{'медиана':>9}{'95%':>8}{'макс.':>8}")
    for key, title in STEPS:
        values = [s[key] for s in detailed]
        print(
            f"  {title:<44}{statistics.median(values):>9.0f}"
            f"{percentile(values, 0.95):>8.0f}{max(values):>8.0f}"
        )
    total = statistics.median([sum(s.values()) for s in detailed])
    print(f"  {'Итого (медиана суммы шагов)':<44}{total:>9.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
