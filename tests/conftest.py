from __future__ import annotations

import sys

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform == "win32":
        return
    skip = pytest.mark.skip(reason="требуется Windows")
    for item in items:
        if "windows" in item.keywords:
            item.add_marker(skip)
