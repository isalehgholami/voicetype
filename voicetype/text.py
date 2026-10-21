"""Text direction helpers.

The overlay draws the live transcript one word at a time, so it has to know
which way the words run: a Persian or Arabic sentence is laid out from the
right, an English one from the left.

The rule is the one the Unicode bidirectional algorithm uses for a paragraph
(UAX #9, rules P2 and P3): the direction of the first *strongly* directional
character wins.  Weak and neutral characters — digits, punctuation, spaces —
are skipped, so "123" stays left-to-right and "سلام" goes right-to-left.

The Unicode character database that ships with Python knows the bidirectional
class of every character, so this needs no extra dependency.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

#: The two horizontal writing directions.
LTR = "ltr"
RTL = "rtl"

__all__ = [
    "LTR",
    "RTL",
    "base_direction",
    "first_strong_direction",
    "is_rtl",
    "word_direction",
    "word_directions",
]


def first_strong_direction(text: str) -> str | None:
    """The direction of the first strongly directional character in ``text``.

    ``None`` when the text holds no strong character at all — only digits,
    punctuation or spaces, whose direction depends on their surroundings.
    """
    for char in text or "":
        kind = unicodedata.bidirectional(char)
        if kind in ("R", "AL"):
            return RTL
        if kind == "L":
            return LTR
    return None


def base_direction(text: str) -> str:
    """The paragraph direction of ``text``.

    ``ltr`` when the text has no strong character: that is the fallback the
    bidirectional algorithm uses as well.
    """
    return first_strong_direction(text) or LTR


def is_rtl(text: str) -> bool:
    """True when ``text`` runs from right to left."""
    return base_direction(text) == RTL


def word_direction(word: str, fallback: str = LTR) -> str:
    """The direction of a single word.

    A word without a strong character — a number, a punctuation mark —
    inherits ``fallback``, which is the direction of the paragraph it sits in.
    """
    return first_strong_direction(word) or fallback


def word_directions(words: Sequence[str], fallback: str = LTR) -> list[str]:
    """The direction of every word of a list (see :func:`word_direction`)."""
    return [word_direction(word, fallback) for word in words]
