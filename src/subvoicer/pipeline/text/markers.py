"""Game HUD read along with a subtitle (TZ 4.3): the distance to a quest marker («20M») sits
under the subtitle in Cyberpunk 2077 and has the colour of the text."""

from __future__ import annotations

import re

# «Поможешь? 30M», «подвернулось? 0 20M»: only after the end of a sentence, so «20 м» of a
# line that goes on is not cut
DISTANCE = re.compile(r"(?<=[.!?…»\"])(?:\s+\d{1,3})*\s+\d{1,4}\s?[MmМм]\s*$")


def without_distance(text: str) -> str:
    return DISTANCE.sub("", text)
