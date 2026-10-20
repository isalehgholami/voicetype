"""WER / CER for benchmarking STT providers."""

from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_ZWNJ = "‌"
_AR_TO_FA = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "أ": "ا", "إ": "ا"})


def normalize(text: str) -> str:
    """Normalize for comparison: NFKC, Arabic→Persian letters, drop punctuation/diacritics, lowercase."""
    text = unicodedata.normalize("NFKC", text).translate(_AR_TO_FA).replace(_ZWNJ, " ")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(_PUNCT.sub(" ", text).lower().split())


def _edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> float:
    r, h = normalize(ref).split(), normalize(hyp).split()
    if not r:
        return 0.0 if not h else 1.0
    return _edit_distance(r, h) / len(r)


def cer(ref: str, hyp: str) -> float:
    r, h = list(normalize(ref).replace(" ", "")), list(normalize(hyp).replace(" ", ""))
    if not r:
        return 0.0 if not h else 1.0
    return _edit_distance(r, h) / len(r)
