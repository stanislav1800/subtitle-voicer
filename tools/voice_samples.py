"""Voice samples and the gender catalog (TZ 4.6).

    uv run python tools/voice_samples.py            # samples/<id>.wav + voices catalog
    uv run python tools/voice_samples.py --label    # play each voice and ask its gender

The voice ids come from the loaded Silero model. The catalog
``src/subvoicer/resources/voices/silero_v5.toml`` is created with ``gender = "unknown"``;
genders already set there are kept. With ``--label`` each sample is played and the answer
(м / ж / Enter — skip, п — play again, q — stop) is saved right away.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np
import tomlkit

from subvoicer.config.resources import resource_path, voice_catalog
from subvoicer.core.models import Gender, Voice
from subvoicer.engines.tts.silero import SileroEngine
from subvoicer.engines.tts.silero_stress import SileroStressMarker

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
CATALOG = "silero_v5"
PHRASE = "Привет! Я буду озвучивать реплики персонажей. Как тебе мой голос?"
ANSWERS = {"м": Gender.MALE, "m": Gender.MALE, "ж": Gender.FEMALE, "f": Gender.FEMALE}


def catalog_file() -> Path:
    return resource_path(f"voices/{CATALOG}.toml")


def write_catalog(voices: list[Voice]) -> None:
    doc = tomlkit.document()
    doc.add(tomlkit.comment("Каталог голосов Silero v5_cis_base (ТЗ 4.6)."))
    doc.add(tomlkit.comment("id берутся из модели; пол проставляет пользователь:"))
    doc.add(tomlkit.comment("uv run python tools/voice_samples.py --label"))
    table = tomlkit.aot()
    for v in sorted(voices, key=lambda v: v.id):
        item = tomlkit.table()
        item.add("id", v.id)
        item.add("name", v.display_name)
        item.add("gender", v.gender.value)
        table.append(item)
    doc.add("voice", table)
    path = catalog_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8", newline="\n")


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as f:
        data = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
        return data.astype(np.float32) / 32767, f.getframerate()


def generate() -> list[Voice]:
    known = voice_catalog(CATALOG)
    engine = SileroEngine(catalog=known)
    engine.load()
    marker = SileroStressMarker()
    marker.load()
    SAMPLES.mkdir(exist_ok=True)
    voices = engine.voices()
    for voice in voices:
        text = marker.mark(PHRASE)
        clip = engine.synthesize(text, voice.id, 1.0)
        write_wav(SAMPLES / f"{voice.id}.wav", clip.samples, clip.sample_rate)
        print(f"  {voice.id:<16} {voice.gender.value:<8} {clip.duration:.1f} с")
    write_catalog(voices)
    print(f"\nОбразцы: {SAMPLES}\nКаталог: {catalog_file()}")
    return voices


def label(voices: list[Voice]) -> None:
    import sounddevice as sd

    current = {v.id: v for v in voices}
    todo = [v for v in voices if v.gender is Gender.UNKNOWN]
    print(
        f"\nРазметка: {len(todo)} голосов без пола. м — мужской, ж — женский, "
        "Enter — пропустить, п — ещё раз, q — выход."
    )
    for n, voice in enumerate(todo, 1):
        samples, rate = read_wav(SAMPLES / f"{voice.id}.wav")
        while True:
            sd.play(samples, rate)
            answer = input(f"[{n}/{len(todo)}] {voice.id}: ").strip().lower()
            sd.stop()
            if answer in ("п", "p"):
                continue
            break
        if answer in ("q", "й"):
            break
        gender = ANSWERS.get(answer)
        if gender is not None:
            current[voice.id] = Voice(voice.id, gender, voice.display_name)
            write_catalog(list(current.values()))
    counts = {g: sum(v.gender is g for v in current.values()) for g in Gender}
    print(
        f"\nИтого: мужских {counts[Gender.MALE]}, женских {counts[Gender.FEMALE]}, "
        f"без пола {counts[Gender.UNKNOWN]}. Каталог: {catalog_file()}"
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace", line_buffering=True)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--label", action="store_true", help="прослушать и проставить пол")
    args = parser.parse_args()
    if (
        args.label
        and all((SAMPLES / f"{v}.wav").exists() for v in voice_catalog(CATALOG))
        and voice_catalog(CATALOG)
    ):
        voices = list(voice_catalog(CATALOG).values())
    else:
        print("Синтез образцов…")
        voices = generate()
    if args.label:
        label(voices)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
