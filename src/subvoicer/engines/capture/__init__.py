"""capture engine family: engine id -> 'module:Class' (see engines/registry.py)."""

CAPTURE: dict[str, str] = {
    "wgc": "subvoicer.engines.capture.wgc:WgcFrameSource",
    "dxgi": "subvoicer.engines.capture.dxgi:DxgiFrameSource",
}
