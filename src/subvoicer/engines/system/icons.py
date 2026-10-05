"""Game icons and names from files (TZ 4.16): the icon of an exe through the Windows API,
cached as PNG; ``ProductName`` from the exe's version info. No internet."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

ICON_SIZE = 64
DI_NORMAL = 3
DIB_RGB_COLORS = 0


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def exe_icon(exe: Path, size: int = ICON_SIZE) -> Image.Image | None:
    """The exe's main icon at ``size`` px with its alpha channel, or None."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    user32.DrawIconEx.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HICON,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.HBRUSH,
        wintypes.UINT,
    ]
    icon = wintypes.HICON()
    icon_id = wintypes.UINT()
    count = user32.PrivateExtractIconsW(
        str(exe), 0, size, size, ctypes.byref(icon), ctypes.byref(icon_id), 1, 0
    )
    if count < 1 or not icon:
        return None
    dc = gdi32.CreateCompatibleDC(None)
    header = _BitmapInfoHeader()
    header.biSize = ctypes.sizeof(header)
    header.biWidth, header.biHeight = size, -size  # top-down
    header.biPlanes, header.biBitCount = 1, 32
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(
        dc, ctypes.byref(header), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
    )
    try:
        if not bitmap or not bits:
            return None
        previous = gdi32.SelectObject(dc, bitmap)
        user32.DrawIconEx(dc, 0, 0, icon, size, size, 0, None, DI_NORMAL)
        gdi32.SelectObject(dc, previous)
        data = bytearray(ctypes.string_at(bits, size * size * 4))
        if not any(data[3::4]):  # an icon without alpha: opaque
            data[3::4] = b"\xff" * (size * size)
        return Image.frombuffer("RGBA", (size, size), bytes(data), "raw", "BGRA", 0, 1)
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(dc)
        user32.DestroyIcon(icon)


def save_icon(source: Path, target: Path, size: int = ICON_SIZE) -> bool:
    """``source`` — an exe (its icon) or a picture (Steam's cached icon) -> PNG ``target``."""
    try:
        if source.suffix.lower() == ".exe":
            image = exe_icon(source, size)
        else:
            with Image.open(source) as opened:
                image = opened.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        if image is None:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, "PNG")
        return True
    except Exception:
        log.warning("Иконка из %s не сохранена", source, exc_info=True)
        return False


def product_name(exe: Path) -> str | None:
    """``ProductName`` from the version info of the exe (TZ 4.16), or None."""
    import win32api

    try:
        translations = win32api.GetFileVersionInfo(str(exe), "\\VarFileInfo\\Translation")
        for lang, codepage in translations or []:
            block = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\ProductName"
            name = win32api.GetFileVersionInfo(str(exe), block)
            if name and str(name).strip():
                return str(name).strip()
    except Exception:
        log.debug("Нет ProductName у %s", exe, exc_info=True)
    return None
