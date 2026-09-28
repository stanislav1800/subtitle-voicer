"""Stack smoke test (stage 0): imports, OCR of a generated Russian image, one Silero phrase.

Run: uv run python tools/smoke_stack.py [--out smoke.wav] [--no-play]

Models are fetched by the libraries' own mechanisms (TZ 4.14): RapidOCR caches its
models inside the package, Silero downloads into the ``silero`` package folder.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
import traceback
import wave
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# (distribution on PyPI, module to import) — TZ section 7, without `vdf` (stage 9)
STACK: list[tuple[str, str]] = [
    ("windows-capture", "windows_capture"),
    ("opencv-python-headless", "cv2"),
    ("numpy", "numpy"),
    ("pillow", "PIL"),
    ("rapidocr", "rapidocr"),
    ("onnxruntime", "onnxruntime"),
    ("winrt-Windows.Media.Ocr", "winrt.windows.media.ocr"),
    ("silero", "silero"),
    ("silero-stress", "silero_stress"),
    ("torch", "torch"),
    ("rapidfuzz", "rapidfuzz"),
    ("num2words", "num2words"),
    ("sounddevice", "sounddevice"),
    ("pycaw", "pycaw.pycaw"),
    ("comtypes", "comtypes"),
    ("psutil", "psutil"),
    ("pywin32", "win32api"),
    ("pynput", "pynput"),
    ("PySide6", "PySide6.QtWidgets"),
    ("pydantic", "pydantic"),
    ("tomlkit", "tomlkit"),
    ("platformdirs", "platformdirs"),
]

OCR_TEXT = "Джуди: Встретимся у Арасаки в 23:00, не опаздывай!"
TTS_TEXT = "Привет! Это проверка синтеза речи. Встретимся у моста через пять минут."
FONT_CANDIDATES = ["arial.ttf", "segoeui.ttf", "tahoma.ttf", "verdana.ttf"]
SAMPLE_RATE = 48_000


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def check_imports() -> bool:
    section("Импорт зависимостей")
    ok = True
    for dist, module in STACK:
        try:
            importlib.import_module(module)
            try:
                ver = version(dist)
            except PackageNotFoundError:
                ver = "?"
            print(f"  OK    {dist:<26} {ver}")
        except Exception as exc:
            ok = False
            print(f"  FAIL  {dist:<26} {type(exc).__name__}: {exc}")
    import torch

    print(f"\n  torch: {torch.__version__}, CUDA-сборка: {torch.version.cuda or 'нет (CPU)'}")
    return ok


def render_text_image(text: str):  # -> numpy BGR image
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    fonts_dir = Path("C:/Windows/Fonts")
    font_path = next((fonts_dir / f for f in FONT_CANDIDATES if (fonts_dir / f).exists()), None)
    if font_path is None:
        raise FileNotFoundError(f"Нет ни одного шрифта из {FONT_CANDIDATES} в {fonts_dir}")
    font = ImageFont.truetype(str(font_path), 40)
    image = Image.new("RGB", (1200, 120), (20, 20, 20))
    ImageDraw.Draw(image).text((30, 35), text, font=font, fill=(255, 255, 255))
    print(f"  шрифт: {font_path.name}")
    return np.asarray(image)[:, :, ::-1].copy()  # RGB -> BGR, как у OpenCV


def check_ocr() -> bool:
    section("OCR: RapidOCR, PP-OCRv5 cyrillic mobile, CPU")
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    image = render_text_image(OCR_TEXT)
    engine = RapidOCR(
        params={
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Det.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.lang_type": LangRec.CYRILLIC,
            "Rec.model_type": ModelType.MOBILE,
        }
    )
    engine(image)  # прогрев
    started = time.perf_counter()
    result = engine(image)
    elapsed_ms = (time.perf_counter() - started) * 1000
    texts = list(result.txts or ())
    scores = list(result.scores or ())
    print(f"  ожидалось:   {OCR_TEXT}")
    print(f"  распознано:  {' '.join(texts)}")
    print(f"  уверенность: {[round(s, 3) for s in scores]}")
    print(f"  время:       {elapsed_ms:.0f} мс (после прогрева)")
    return bool(texts)


def write_wav(path: Path, samples, sample_rate: int) -> None:
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def check_tts(out: Path, play: bool) -> bool:
    section("TTS: silero-stress + Silero v5_cis_base, CPU")
    import torch
    from silero import silero_tts
    from silero_stress import load_accentor

    torch.set_grad_enabled(False)
    accentor = load_accentor()
    stressed = accentor(TTS_TEXT)
    print(f"  ударения: {stressed}")

    model, _example = silero_tts(language="ru", speaker="v5_cis_base")
    model.to(torch.device("cpu"))
    speakers = list(getattr(model, "speakers", []))
    ru_speakers = [s for s in speakers if s.startswith("ru_")]
    print(f"  голосов в модели: {len(speakers)}, из них ru_*: {len(ru_speakers)}")
    for name in speakers:
        print(f"    {name}")
    if not ru_speakers:
        print("  FAIL: модель не отдала ни одного голоса ru_*")
        return False

    speaker = ru_speakers[0]
    started = time.perf_counter()
    audio = model.apply_tts(text=stressed, speaker=speaker, sample_rate=SAMPLE_RATE)
    elapsed = time.perf_counter() - started
    samples = audio.numpy()
    duration = len(samples) / SAMPLE_RATE
    write_wav(out, samples, SAMPLE_RATE)
    print(f"  голос {speaker}: {duration:.2f} с звука за {elapsed:.2f} с -> {out.resolve()}")
    if play:
        import sounddevice as sd

        device = sd.query_devices(kind="output")["name"]
        print(f"  воспроизведение на устройстве по умолчанию: {device}")
        sd.play(samples, SAMPLE_RATE)
        sd.wait()
    return duration > 0.5


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        # построчный вывод: иначе при запуске не из консоли текст появляется только в конце
        sys.stdout.reconfigure(errors="replace", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("smoke.wav"), help="куда записать wav")
    parser.add_argument("--no-play", action="store_true", help="не проигрывать фразу")
    args = parser.parse_args()

    print(f"Python {sys.version}")
    print("Первый запуск скачивает модели (~100 МБ), дальше — около 10 секунд.")
    results: dict[str, bool] = {}
    for name, check in [
        ("импорт", check_imports),
        ("OCR", check_ocr),
        ("TTS", lambda: check_tts(args.out, play=not args.no_play)),
    ]:
        try:
            results[name] = check()
        except Exception:
            traceback.print_exc()
            results[name] = False

    section("Итог")
    for name, ok in results.items():
        print(f"  {'OK  ' if ok else 'FAIL'}  {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
