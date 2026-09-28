"""Command line entry point: ``uv run python -m subvoicer.cli <command>`` (TZ 4.15).

Commands: ``watch`` (stage 2). ``run`` and ``characters`` come with stage 5.
"""

from __future__ import annotations

import argparse
import logging
import queue
import sys
from collections.abc import Sequence
from datetime import datetime

from subvoicer.app import runtime
from subvoicer.app.bus import Bus
from subvoicer.app.container import MASK_PRESETS, SPEAKER_MODES, WatchConfig, build_watch
from subvoicer.app.log import setup_logging
from subvoicer.config.paths import AppPaths
from subvoicer.core.events import CaptureFailed, CaptureStopped, Event, UtteranceDetected
from subvoicer.core.models import Rect, UtteranceKind, WindowRef

log = logging.getLogger("subvoicer.cli")

EXIT_WINDOW_NOT_CHOSEN = 2


def parse_rect(value: str) -> Rect:
    try:
        x1, y1, x2, y2 = (float(part) for part in value.split(","))
        return Rect(x1, y1, x2, y2)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается x1,y1,x2,y2 в долях кадра (0..1), получено {value!r}: {exc}"
        ) from None


def parse_hsv_range(value: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    try:
        low, high = value.split(":")
        lo = tuple(int(v) for v in low.split(","))
        hi = tuple(int(v) for v in high.split(","))
        if len(lo) != 3 or len(hi) != 3:
            raise ValueError("нужно по 3 числа")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается h1,s1,v1:h2,s2,v2 (OpenCV: H 0-179, S и V 0-255), получено {value!r}"
        ) from exc
    return lo, hi


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="subvoicer", description="Subtitle Voicer (CLI)")
    commands = parser.add_subparsers(dest="command", required=True)

    watch = commands.add_parser(
        "watch", help="следить за одной зоной окна и печатать реплики (этап 2)"
    )
    watch.add_argument("--exe", required=True, help="имя exe процесса окна, например firefox.exe")
    watch.add_argument("--title", help="подстрока заголовка, если окон несколько")
    watch.add_argument(
        "--rect", required=True, type=parse_rect, help="зона x1,y1,x2,y2 в долях кадра"
    )
    watch.add_argument("--preset", required=True, choices=MASK_PRESETS, help="пресет маски")
    watch.add_argument(
        "--color-hsv",
        action="append",
        type=parse_hsv_range,
        default=[],
        help="диапазон цвета текста для пресета color: h1,s1,v1:h2,s2,v2 (можно несколько)",
    )
    watch.add_argument("--threshold", type=int, help="порог яркости для bright/dark_on_*")
    watch.add_argument("--speaker", choices=SPEAKER_MODES, default="none")
    watch.add_argument("--debug", action="store_true", help="подробный лог (DEBUG)")
    return parser


def format_window(w: WindowRef) -> str:
    return f"  hwnd={w.hwnd:<10} {w.exe:<24} {w.width}x{w.height}  {w.title}"


def choose_window(exe: str, title: str | None) -> WindowRef | None:
    candidates = runtime.find_windows(exe, title)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        print(f"Найдено несколько окон {exe}; уточните --title:", file=sys.stderr)
        shown = candidates
    else:
        print(
            f"Окно {exe}{f' с «{title}»' if title else ''} не найдено. Все окна:", file=sys.stderr
        )
        shown = runtime.all_windows()
    for w in shown:
        print(format_window(w), file=sys.stderr)
    return None


def print_utterance(event: UtteranceDetected) -> None:
    u = event.utterance
    stamp = datetime.now().strftime("%H:%M:%S")
    text = f"… {u.text}" if u.kind is UtteranceKind.CONTINUATION else u.text
    print(f"[{stamp}] [{u.speaker or '-'}] {text}", flush=True)


def run_watch(args: argparse.Namespace) -> int:
    if args.preset == "color" and not args.color_hsv:
        print("Для пресета color нужен хотя бы один --color-hsv", file=sys.stderr)
        return EXIT_WINDOW_NOT_CHOSEN
    window = choose_window(args.exe, args.title)
    if window is None:
        return EXIT_WINDOW_NOT_CHOSEN
    log.info("Окно: %s «%s» (%dx%d)", window.exe, window.title, window.width, window.height)

    bus = Bus()
    events: queue.Queue[Event] = queue.Queue()
    bus.subscribe(Event, events.put)
    config = WatchConfig(
        hwnd=window.hwnd,
        rect=args.rect,
        preset=args.preset,
        colors_hsv=tuple(args.color_hsv),
        threshold=args.threshold,
        speaker=args.speaker,
    )
    print("Загрузка OCR…", file=sys.stderr)
    session = build_watch(config, bus)
    session.start()
    print("Слежу за зоной. Ctrl+C — выход.", file=sys.stderr)
    try:
        while True:
            try:
                event = events.get(timeout=0.5)
            except queue.Empty:
                continue
            if isinstance(event, UtteranceDetected):
                print_utterance(event)
            elif isinstance(event, CaptureFailed):
                print("Не удаётся захватить окно игры — продолжаю пытаться", file=sys.stderr)
            elif isinstance(event, CaptureStopped):
                print("Окно закрыто.", file=sys.stderr)
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        session.stop()


def main(argv: Sequence[str] | None = None) -> int:
    runtime.prepare_process()
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace", line_buffering=True)
    log_file = setup_logging(
        AppPaths.default().logs_dir,
        level=logging.DEBUG if args.debug else logging.INFO,
        console=True,
    )
    log.debug("Лог: %s", log_file)
    if args.command == "watch":
        return run_watch(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
