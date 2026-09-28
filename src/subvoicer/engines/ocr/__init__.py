"""ocr engine family: engine id -> 'module:Class' (see engines/registry.py)."""

OCR: dict[str, str] = {
    "rapidocr": "subvoicer.engines.ocr.rapid:RapidOcrEngine",
    "winrt": "subvoicer.engines.ocr.winrt:WinRtOcrEngine",
}
