"""The translation interface: a ``text -> text`` callable that may fail."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class TranslateError(RuntimeError):
    """A translation could not be produced (message is user-facing)."""

    user_message = "Translation failed"


@runtime_checkable
class Translator(Protocol):
    """Rewrites a transcript in the configured target language.

    A translator may also carry a ``label`` — the name of the target
    language ("GERMAN") — which the overlay shows next to the result.
    """

    def translate(self, text: str) -> str: ...
