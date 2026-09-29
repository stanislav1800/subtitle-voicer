"""Latency report (TZ 6): subtitle appeared -> voice started.

    uv run python tools/latency_report.py [LOG ...]

Reads the DEBUG log (by default %APPDATA%/SubtitleVoicer/logs/subvoicer.log*), pairs
``LATENCY change id=N t=...`` (the frame where the zone mask first changed) with
``LATENCY play id=N t=...`` (the first audio buffer of that line) and prints the median,
the 95th percentile and the worst lines. A run of the app writes ids from 1 again, so
pairs are matched within each run (a run starts at the "Лог:" / start-up line or when ids
go back to 1).
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

from subvoicer.config.paths import AppPaths

CHANGE = re.compile(r"LATENCY change id=(\d+) t=([\d.]+)")
PLAY = re.compile(r"LATENCY play id=(\d+) t=([\d.]+)")
TARGET_MEDIAN_MS = 700
TARGET_P95_MS = 1200


def default_logs() -> list[Path]:
    logs = AppPaths.default().logs_dir
    return sorted(logs.glob("subvoicer.log*"), key=lambda p: p.stat().st_mtime)


def collect(paths: list[Path]) -> list[float]:
    """Latencies in ms, one per voiced line."""
    latencies: list[float] = []
    changes: dict[int, float] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if m := CHANGE.search(line):
                line_id, t = int(m.group(1)), float(m.group(2))
                if line_id == 1:
                    changes.clear()  # a new run
                changes[line_id] = t
            elif (m := PLAY.search(line)) and int(m.group(1)) in changes:
                latencies.append((float(m.group(2)) - changes.pop(int(m.group(1)))) * 1000)
    return latencies


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
    args = parser.parse_args()
    paths = args.logs or default_logs()
    if not paths:
        print("Лог не найден. Запустите watch --speak --debug.")
        return 1
    latencies = collect(paths)
    if not latencies:
        print("В логе нет пар LATENCY. Нужен запуск с --speak --debug.")
        return 1
    median = statistics.median(latencies)
    p95 = percentile(latencies, 0.95)
    print(f"Реплик: {len(latencies)}")
    print(
        f"Медиана: {median:.0f} мс (цель ≤ {TARGET_MEDIAN_MS}) {mark(median <= TARGET_MEDIAN_MS)}"
    )
    print(f"95-й перцентиль: {p95:.0f} мс (цель ≤ {TARGET_P95_MS}) {mark(p95 <= TARGET_P95_MS)}")
    print(f"Минимум / максимум: {min(latencies):.0f} / {max(latencies):.0f} мс")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
