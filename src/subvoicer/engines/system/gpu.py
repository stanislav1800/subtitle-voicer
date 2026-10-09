"""Video adapters of the computer, for the character voices (TZ 1.24, 1.25).

``nvidia-smi`` (it comes with the driver) tells whether the NVIDIA GPU can run CUDA; the app's
own torch is the CPU build and cannot tell. The other adapters (Radeon, Intel, built-in ones) are
read from Windows (WMI ``Win32_VideoController``) to show the user what was found.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

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


# drivers that are not a real GPU: Windows' fallback, remote desktop, virtual monitors
NOT_A_GPU = ("microsoft basic", "microsoft remote", "remote display", "virtual", "parsec", "citrix")
VENDORS = {
    "nvidia": "nvidia",
    "advanced micro": "amd",
    "amd": "amd",
    "radeon": "amd",
    "intel": "intel",
}


@dataclass(frozen=True, slots=True)
class Adapter:
    name: str
    vendor: str  # "nvidia", "amd", "intel" or "other"


def vendor_of(name: str, company: str = "") -> str:
    text = f"{company} {name}".lower()
    return next((v for key, v in VENDORS.items() if key in text), "other")


def video_adapters() -> list[Adapter]:
    """The GPUs Windows knows about, built-in ones too; [] if it cannot be asked."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return []

    def query() -> list[tuple[str, str]]:  # every COM object is released on return
        service = win32com.client.GetObject("winmgmts:")
        rows = service.ExecQuery("SELECT Name, AdapterCompatibility FROM Win32_VideoController")
        return [(str(r.Name or "").strip(), str(r.AdapterCompatibility or "")) for r in rows]

    pythoncom.CoInitialize()  # any thread; balanced below
    try:
        rows = query()
    except Exception:  # WMI broken or disabled: only the NVIDIA check is left
        log.warning("Не удалось получить список видеокарт из Windows", exc_info=True)
        rows = []
    finally:
        pythoncom.CoUninitialize()
    return [
        Adapter(name, vendor_of(name, company))
        for name, company in rows
        if name and not any(word in name.lower() for word in NOT_A_GPU)
    ]


# Radeons with torch for ROCm on Windows (AMD's matrix, ROCm 7.2.1: gfx1100, gfx1101, gfx1200,
# gfx1201): RX 7700–7900, PRO W7700–W7900, RX 9060–9070, AI PRO R9700. Not RX 7600 (gfx1102).
ROCM_GPU = re.compile(r"\b(?:RX\s*7[789]\d\d|W7[789]\d\d|RX\s*90[67]\d|R9700)\b", re.IGNORECASE)
WINDOWS_11_BUILD = 22000


def rocm_gpu(adapters: list[Adapter]) -> str | None:
    """The first Radeon that torch for ROCm supports on this Windows (11 only), or None."""
    if sys.platform != "win32" or sys.getwindowsversion().build < WINDOWS_11_BUILD:
        return None
    return next((a.name for a in adapters if a.vendor == "amd" and ROCM_GPU.search(a.name)), None)
