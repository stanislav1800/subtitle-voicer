"""Profile format migrations (TZ 4.11): a chain of pure functions ``vN -> vN+1`` over dicts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from subvoicer.config.profile import SCHEMA_VERSION

Data = dict[str, Any]
Migration = Callable[[Data], Data]


def _v1_to_v2(data: Data) -> Data:
    """TZ 1.23: words are fixed by the dictionary in every game, the а/о pair by default."""
    ocr = dict(data.get("ocr") or {})
    ocr.setdefault("fix_words", True)
    ocr.setdefault("confusions", [["а", "о"]])
    data["ocr"] = ocr
    return data


def _v2_to_v3(data: Data) -> Data:
    """TZ 1.26: one layout for every aspect ratio instead of one per ratio. The layout with
    the most zones is kept (on a tie — 16:9, then the first by key); the file before the
    migration stays in profiles/backup with the next save."""
    layouts: dict[str, Any] = dict(data.pop("layouts", None) or {})

    def rank(key: str) -> tuple[int, int, str]:
        zones = (layouts[key] or {}).get("zones") or []
        return (-len(zones), 0 if key == "16:9" else 1, key)

    data["layout"] = layouts[min(layouts, key=rank)] if layouts else {"zones": []}
    return data


def _v3_to_v4(data: Data) -> Data:
    """TZ 1.28: the capture mode is set in the profile; old profiles keep the window capture
    with the screen fallback, as before."""
    data.setdefault("capture", "auto")
    return data


def _v4_to_v5(data: Data) -> Data:
    """TZ 1.29: zones may look for subtitle plates; the old ones keep their fixed rect."""
    layout = dict(data.get("layout") or {})
    layout["zones"] = [{"locate": "fixed", **zone} for zone in layout.get("zones") or []]
    data["layout"] = layout
    return data


def _v5_to_v6(data: Data) -> Data:
    """TZ 1.30: the game is paused while the zone editor is open over it."""
    data.setdefault("pause_in_editor", True)
    return data


def _v6_to_v7(data: Data) -> Data:
    """TZ 1.31: the game's own speech settings, none yet — as in the general settings."""
    data.setdefault("speech", {})
    return data


# from version N to N+1; every entry has a unit test on an example of the old file
MIGRATIONS: dict[int, Migration] = {
    1: _v1_to_v2,
    2: _v2_to_v3,
    3: _v3_to_v4,
    4: _v4_to_v5,
    5: _v5_to_v6,
    6: _v6_to_v7,
}


class ProfileFormatError(ValueError):
    """The file cannot be read as a profile of any known version."""


class ProfileTooNewError(ProfileFormatError):
    """Made by a newer version of the app: neither loaded nor overwritten."""


def schema_version(data: Mapping[str, Any]) -> int:
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise ProfileFormatError(f"schema_version должен быть целым числом, а не {version!r}")
    return version


def migrate(
    data: Data,
    current: int = SCHEMA_VERSION,
    migrations: Mapping[int, Migration] = MIGRATIONS,
) -> Data:
    """Bring ``data`` up to ``current``; the input dict is not modified."""
    version = schema_version(data)
    if version > current:
        raise ProfileTooNewError(
            f"Профиль создан более новой версией (формат {version}, известен до {current})"
        )
    result = dict(data)
    while version < current:
        step = migrations.get(version)
        if step is None:
            raise ProfileFormatError(f"Нет миграции профиля с версии {version}")
        result = step(dict(result))
        version += 1
        result["schema_version"] = version
    return result
