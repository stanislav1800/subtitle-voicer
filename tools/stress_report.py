"""Which words of a game the voice may say wrong: a report for the pronunciation dictionaries.

    uv run python tools/stress_report.py [LOG ...] [--min 2] [--out report.txt]

Takes the recognized lines from the log (``LATENCY change ... text=``, any game), prepares
them like the voicing does (numbers, Latin, stress marks by silero-stress) and lists words with
how often they occur:

- not in the dictionary of word forms (``models/words``): names, slang, game terms — or OCR
  garbage; with the stress silero-stress gave them;
- in the dictionary, but silero-stress put no stress mark on them (2+ vowels).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from subvoicer.config.models import model_path
from subvoicer.config.paths import AppPaths
from subvoicer.config.resources import letter_names, translit_table
from subvoicer.config.words import load_word_counts
from subvoicer.engines.tts.silero_stress import SileroStressMarker
from subvoicer.pipeline.text.latin import LatinReader
from subvoicer.pipeline.text.preparer import TextPreparer

TEXT = re.compile(r"LATENCY change .* text=(['\"])(.*)\1\s*$")
WORD = re.compile(r"[А-Яа-яЁё+]+(?:-[А-Яа-яЁё+]+)*")
VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")
NAME_PREFIX = re.compile(r"^[^:]{1,30}:\s*")


def lines_from(paths: list[Path], since: str = "") -> list[str]:
    found = []
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line[: len(since)] < since:
                continue  # log lines start with "YYYY-MM-DD HH:MM:SS"
            if m := TEXT.search(line):
                found.append(NAME_PREFIX.sub("", m.group(2)))
    return found


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("logs", nargs="*", type=Path)
    parser.add_argument("--min", type=int, default=1, help="слова, встретившиеся не реже")
    parser.add_argument("--out", type=Path, help="записать отчёт в файл")
    parser.add_argument(
        "--since", default="", help='только строки лога с этого времени, "2026-10-08 20:46"'
    )
    args = parser.parse_args()
    paths = AppPaths.default()
    logs = args.logs or sorted(paths.logs_dir.glob("subvoicer.log*"))
    texts = lines_from(logs, args.since)
    counts = load_word_counts(model_path(paths.models_dir, "words_ru"))
    stress = SileroStressMarker()
    stress.load()
    preparer = TextPreparer(stress, LatinReader(letter_names(), translit_table()))
    unknown: Counter[str] = Counter()
    unstressed: Counter[str] = Counter()
    voiced: dict[str, str] = {}
    for text in texts:
        for word in WORD.findall(" ".join(preparer.prepare(text))):
            plain = word.replace("+", "")
            key = plain.lower().replace("ё", "е")
            voiced.setdefault(key, word)
            if key not in counts:
                unknown[key] += 1
            elif "+" not in word and sum(ch in VOWELS for ch in plain) >= 2:
                unstressed[key] += 1
    out = [
        f"Реплик: {len(texts)}; слов вне словаря: {len(unknown)}, без ударения: {len(unstressed)}"
    ]
    out.append(
        "\n== Нет в словаре (имена, сленг, термины или мусор OCR): сколько раз — как озвучено"
    )
    out += [f"{n:4}  {voiced[w]}" for w, n in unknown.most_common() if n >= args.min]
    out.append("\n== В словаре, но silero-stress не поставил ударение")
    out += [f"{n:4}  {voiced[w]}" for w, n in unstressed.most_common() if n >= args.min]
    report = "\n".join(out)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
