"""Drop typical ASR junk outputs (pattern from hyprwhspr hallucination.py)."""

from __future__ import annotations

import re

_JUNK = {
    # Whisper on silence / music
    "thanks for watching",
    "thank you for watching",
    "thank you",
    "thanks",
    "you",
    "bye",
    "subtitles by the amara.org community",
    "[music]",
    "[blank_audio]",
    "(music)",
    # Persian equivalents
    "ممنون که تماشا کردید",
    "از تماشای شما متشکرم",
    "زیرنویس از جامعه amara.org",
    "موسیقی",
}
_ONLY_PUNCT = re.compile(r"^[\W_]*$", re.UNICODE)
_REPEAT = re.compile(r"^(.{1,12}?)(?:\s*\1){5,}$", re.UNICODE)


def is_hallucination(text: str) -> bool:
    t = text.strip()
    if not t or _ONLY_PUNCT.match(t):
        return True
    low = t.lower().strip(" .!،؟?…")
    if low in _JUNK:
        return True
    return bool(_REPEAT.match(low))
