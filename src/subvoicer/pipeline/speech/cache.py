"""In-memory cache of synthesized phrases (TZ 4.8): key = (text, voice, rate)."""

from __future__ import annotations

from collections import OrderedDict

from subvoicer.core.models import AudioClip

CacheKey = tuple[str, str, float]


class SpeechCache:
    def __init__(self, size: int = 200) -> None:
        self.size = size
        self._items: OrderedDict[CacheKey, AudioClip] = OrderedDict()

    def get(self, text: str, voice_id: str, rate: float) -> AudioClip | None:
        key = (text, voice_id, round(rate, 3))
        clip = self._items.get(key)
        if clip is not None:
            self._items.move_to_end(key)
        return clip

    def put(self, text: str, voice_id: str, rate: float, clip: AudioClip) -> None:
        key = (text, voice_id, round(rate, 3))
        self._items[key] = clip
        self._items.move_to_end(key)
        while len(self._items) > self.size:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)
