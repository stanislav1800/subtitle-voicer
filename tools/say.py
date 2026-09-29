"""Say a phrase through the whole voicing path: text preparation (TZ 4.7) + TTS + player.

    uv run python tools/say.py "Встретимся в 22:30 у NCPD, это 3-й раз!"
    uv run python tools/say.py "У Арасаки новый план" --dict tools/data/dict_example.json
    uv run python tools/say.py "Быстрее!" --rate 1.3 --voice ru_aigul
    uv run python tools/say.py --voices

Prints the prepared text (with stresses) and plays it on the default output device.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from subvoicer.app.container import load_dictionary
from subvoicer.config.resources import letter_names, translit_table, voice_catalog
from subvoicer.engines.audio.output import SoundDeviceOutput
from subvoicer.engines.tts.silero import SileroEngine
from subvoicer.engines.tts.silero_stress import SileroStressMarker
from subvoicer.pipeline.speech.voices import default_voices
from subvoicer.pipeline.text.latin import LatinReader
from subvoicer.pipeline.text.preparer import TextPreparer


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace", line_buffering=True)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("text", nargs="?", help="что сказать")
    parser.add_argument("--dict", type=Path, help="словарь произношений (JSON)")
    parser.add_argument("--voice", help="id голоса (по умолчанию — рассказчик)")
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--voices", action="store_true", help="показать голоса и выйти")
    args = parser.parse_args()

    engine = SileroEngine(catalog=voice_catalog("silero_v5"))
    engine.load()
    if args.voices:
        for v in engine.voices():
            print(f"{v.id:<16} {v.gender.value:<8} {v.display_name}")
        return 0
    if not args.text:
        parser.error("нужен текст")
    stress = SileroStressMarker()
    stress.load()
    preparer = TextPreparer(
        stress, LatinReader(letter_names(), translit_table()), load_dictionary(args.dict)
    )
    voice = args.voice or default_voices(engine.voices()).narrator
    pieces = preparer.prepare(args.text)
    print(f"Голос: {voice}, скорость {args.rate}")
    for piece in pieces:
        print(f"  {piece}")

    output = SoundDeviceOutput()
    done = False

    def finished(_line: int, _completed: bool) -> None:
        nonlocal done
        done = True

    output.set_listeners(lambda _l, _t: None, finished)
    output.load()
    try:
        for i, piece in enumerate(pieces):
            clip = engine.synthesize(piece, voice, args.rate)
            if i == 0:
                output.start_line(1, clip)
            else:
                output.feed(1, clip)
        output.finish_feeding(1)
        while not done:
            time.sleep(0.05)
    finally:
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
