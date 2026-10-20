"""Persian text normalization: ZWNJ, digits, punctuation."""

from __future__ import annotations

import re

ZWNJ = "‌"
_PERSIAN_LETTER = re.compile(r"[؀-ۿ]")
_WORD = r"(?<![\w‌])"  # left boundary that treats ZWNJ as part of a word
_WORD_END = r"(?![\w‌])"

# "می " / "نمی " + verb → half-space.  Only when followed by a Persian letter.
_MI_PREFIX = re.compile(rf"{_WORD}(ن?می) +(?=[؀-ۿ])")
# plural / comparative suffixes written as separate words
_SUFFIX = re.compile(rf"(?<=[؀-ۿ]) +(ها|های|هایی|ترین|تری|ام|ات|اش|مان|تان|شان){_WORD_END}")
_TAR = re.compile(rf"(?<=[؀-ۿ]) +(تر){_WORD_END}")

# words that look like suffixes but are real words when separate; protected from the rules above
_NO_JOIN_BEFORE = {"من", "تو", "او", "ما", "شما", "آنها", "اینها"}

_DIGIT_MAPS = {
    "latin": str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"),
    "persian": str.maketrans("0123456789٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹۰۱۲۳۴۵۶۷۸۹"),
}


def has_persian(text: str) -> bool:
    return bool(_PERSIAN_LETTER.search(text))


def normalize_zwnj(text: str) -> str:
    """Insert half-spaces: می‌شود، کتاب‌ها، بزرگ‌تر، کتابم → (ام is left alone if ambiguous)."""
    text = _MI_PREFIX.sub(lambda m: m.group(1) + ZWNJ, text)

    def suffix(m: re.Match) -> str:
        before = text_before(m)
        if before in _NO_JOIN_BEFORE:
            return m.group(0)
        return ZWNJ + m.group(1)

    def text_before(m: re.Match) -> str:
        s = m.string[: m.start()]
        parts = s.split()
        return parts[-1] if parts else ""

    text = _SUFFIX.sub(suffix, text)
    text = _TAR.sub(suffix, text)
    return text


def convert_digits(text: str, target: str) -> str:
    """target: 'keep' | 'latin' | 'persian'."""
    if target == "keep":
        return text
    return text.translate(_DIGIT_MAPS[target])


_COMMA = re.compile(r"(?<!\d),(?!\d)")  # not inside numbers like 3,500
_QUESTION = re.compile(r"\?")
_SEMI = re.compile(r";")


def persian_punctuation(text: str) -> str:
    """Use ، ؟ ؛ in Persian text (only if the text contains Persian letters)."""
    if not has_persian(text):
        return text
    text = _COMMA.sub("،", text)
    text = _QUESTION.sub("؟", text)
    text = _SEMI.sub("؛", text)
    return text
