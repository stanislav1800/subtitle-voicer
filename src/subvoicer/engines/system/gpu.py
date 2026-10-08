"""Is there an NVIDIA GPU for the character voices (TZ 1.24)? ``nvidia-smi`` comes with the
driver; the app's own torch is the CPU build and cannot tell."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def nvidia_gpu() -> str | None:
    """The name of the first NVIDIA GPU, or None."""
    tool = shutil.which("nvidia-smi")
    if tool is None:
        return None
    try:
        out = subprocess.run(
            [tool, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        log.warning("nvidia-smi не ответил")
        return None
    names = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    return names[0] if out.returncode == 0 and names else None
