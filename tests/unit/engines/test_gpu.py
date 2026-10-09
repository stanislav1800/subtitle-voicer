"""Which vendor an adapter Windows reports belongs to (TZ 1.25)."""

from __future__ import annotations

import pytest

from subvoicer.engines.system.gpu import vendor_of


@pytest.mark.parametrize(
    ("name", "company", "vendor"),
    [
        ("NVIDIA GeForce RTX 3070", "NVIDIA", "nvidia"),
        ("AMD Radeon RX 6600", "Advanced Micro Devices, Inc.", "amd"),
        ("AMD Radeon(TM) Graphics", "", "amd"),
        ("Intel(R) UHD Graphics 630", "Intel Corporation", "intel"),
        ("Some Adapter", "", "other"),
    ],
)
def test_vendor_of(name: str, company: str, vendor: str) -> None:
    assert vendor_of(name, company) == vendor


@pytest.mark.parametrize(
    ("name", "supported"),
    [
        ("AMD Radeon RX 7900 XTX", True),
        ("AMD Radeon RX 7800 XT", True),
        ("AMD Radeon RX 9070 XT", True),
        ("AMD Radeon RX 9060 XT", True),
        ("AMD Radeon AI PRO R9700", True),
        ("AMD Radeon PRO W7900", True),
        ("AMD Radeon RX 7600", False),
        ("AMD Radeon RX 6600", False),
        ("AMD Radeon(TM) Graphics", False),
    ],
)
def test_rocm_gpu(name: str, supported: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from subvoicer.engines.system import gpu

    monkeypatch.setattr(sys, "platform", "win32")
    if not hasattr(sys, "getwindowsversion"):
        pytest.skip("Windows only")
    found = gpu.rocm_gpu([gpu.Adapter("Intel(R) UHD Graphics", "intel"), gpu.Adapter(name, "amd")])
    assert (found == name) == supported
