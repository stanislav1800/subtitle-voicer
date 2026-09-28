"""Capture probe for the HDR check (TZ 4.1): what frames look like with HDR off and on.

Run the same paused scene twice — HDR off, then HDR on (Win+Alt+B or Settings > Display):
    uv run python tools/capture_probe.py --exe firefox.exe --label hdr_off
    uv run python tools/capture_probe.py --exe firefox.exe --label hdr_on
    uv run python tools/capture_probe.py --compare hdr_off hdr_on

Each run saves one frame from WGC and one from DXGI (window client area) as PNG plus a JSON
with the pixel format and colour statistics to ``captures/probe/<label>/``. ``--compare``
prints how much the colours differ between two runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "captures" / "probe"


def stats(image: np.ndarray) -> dict[str, Any]:
    bgr = image[:, :, :3].astype(np.float32)
    h, w = bgr.shape[:2]
    centre = bgr[h * 2 // 5 : h * 3 // 5, w * 2 // 5 : w * 3 // 5]
    bottom = bgr[int(h * 0.8) :, w // 5 : w * 4 // 5]  # where subtitles usually are
    return {
        "shape": list(image.shape),
        "dtype": str(image.dtype),
        "mean_bgr": [round(float(v), 1) for v in bgr.reshape(-1, 3).mean(axis=0)],
        "max": int(bgr.max()),
        "p99": float(np.percentile(bgr, 99)),
        "centre_mean_bgr": [round(float(v), 1) for v in centre.reshape(-1, 3).mean(axis=0)],
        "bottom_p99": float(np.percentile(bottom, 99)),
        "bottom_mean_bgr": [round(float(v), 1) for v in bottom.reshape(-1, 3).mean(axis=0)],
    }


def grab_wgc(hwnd: int) -> np.ndarray | None:
    from subvoicer.engines.capture.wgc import WgcFrameSource

    source = WgcFrameSource()
    source.start(hwnd)
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            frame = source.latest()
            if frame is not None:
                return frame.image
            time.sleep(0.05)
        return None
    finally:
        source.stop()


def grab_dxgi(hwnd: int) -> tuple[np.ndarray | None, str]:
    from windows_capture import DxgiDuplicationSession

    from subvoicer.engines.system import windows

    monitor = windows.monitor_of_window(hwnd)
    session = DxgiDuplicationSession(monitor.index)
    frame = None
    for _ in range(60):
        frame = session.acquire_frame(timeout_ms=100)
        if frame is not None:
            break
    if frame is None:
        return None, "?"
    image = frame.to_bgr()
    left, top, right, bottom = windows.client_rect_on_screen(hwnd)
    mx, my = monitor.rect[0], monitor.rect[1]
    return np.ascontiguousarray(
        image[top - my : bottom - my, left - mx : right - mx]
    ), frame.color_format


def probe(args: argparse.Namespace) -> int:
    from subvoicer.engines.system import windows

    windows.enable_dpi_awareness()
    found = windows.find_windows(args.exe, args.title)
    if len(found) != 1:
        print(f"Нужно ровно одно окно {args.exe}, найдено {len(found)}; уточните --title")
        for w in found:
            print(f"  {w.hwnd} {w.title}")
        return 2
    window = found[0]
    out = OUT / args.label
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"label": args.label, "window": window.title, "exe": window.exe}

    wgc = grab_wgc(window.hwnd)
    if wgc is not None:
        cv2.imencode(".png", wgc[:, :, :3])[1].tofile(str(out / "wgc.png"))
        report["wgc"] = stats(wgc)
    dxgi, color_format = grab_dxgi(window.hwnd)
    if dxgi is not None:
        cv2.imencode(".png", dxgi)[1].tofile(str(out / "dxgi.png"))
        report["dxgi"] = {"color_format": color_format, **stats(dxgi)}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"Сохранено в {out}")
    return 0


def compare(labels: list[str]) -> int:
    a, b = (json.loads((OUT / label / "report.json").read_text("utf-8")) for label in labels)
    for source in ("wgc", "dxgi"):
        if source not in a or source not in b:
            continue
        print(
            f"{source}: формат {a[source].get('color_format', 'bgra8')} -> "
            f"{b[source].get('color_format', 'bgra8')}"
        )
        for key in ("mean_bgr", "centre_mean_bgr", "bottom_mean_bgr"):
            diff = [round(y - x, 1) for x, y in zip(a[source][key], b[source][key], strict=True)]
            print(f"  {key}: {a[source][key]} -> {b[source][key]}  (разница {diff})")
        print(f"  bottom_p99: {a[source]['bottom_p99']} -> {b[source]['bottom_p99']}")
        img_a = cv2.imdecode(
            np.fromfile(OUT / labels[0] / f"{source}.png", np.uint8), cv2.IMREAD_COLOR
        )
        img_b = cv2.imdecode(
            np.fromfile(OUT / labels[1] / f"{source}.png", np.uint8), cv2.IMREAD_COLOR
        )
        if img_a is not None and img_b is not None and img_a.shape == img_b.shape:
            hsv_a = cv2.cvtColor(img_a, cv2.COLOR_BGR2HSV).astype(np.int16)
            hsv_b = cv2.cvtColor(img_b, cv2.COLOR_BGR2HSV).astype(np.int16)
            delta = np.abs(hsv_a - hsv_b).reshape(-1, 3)
            print(f"  средняя разница HSV по пикселям: {delta.mean(axis=0).round(1).tolist()}")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--exe")
    parser.add_argument("--title")
    parser.add_argument("--label", default="probe")
    parser.add_argument("--compare", nargs=2, metavar=("LABEL_A", "LABEL_B"))
    args = parser.parse_args()
    if args.compare:
        return compare(args.compare)
    if not args.exe:
        parser.error("нужен --exe (или --compare)")
    return probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
