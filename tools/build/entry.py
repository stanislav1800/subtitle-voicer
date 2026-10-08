"""Entry point of the installed app (TZ 1.24): ``SubtitleVoicer.exe`` built by PyInstaller."""

import os
import sys

# a windowed exe has no console: libraries that print must not fail on a missing stream
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

from subvoicer.ui.app import main

raise SystemExit(main())
