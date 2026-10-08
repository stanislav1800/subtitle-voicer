"""The autostart command: the installed exe (TZ 1.24) or pythonw of the environment."""

from __future__ import annotations

import sys

import pytest

from subvoicer.app import runtime


def test_installed_app_starts_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    exe = r"C:\Users\u\AppData\Local\Programs\SubtitleVoicer\SubtitleVoicer.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", exe)
    assert runtime.autostart_command() == f'"{exe}" --autostart'


def test_source_checkout_runs_the_module() -> None:
    assert runtime.autostart_command().endswith('" -m subvoicer --autostart')
