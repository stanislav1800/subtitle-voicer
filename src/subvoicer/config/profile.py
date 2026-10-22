"""Game profile: pydantic v2 models of the profile file (TZ 4.2, 4.5, 4.6, 5.4).

Unknown fields are validation errors, not silently dropped (TZ 5.4).
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from subvoicer.core.models import Gender

SCHEMA_VERSION = 8  # 2: ocr.fix_words, ocr.confusions (TZ 1.23); 3: one layout (TZ 1.26);
# 4: capture (TZ 1.28); 5: zone locate (TZ 1.29); 6: pause_in_editor (TZ 1.30); 7: speech (1.31);
# 8: voice mode gender (1.32)
MAX_ZONES = 12  # per layout (TZ 4.2)

Hsv = tuple[int, int, int]
HsvRange = tuple[Hsv, Hsv]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]

MaskPresetName = Literal["color", "bright_on_dark", "dark_on_bright", "adaptive"]
PriorityName = Literal["high", "normal", "low"]
VoiceModeName = Literal["speaker", "gender", "fixed", "narrator"]
SpeakerModeName = Literal["prefix", "zone", "color", "none"]
TriggerName = Literal["auto", "hotkey", "off"]
CharacterSource = Literal["auto", "import", "manual"]
# the window (WGC), the screen cut to the window (DXGI), or the window with the screen fallback
CaptureName = Literal["auto", "wgc", "dxgi"]
OverlapName = Literal["cut", "queue", "speedup", "adaptive"]  # TZ 4.9
# the text is in the zone's rect, or the rect is where subtitle plates are looked for (4.2.2)
LocateName = Literal["fixed", "plates"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _check_hsv(ranges: list[HsvRange]) -> list[HsvRange]:
    """OpenCV HSV: H 0..179, S and V 0..255 (TZ 4.2.1)."""
    for lo, hi in ranges:
        for h, s, v in (lo, hi):
            if not (0 <= h <= 179 and 0 <= s <= 255 and 0 <= v <= 255):
                raise ValueError(f"HSV вне диапазона OpenCV: {(h, s, v)}")
    return ranges


class MaskConfig(StrictModel):
    preset: MaskPresetName
    colors_hsv: list[HsvRange] = Field(default_factory=list)
    threshold: int | None = Field(default=None, ge=0, le=255)

    _hsv = field_validator("colors_hsv")(_check_hsv)

    @model_validator(mode="after")
    def _color_needs_ranges(self) -> Self:
        if self.preset == "color" and not self.colors_hsv:
            raise ValueError("Пресету color нужен хотя бы один диапазон colors_hsv")
        return self


class VoiceConfig(StrictModel):
    mode: VoiceModeName = "speaker"
    id: str | None = None

    @model_validator(mode="after")
    def _fixed_needs_id(self) -> Self:
        if self.mode == "fixed" and not self.id:
            raise ValueError("Для голоса fixed нужен id")
        return self


class SpeakerConfig(StrictModel):
    mode: SpeakerModeName = "none"
    zone_id: str | None = None  # mode "zone": the id of a zone of kind "speaker"
    colors_hsv: list[HsvRange] = Field(default_factory=list)  # mode "color"

    _hsv = field_validator("colors_hsv")(_check_hsv)

    @model_validator(mode="after")
    def _mode_fields(self) -> Self:
        if self.mode == "zone" and not self.zone_id:
            raise ValueError("Для говорящего zone нужен zone_id")
        if self.mode == "color" and not self.colors_hsv:
            raise ValueError("Для говорящего color нужен colors_hsv")
        return self


class VoicingPolicy(StrictModel):
    """What happens to a text after it is read, whatever the source (TZ 4.2, 4.17)."""

    priority: PriorityName = "normal"  # not used by hotkey zones (manual priority)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    speaker: SpeakerConfig = Field(default_factory=SpeakerConfig)
    repeat_cooldown_min: float = Field(default=0, ge=0)
    duck: bool = True


class StabilizationOverride(StrictModel):
    stable_ms: int | None = Field(default=None, ge=0)
    max_wait_ms: int | None = Field(default=None, ge=0)
    typing_max_wait_ms: int | None = Field(default=None, ge=0)
    match_ratio: float | None = Field(default=None, ge=0, le=100)
    change_ratio: float | None = Field(default=None, ge=0, le=1)
    empty_ratio: float | None = Field(default=None, ge=0, le=1)


class Stabilization(StrictModel):
    stable_ms: int = Field(default=200, ge=0)
    max_wait_ms: int = Field(default=1500, ge=0)
    typing_max_wait_ms: int = Field(default=5000, ge=0)
    match_ratio: float = Field(default=90, ge=0, le=100)
    change_ratio: float = Field(default=0.005, ge=0, le=1)
    empty_ratio: float = Field(default=0.001, ge=0, le=1)

    def merged(self, override: StabilizationOverride | None) -> Stabilization:
        if override is None:
            return self
        return self.model_copy(update=override.model_dump(exclude_none=True))


class SpeechOverride(StrictModel):
    """The game's own speech settings (TZ 4.9, 1.31); null — as in the general settings."""

    base_rate: float | None = Field(default=None, gt=0)
    overlap_mode: OverlapName | None = None
    hurry_rate: float | None = Field(default=None, ge=1)


Letter = Annotated[str, Field(min_length=1, max_length=1)]
Confusion = tuple[Letter, Letter]  # letters OCR mixes up in a game's font (TZ 1.23)


class OcrOverride(StrictModel):
    engine: str | None = None
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    upscale: float | None = Field(default=None, ge=1)
    fix_words: bool | None = None
    confusions: list[Confusion] | None = None


class OcrConfig(StrictModel):
    engine: str = "rapidocr"
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    upscale: float = Field(default=2.0, ge=1)
    fix_words: bool = True  # fix words by the dictionary of word forms (TZ 1.23)
    confusions: list[Confusion] = Field(default_factory=lambda: [("а", "о")])

    def merged(self, override: OcrOverride | None) -> OcrConfig:
        if override is None:
            return self
        return self.model_copy(update=override.model_dump(exclude_none=True))


class Zone(StrictModel):
    id: str = Field(min_length=1)
    name: str = ""
    kind: Literal["text", "speaker"] = "text"
    locate: LocateName = "fixed"  # TZ 4.2.2, 1.29
    rect: tuple[Fraction, Fraction, Fraction, Fraction]  # x1, y1, x2, y2 in frame fractions
    mask: MaskConfig
    trigger: TriggerName | None = None  # text zones only
    hotkey: str | None = None  # trigger "hotkey"
    policy: VoicingPolicy | None = None  # text zones only
    stabilization: StabilizationOverride | None = None
    ocr: OcrOverride | None = None

    @model_validator(mode="after")
    def _kind_fields(self) -> Self:
        x1, y1, x2, y2 = self.rect
        if x1 >= x2 or y1 >= y2:
            raise ValueError(f"Зона {self.id}: прямоугольник пустой {self.rect}")
        if self.kind == "speaker":
            if self.locate != "fixed":
                raise ValueError(f"Зона {self.id} вида speaker не ищет подложки")
            if self.trigger or self.hotkey or self.policy or self.stabilization or self.ocr:
                raise ValueError(f"Зона {self.id} вида speaker: только id, name, kind, rect, mask")
            return self
        if self.trigger is None:
            raise ValueError(f"Текстовой зоне {self.id} нужен trigger")
        if self.policy is None:
            raise ValueError(f"Текстовой зоне {self.id} нужна policy")
        if self.trigger == "hotkey" and not self.hotkey:
            raise ValueError(f"Зоне {self.id} с trigger hotkey нужен hotkey")
        return self


class Layout(StrictModel):
    zones: list[Zone] = Field(default_factory=list, max_length=MAX_ZONES)

    @model_validator(mode="after")
    def _references(self) -> Self:
        ids = [z.id for z in self.zones]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Повторяющиеся id зон: {ids}")
        speakers = {z.id for z in self.zones if z.kind == "speaker"}
        for zone in self.zones:
            speaker = zone.policy.speaker if zone.policy else None
            if speaker is not None and speaker.mode == "zone" and speaker.zone_id not in speakers:
                raise ValueError(
                    f"Зона {zone.id}: zone_id {speaker.zone_id!r} — нет такой зоны вида speaker"
                )
        return self


class Character(StrictModel):
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    gender: Gender = Gender.UNKNOWN
    gender_locked: bool = False
    voice: str | None = None
    voice_locked: bool = False
    source: CharacterSource = "auto"


class DefaultVoices(StrictModel):
    male: str
    female: str


class PronunciationRule(StrictModel):
    """``{"from": ..., "to": ..., "regex": false}`` (TZ 4.7.1)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", min_length=1)
    to: str
    regex: bool = False


class Profile(StrictModel):
    schema_version: int = SCHEMA_VERSION
    exe: str | None = None
    install_dir: str | None = None
    title: str = ""
    icon: str | None = None
    # zones in fractions of the frame, for a window of any size and aspect ratio (TZ 1.26)
    layout: Layout = Field(default_factory=Layout)
    capture: CaptureName = "auto"  # TZ 4.1, 1.28
    pause_in_editor: bool = True  # the game is paused while the zone editor is over it (1.30)
    speech: SpeechOverride = Field(default_factory=SpeechOverride)  # TZ 4.9, 1.31
    narrator_voice: str
    default_voices: DefaultVoices
    speaker_stoplist: list[str] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    pronunciation: list[PronunciationRule] = Field(default_factory=list)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    stabilization: Stabilization = Field(default_factory=Stabilization)
    hooks: None = None  # stage 11 (TZ 4.17)

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.exe is None and self.install_dir is None:
            raise ValueError("У профиля нужен exe или install_dir")
        return self

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2)
