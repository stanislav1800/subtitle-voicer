"""Profile and character commands for the CLI (stage 5: profiles are edited as JSON + these)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from subvoicer.config.characters_csv import CsvProblem, parse_characters
from subvoicer.config.paths import AppPaths
from subvoicer.config.profile import DefaultVoices, Layout, Profile, Zone
from subvoicer.config.profile_store import ProfileStore, StoredProfile, same_exe
from subvoicer.config.resources import read_toml, speaker_stoplist, voice_catalog
from subvoicer.pipeline.speaker.characters import (
    ImportReport,
    import_characters,
    merge_characters,
)
from subvoicer.pipeline.speech.voices import default_voices
from subvoicer.pipeline.text.skip import skip_key

TTS_CATALOG = "silero_v5"


class ProfileNotFoundError(LookupError):
    pass


def store_for(paths: AppPaths | None = None) -> ProfileStore:
    return ProfileStore((paths or AppPaths.default()).profiles_dir)


def find_profile(store: ProfileStore, ref: str) -> StoredProfile:
    """``ref``: a profile file path, a file name (with or without ``.json``) or an exe name."""
    candidate = Path(ref)
    if candidate.suffix == ".json" and candidate.exists():
        return StoredProfile(candidate, store.load(candidate))
    profiles = store.load_all()
    for stored in profiles:
        if stored.path.name in (ref, f"{ref}.json") or stored.path.stem == ref:
            return stored
    matches = [
        p for p in profiles if same_exe(p.profile.exe, ref) or same_exe(p.profile.exe, f"{ref}.exe")
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = ", ".join(p.path.name for p in matches)
        raise ProfileNotFoundError(f"Профилей с exe {ref} несколько ({names}): укажите файл")
    raise ProfileNotFoundError(f"Профиль «{ref}» не найден в {store.directory}")


def new_profile(
    store: ProfileStore,
    exe: str | None,
    title: str | None = None,
    install_dir: str | None = None,
    icon: str | None = None,
) -> StoredProfile:
    """Voices by TZ 4.6 from the voice catalog, the default speaker stop-list and the zone
    «Субтитры (авто)» (TZ 4.2.2): nothing to set up for a game with subtitle plates.
    ``exe`` is None for a Steam game: it is bound on the first run (4.11)."""
    profiles = store.load_all()
    if exe is not None and install_dir is None and store.match(profiles, exe, None) is not None:
        raise FileExistsError(f"Профиль для {exe} уже есть")
    defaults = default_voices(list(voice_catalog(TTS_CATALOG).values()))
    profile = Profile(
        exe=exe,
        install_dir=install_dir,
        icon=icon,
        title=title or (Path(exe).stem if exe else Path(install_dir or "").name),
        narrator_voice=defaults.narrator,
        default_voices=DefaultVoices(male=defaults.male, female=defaults.female),
        speaker_stoplist=speaker_stoplist(),
        layout=Layout(zones=[zone_from_template(AUTO_TEMPLATE, AUTO_RECT, set())]),
    )
    return store.save(profile, store.path_for(profile, profiles))


AUTO_TEMPLATE = "subtitles_auto"
AUTO_RECT = (0.0, 0.55, 1.0, 1.0)  # where subtitle plates are looked for (TZ 4.2.2)


def zone_templates() -> dict[str, dict[str, Any]]:
    return read_toml("zone_templates.toml")


def zone_from_template(
    template: str,
    rect: tuple[float, float, float, float],
    taken: set[str],
    *,
    zone_id: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> Zone:
    """A zone from a template (TZ 4.2) with an id not in ``taken``; ``overrides`` replaces
    top-level fields such as mask and is merged into ``policy``."""
    templates = zone_templates()
    if template not in templates:
        raise KeyError(f"Нет шаблона {template!r} (есть: {', '.join(templates)})")
    data = dict(templates[template])
    for key, value in (overrides or {}).items():
        if key == "policy" and isinstance(value, dict):
            data["policy"] = {**data.get("policy", {}), **value}
        else:
            data[key] = value
    base = zone_id or template
    new_id = base
    counter = 2
    while new_id in taken:
        new_id, counter = f"{base}{counter}", counter + 1
    return Zone.model_validate({"id": new_id, **data, "rect": rect})


def add_zone(
    store: ProfileStore,
    ref: str,
    *,
    template: str,
    rect: tuple[float, float, float, float],
    zone_id: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[StoredProfile, Zone]:
    """A zone from a template (TZ 4.2); ``overrides`` replaces top-level fields such as mask
    and is merged into ``policy``."""
    stored = find_profile(store, ref)
    layout = stored.profile.layout
    zone = zone_from_template(
        template, rect, {z.id for z in layout.zones}, zone_id=zone_id, overrides=overrides
    )
    profile = stored.profile.model_copy(update={"layout": Layout(zones=[*layout.zones, zone])})
    return store.save(profile, stored.path), zone


@dataclass(frozen=True)
class CharactersImport:
    stored: StoredProfile
    report: ImportReport
    problems: list[CsvProblem]


def import_characters_file(store: ProfileStore, ref: str, source: Path) -> CharactersImport:
    return import_characters_text(store, ref, source.read_text(encoding="utf-8-sig"))


def import_characters_text(store: ProfileStore, ref: str, text: str) -> CharactersImport:
    """A file or a pasted text (TZ 4.6)."""
    rows, problems = parse_characters(text)
    target = find_profile(store, ref)
    report_box: list[ImportReport] = []

    def change(profile: Profile) -> Profile | None:
        characters, report = import_characters(profile.characters, rows)
        report_box.append(report)
        if not report.added and not report.updated:
            return None
        return profile.model_copy(update={"characters": characters})

    stored = store.update(target.path, change)
    return CharactersImport(stored, report_box[-1], problems)


def merge_characters_in(store: ProfileStore, ref: str, source: str, target: str) -> StoredProfile:
    stored = find_profile(store, ref)

    def change(profile: Profile) -> Profile:
        try:
            merged = merge_characters(profile.characters, source, target)
        except KeyError as exc:
            raise ProfileNotFoundError(f"Персонаж «{exc.args[0]}» не найден") from None
        return profile.model_copy(update={"characters": merged})

    return store.update(stored.path, change)


def with_skip_phrase(profile: Profile, phrase: str) -> Profile | None:
    """The profile with ``phrase`` in «Не озвучивать» (TZ 1.33); None if it is there already."""
    phrase = phrase.strip()
    if not skip_key(phrase) or any(skip_key(p) == skip_key(phrase) for p in profile.skip_phrases):
        return None
    return profile.model_copy(update={"skip_phrases": [*profile.skip_phrases, phrase]})
