"""Entry point of the installed app (TZ 1.24): ``SubtitleVoicer.exe`` built by PyInstaller."""

import os
import sys

# a windowed exe has no console: libraries that print must not fail on a missing stream
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

# started by the installer, the working folder is its temp folder that is deleted afterwards:
# work from the app's own folder
os.chdir(os.path.dirname(sys.executable))

from subvoicer.ui.app import main  # noqa: E402

raise SystemExit(main())
