"""The translation interface: a ``text -> text`` callable that may fail."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class TranslateError(RuntimeError):
    """A translation could not be produced (message is user-facing)."""

    user_message = "Translation failed"


@runtime_checkable
class Translator(Protocol):
    """Rewrites a transcript in the configured target language."""

    def translate(self, text: str) -> str: ...
