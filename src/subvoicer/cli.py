"""Command line entry point: ``uv run python -m subvoicer.cli <command>`` (TZ 4.15).

Commands: ``watch`` (stages 2-3); ``run``, ``profiles``, ``characters`` (stage 5).
"""

from __future__ import annotations

import argparse
import logging
import queue
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from subvoicer.app import profile_commands as pc
from subvoicer.app import runtime
from subvoicer.app.bus import Bus
from subvoicer.app.container import (
    DEFAULT_HOTKEYS,
    MASK_PRESETS,
    OVERLAP_MODES,
    PRIORITIES,
    SPEAKER_MODES,
    SPEAKER_MODES_ALL,
    TTS_CHAIN,
    VOICE_MODES,
    SpeechConfig,
    WatchConfig,
    build_watch,
)
from subvoicer.app.game_runner import build_run
from subvoicer.app.log import setup_logging
from subvoicer.app.models_setup import ConsoleProgress, ModelSetup, engine_options
from subvoicer.config.paths import AppPaths
from subvoicer.config.profile_store import format_error
from subvoicer.config.settings import load_settings
from subvoicer.core.errors import ModelsError
from subvoicer.core.events import (
    CaptureFailed,
    CaptureStopped,
    Event,
    GameActivated,
    GameClosed,
    LayoutMissing,
    ProfileBound,
    ProfileOffered,
    UtteranceDetected,
)
from subvoicer.core.models import Rect, UtteranceKind, WindowRef

log = logging.getLogger("subvoicer.cli")

EXIT_WINDOW_NOT_CHOSEN = 2
EXIT_ALREADY_RUNNING = 3


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
    speech = watch.add_argument_group("озвучка (этап 3)")
    speech.add_argument("--speak", action="store_true", help="озвучивать реплики")
    speech.add_argument("--dict", type=Path, help="словарь произношений: JSON-список записей")
    speech.add_argument("--voice-mode", choices=VOICE_MODES, default="speaker")
    speech.add_argument("--voice", help="id голоса для --voice-mode fixed")
    speech.add_argument("--priority", choices=PRIORITIES, default="high")
    speech.add_argument("--rate", type=float, default=1.0, help="базовая скорость речи")
    speech.add_argument(
        "--overlap",
        choices=OVERLAP_MODES,
        default="speedup",
        help="новая реплика, пока звучит старая: cut — оборвать, queue — ждать, "
        "speedup — ускорить старую и дочитать, adaptive — скорость по длине очереди",
    )
    speech.add_argument(
        "--hurry-rate", type=float, default=1.5, help="во сколько раз ускорять в режиме speedup"
    )
    speech.add_argument(
        "--max-lag", type=float, default=8.0, help="реплика, ждущая дольше (с), пропускается"
    )
    speech.add_argument("--volume", type=float, default=1.0, help="громкость 0..1")
    speech.add_argument("--tts", choices=TTS_CHAIN, help="только этот движок (иначе цепочка)")
    speech.add_argument("--no-hotkeys", action="store_true", help="без горячих клавиш")
    speech.add_argument("--no-duck", action="store_true", help="не приглушать игру")
    speech.add_argument(
        "--duck-level", type=float, default=0.4, help="до какой доли приглушать игру (0..1)"
    )
    watch.add_argument("--debug", action="store_true", help="подробный лог (DEBUG)")

    run = commands.add_parser("run", help="озвучка по профилям без GUI (этап 5)")
    run.add_argument("--no-hotkeys", action="store_true", help="без горячих клавиш")
    run.add_argument("--debug", action="store_true", help="подробный лог (DEBUG)")
    add_profile_commands(commands)
    add_character_commands(commands)
    return parser


def add_profile_commands(commands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    profiles = commands.add_parser("profiles", help="профили игр").add_subparsers(
        dest="action", required=True
    )
    profiles.add_parser("list", help="список профилей")
    new = profiles.add_parser("new", help="новый профиль без зон")
    new.add_argument("exe", help="имя exe игры, например Cyberpunk2077.exe")
    new.add_argument("--title", help="название игры")
    new.add_argument("--install-dir", help="папка установки")
    zone = profiles.add_parser("add-zone", help="добавить зону из шаблона (ТЗ 4.2)")
    zone.add_argument("profile", help="exe или имя файла профиля")
    zone.add_argument("--template", required=True, choices=tuple(pc.zone_templates()))
    zone.add_argument("--rect", required=True, type=parse_rect, help="x1,y1,x2,y2 в долях кадра")
    zone.add_argument("--aspect", help="соотношение сторон (по умолчанию — по окну игры)")
    zone.add_argument("--id", help="id зоны (по умолчанию — имя шаблона)")
    zone.add_argument("--name", help="отображаемое имя")
    zone.add_argument("--preset", choices=MASK_PRESETS, help="пресет маски")
    zone.add_argument("--color-hsv", action="append", type=parse_hsv_range, default=[])
    zone.add_argument("--threshold", type=int)
    zone.add_argument("--priority", choices=PRIORITIES)
    zone.add_argument("--voice-mode", choices=VOICE_MODES)
    zone.add_argument("--voice", help="id голоса для --voice-mode fixed")
    zone.add_argument("--speaker", choices=SPEAKER_MODES_ALL, help="как определять говорящего")
    zone.add_argument("--speaker-zone", help="id зоны вида speaker для --speaker zone")
    zone.add_argument(
        "--speaker-hsv", action="append", type=parse_hsv_range, default=[], help="цвет имени"
    )
    zone.add_argument("--cooldown", type=float, help="не повторять N минут")
    zone.add_argument("--duck", choices=("yes", "no"), help="приглушать игру")
    export = profiles.add_parser("export", help="профиль в один файл")
    export.add_argument("profile")
    export.add_argument("file", type=Path)
    imported = profiles.add_parser("import", help="профиль из файла")
    imported.add_argument("file", type=Path)


def add_character_commands(commands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    characters = commands.add_parser("characters", help="персонажи профиля").add_subparsers(
        dest="action", required=True
    )
    listed = characters.add_parser("list", help="персонажи и их голоса")
    listed.add_argument("profile")
    imported = characters.add_parser("import", help="импорт из CSV: имя;пол;варианты")
    imported.add_argument("profile")
    imported.add_argument("file", type=Path)
    merge = characters.add_parser("merge", help="A становится вариантом имени B")
    merge.add_argument("profile")
    merge.add_argument("a")
    merge.add_argument("b")


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


def speech_config(args: argparse.Namespace) -> SpeechConfig | None:
    if not args.speak:
        return None
    if args.voice_mode == "fixed" and not args.voice:
        raise SystemExit("Для --voice-mode fixed нужен --voice")
    return SpeechConfig(
        dictionary_path=args.dict,
        voice_mode=args.voice_mode,
        voice_id=args.voice,
        priority=args.priority,
        base_rate=args.rate,
        overlap=args.overlap,
        hurry_rate=args.hurry_rate,
        max_lag_s=args.max_lag,
        volume=args.volume,
        tts=(args.tts,) if args.tts else TTS_CHAIN,
        hotkeys=not args.no_hotkeys,
        duck=not args.no_duck,
        duck_level=args.duck_level,
    )


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
    ocr_options, tts_options = engine_options(AppPaths.default())
    speech = speech_config(args)
    if speech is not None:
        speech = replace(speech, silero_models=tuple(tts_options["models"].items()))
    config = WatchConfig(
        hwnd=window.hwnd,
        pid=window.pid,
        rect=args.rect,
        preset=args.preset,
        colors_hsv=tuple(args.color_hsv),
        threshold=args.threshold,
        speaker=args.speaker,
        ocr_options=ocr_options,
        speech=speech,
    )
    print("Загрузка OCR и озвучки…" if args.speak else "Загрузка OCR…", file=sys.stderr)
    session = build_watch(config, bus)
    session.start()
    print("Слежу за зоной. Ctrl+C — выход.", file=sys.stderr)
    if args.speak and not args.no_hotkeys:
        print(
            "Горячие клавиши: пауза {pause}, повторить {repeat}, пропустить {skip}".format(
                **DEFAULT_HOTKEYS
            ),
            file=sys.stderr,
        )
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


def run_profiles(args: argparse.Namespace) -> int:
    paths = AppPaths.default()
    try:
        settings = load_settings(paths.settings_file)
    except ValueError as exc:
        print(f"settings.toml: {format_error(exc)}", file=sys.stderr)
        return 1
    bus = Bus()
    events: queue.Queue[Event] = queue.Queue()
    bus.subscribe(Event, events.put)
    print("Загрузка OCR и озвучки…", file=sys.stderr)
    session = build_run(settings, bus, paths, hotkeys=not args.no_hotkeys).session
    session.start()
    print(f"Профили: {paths.profiles_dir}. Жду игру в фокусе. Ctrl+C — выход.", file=sys.stderr)
    try:
        while True:
            try:
                event = events.get(timeout=0.5)
            except queue.Empty:
                continue
            report_event(event)
    except KeyboardInterrupt:
        return 0
    finally:
        session.stop()


def report_event(event: Event) -> None:
    match event:
        case UtteranceDetected():
            print_utterance(event)
        case GameActivated(profile_path=path, window=window, aspect=aspect):
            print(f"Игра: {window.exe} — профиль {Path(path).name} ({aspect})", file=sys.stderr)
        case LayoutMissing(title=title, aspect=aspect):
            print(f"Настройте зоны для этой игры: {title}, {aspect}", file=sys.stderr)
        case ProfileOffered(window=window):
            print(
                f"Игра {window.exe} без профиля. Создать: "
                f"uv run python -m subvoicer.cli profiles new {window.exe}",
                file=sys.stderr,
            )
        case ProfileBound(title=title):
            print(f"Игра {title} привязана", file=sys.stderr)
        case GameClosed():
            print("Игра закрыта.", file=sys.stderr)
        case CaptureFailed():
            print("Не удаётся захватить окно игры — продолжаю пытаться", file=sys.stderr)


def run_profiles_command(args: argparse.Namespace) -> int:
    store = pc.store_for()
    match args.action:
        case "list":
            for stored in store.load_all():
                p = stored.profile
                layouts = ", ".join(
                    f"{key}: {len(layout.zones)} зон" for key, layout in p.layouts.items()
                )
                print(f"{stored.path.name:<32} {p.title} [{layouts or 'нет зон'}]")
        case "new":
            stored = pc.new_profile(store, args.exe, args.title, args.install_dir)
            print(f"Создан {stored.path}")
        case "add-zone":
            stored = pc.find_profile(store, args.profile)
            aspect = args.aspect or pc.running_aspect(stored.profile.exe)
            if aspect is None:
                print("Игра не запущена: укажите --aspect, например 16:9", file=sys.stderr)
                return 1
            rect = args.rect
            stored, zone = pc.add_zone(
                store,
                str(stored.path),
                aspect=aspect,
                template=args.template,
                rect=(rect.x1, rect.y1, rect.x2, rect.y2),
                zone_id=args.id,
                overrides=zone_overrides(args),
            )
            print(f"Зона {zone.id} ({zone.name}) добавлена в {stored.path.name}, {aspect}")
        case "export":
            stored = pc.find_profile(store, args.profile)
            store.export(stored.path, args.file)
            print(f"Профиль сохранён в {args.file}")
        case "import":
            stored = store.import_file(args.file)
            print(f"Профиль импортирован: {stored.path}")
    return 0


def zone_overrides(args: argparse.Namespace) -> dict[str, object]:
    overrides: dict[str, object] = {}
    if args.name:
        overrides["name"] = args.name
    if args.preset or args.color_hsv or args.threshold is not None:
        mask: dict[str, object] = {"preset": args.preset or "color"}
        if args.color_hsv:
            mask["colors_hsv"] = args.color_hsv
        if args.threshold is not None:
            mask["threshold"] = args.threshold
        overrides["mask"] = mask
    policy: dict[str, object] = {}
    if args.priority:
        policy["priority"] = args.priority
    if args.voice_mode or args.voice:
        policy["voice"] = {"mode": args.voice_mode or "fixed", "id": args.voice}
    if args.speaker:
        speaker: dict[str, object] = {"mode": args.speaker}
        if args.speaker_zone:
            speaker["zone_id"] = args.speaker_zone
        if args.speaker_hsv:
            speaker["colors_hsv"] = args.speaker_hsv
        policy["speaker"] = speaker
    if args.cooldown is not None:
        policy["repeat_cooldown_min"] = args.cooldown
    if args.duck:
        policy["duck"] = args.duck == "yes"
    if policy:
        overrides["policy"] = policy
    return overrides


def run_characters_command(args: argparse.Namespace) -> int:
    store = pc.store_for()
    match args.action:
        case "list":
            stored = pc.find_profile(store, args.profile)
            for c in stored.profile.characters:
                aliases = f" ({', '.join(c.aliases)})" if c.aliases else ""
                locks = [
                    mark for mark, on in (("пол", c.gender_locked), ("голос", c.voice_locked)) if on
                ]
                fixed = f" закреплены: {', '.join(locks)}" if locks else ""
                print(f"{c.name}{aliases}: {c.gender}, {c.voice or '-'} [{c.source}]{fixed}")
        case "import":
            result = pc.import_characters_file(store, args.profile, args.file)
            report = result.report
            print(f"Профиль {result.stored.path.name}:")
            print(f"  добавлено: {len(report.added)} {', '.join(report.added)}")
            print(f"  обновлено: {len(report.updated)} {', '.join(report.updated)}")
            skipped = sorted([*result.problems, *report.skipped], key=lambda p: p.line)
            print(f"  пропущено: {len(skipped)}")
            for problem in skipped:
                print(f"    строка {problem.line}: {problem.text} — {problem.reason}")
        case "merge":
            stored = pc.merge_characters_in(store, args.profile, args.a, args.b)
            print(f"«{args.a}» теперь вариант имени «{args.b}» ({stored.path.name})")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    runtime.prepare_process()
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace", line_buffering=True)
    log_file = setup_logging(
        AppPaths.default().logs_dir,
        level=logging.DEBUG if getattr(args, "debug", False) else logging.INFO,
        console=True,
    )
    log.debug("Лог: %s", log_file)
    if args.command in ("watch", "run"):
        instance = runtime.acquire_single_instance()  # TZ 4.14: one voicing copy
        if instance is None:
            print("Subtitle Voicer уже запущен — второй экземпляр не нужен.", file=sys.stderr)
            return EXIT_ALREADY_RUNNING
        try:
            ModelSetup(AppPaths.default()).download_missing(ConsoleProgress())
        except ModelsError as exc:
            print(exc, file=sys.stderr)
            runtime.release_single_instance(instance)
            return 1
        try:
            return run_watch(args) if args.command == "watch" else run_profiles(args)
        finally:
            runtime.release_single_instance(instance)
    try:
        if args.command == "profiles":
            return run_profiles_command(args)
        if args.command == "characters":
            return run_characters_command(args)
    except (pc.ProfileNotFoundError, FileExistsError, KeyError) as exc:
        print(exc.args[0] if exc.args else exc, file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(format_error(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
