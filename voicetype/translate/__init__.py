"""Optional translation of the transcript (Google Translate via py-googletrans).

Switched off by default (``[translate] enabled = false``).  When it is on,
every finished transcript is translated into ``[translate] language``
(English by default) right before it is injected.  The live preview in the
overlay always shows the text as it was spoken.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import PostprocessConfig, TranslateConfig
from ..log import get_logger
from ..postprocess.pipeline import auto_punctuate, capitalize_first
from .base import TranslateError, Translator
from .google import GoogleTranslator, language_label, language_tables, resolve_language

log = get_logger("translate")

__all__ = (
    "GoogleTranslator",
    "TranslateError",
    "Translator",
    "build_translator",
    "language_label",
    "language_tables",
    "resolve_language",
    "translate_stage",
)


def build_translator(
    cfg: TranslateConfig,
    *,
    proxy: str = "",
    on_warning: Callable[[str], None] | None = None,
) -> Translator | None:
    """Build the translator described by ``cfg``; ``None`` when it is disabled.

    Raises :class:`ConfigError` when the language is unknown or the optional
    ``googletrans`` package is missing.
    """
    if not cfg.enabled:
        return None
    target = resolve_language(cfg.language, path="translate.language")
    source = resolve_language(cfg.source, path="translate.source", allow_auto=True)
    log.info("translation enabled: %s → %s", source, target)
    return GoogleTranslator(
        target,
        source=source,
        timeout=cfg.timeout_seconds,
        proxy=proxy,
        on_warning=on_warning,
    )


def translate_stage(
    pipeline: Callable[[str], str],
    translator: Translator | None,
    post: PostprocessConfig,
) -> Callable[[str], str]:
    """post-process → translate → final polish.

    The Persian clean-up runs on the text as it was spoken, the translation
    rewrites it in the target language, and the capitalisation and the
    sentence-final mark are applied once more to the result.
    """
    if translator is None:
        return pipeline

    def run(text: str) -> str:
        text = pipeline(text)
        if not text.strip():
            return text  # a spoken command ("new line") is injected as it is
        out = translator.translate(text)
        if post.capitalize_first:
            out = capitalize_first(out)
        if post.auto_punctuate:
            out = auto_punctuate(out)
        return out

    return run
