"""TextPreparer: the order of TZ 4.7 steps. Step 1 (removing the speaker name) is done by
SpeakerResolver before the text gets here."""

from __future__ import annotations

from subvoicer.core.ports import StressMarker
from subvoicer.pipeline.text.cleanup import clean
from subvoicer.pipeline.text.latin import LatinReader
from subvoicer.pipeline.text.numbers import numbers_to_words
from subvoicer.pipeline.text.pronunciation import PronunciationDict
from subvoicer.pipeline.text.sentences import split_sentences
from subvoicer.pipeline.text.stress import add_stress


class TextPreparer:
    def __init__(
        self,
        stress: StressMarker,
        latin: LatinReader,
        dictionary: PronunciationDict | None = None,
    ) -> None:
        self._stress = stress
        self._latin = latin
        self.dictionary = dictionary or PronunciationDict()

    def prepare(self, text: str, dictionary: PronunciationDict | None = None) -> list[str]:
        """Text ready for the TTS engine, as one or more pieces to synthesize in turn."""
        text = (dictionary or self.dictionary).apply(text)  # 2
        text = numbers_to_words(text)  # 3
        text = self._latin.read(text)  # 4
        text = add_stress(text, self._stress)  # 5
        text = clean(text)  # 6
        return split_sentences(text)  # 7
