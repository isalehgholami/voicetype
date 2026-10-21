"""Optional translation of the transcript (Google Translate via the free services).

Switched off by default (``[translate] enabled = false``).  When it is on,
every finished transcript is translated into ``[translate] language``
(English by default) right before it is injected.  The live preview in the
overlay always shows the text as it was spoken.

Two backends are available:

* ``services`` — the ``translators`` package, which tries Google, Bing,
  Yandex and Alibaba in turn until one of them answers.  No API key.
* ``googletrans`` — py-googletrans, a single service.

``[translate] backend = "auto"`` takes whichever package is installed and
prefers the fallback chain.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import ConfigError, PostprocessConfig, TranslateConfig
from ..log import get_logger
from ..network import ProxyError, httpx_proxy_url, parse_proxy
from ..postprocess.pipeline import auto_punctuate, capitalize_first
from .base import TranslateError, Translator
from .google import GoogleTranslator, language_label, language_tables, resolve_language
from .services import (
    INSTALL_HINT,
    SERVICE_ORDER,
    ServiceTranslator,
    probe_services,
    resolve_service_language,
    service_language_label,
    services_available,
    translate_fa_to_en,
    translate_with_fallback,
)

log = get_logger("translate")

__all__ = (
    "GoogleTranslator",
    "INSTALL_HINT",
    "SERVICE_ORDER",
    "ServiceTranslator",
    "TranslateError",
    "Translator",
    "build_translator",
    "check_translate_config",
    "language_label",
    "language_tables",
    "probe_services",
    "resolve_language",
    "resolve_service_language",
    "service_language_label",
    "translate_fa_to_en",
    "translate_stage",
    "translate_with_fallback",
)


def _httpx_proxy(proxy: str) -> str:
    """The proxy in the form httpx understands (``socks5h`` → ``socks5``)."""
    if not proxy:
        return ""
    try:
        return httpx_proxy_url(parse_proxy(proxy))
    except ProxyError:
        return proxy  # an invalid proxy is reported by the network layer


def _build_service_translator(
    cfg: TranslateConfig,
    *,
    proxy: str,
    on_warning: Callable[[str], None] | None,
) -> ServiceTranslator:
    """The translator that walks the free services until one of them answers."""
    target = resolve_service_language(cfg.language, path="translate.language")
    source = resolve_service_language(cfg.source, path="translate.source", allow_auto=True)
    proxies = {"http": proxy, "https": proxy} if proxy else None
    log.info("translation enabled: %s → %s (services: %s)", source, target, ", ".join(SERVICE_ORDER))
    return ServiceTranslator(
        target,
        source=source,
        timeout=cfg.timeout_seconds,
        proxies=proxies,
        on_warning=on_warning,
    )


def _build_googletrans_translator(
    cfg: TranslateConfig,
    *,
    proxy: str,
    on_warning: Callable[[str], None] | None,
) -> GoogleTranslator:
    """The translator that talks to py-googletrans (a single service)."""
    target = resolve_language(cfg.language, path="translate.language")
    source = resolve_language(cfg.source, path="translate.source", allow_auto=True)
    log.info("translation enabled: %s → %s (googletrans)", source, target)
    return GoogleTranslator(
        target,
        source=source,
        timeout=cfg.timeout_seconds,
        proxy=_httpx_proxy(proxy),
        on_warning=on_warning,
    )


def build_translator(
    cfg: TranslateConfig,
    *,
    proxy: str = "",
    on_warning: Callable[[str], None] | None = None,
) -> Translator | None:
    """Build the translator described by ``cfg``; ``None`` when it is disabled.

    ``cfg.backend`` picks the implementation: ``services`` uses the
    ``translators`` package, ``googletrans`` uses py-googletrans and ``auto``
    takes the fallback chain when it is installed and py-googletrans
    otherwise.

    Raises :class:`ConfigError` when the language is unknown or none of the
    optional packages is installed.
    """
    if not cfg.enabled:
        return None
    backend = (cfg.backend or "auto").strip().lower()
    if backend not in ("auto", "services", "googletrans"):
        raise ConfigError(f"config: 'translate.backend' is not a known backend: {cfg.backend!r}")
    if backend in ("auto", "services"):
        if not services_available():
            if backend == "services":
                raise ConfigError(f"config: translation needs the translators package: {INSTALL_HINT}")
            # "auto": py-googletrans is the second choice
            log.info("the translators package is missing; using py-googletrans instead")
        else:
            try:
                return _build_service_translator(cfg, proxy=proxy, on_warning=on_warning)
            except ConfigError as e:
                if backend == "services":
                    raise
                # "auto": the fallback chain cannot serve this language —
                # py-googletrans is the second choice.
                log.info("the fallback chain is unavailable (%s); using py-googletrans instead", e)
    return _build_googletrans_translator(cfg, proxy=proxy, on_warning=on_warning)


def check_translate_config(cfg: TranslateConfig) -> None:
    """Check the configured languages for the configured backend.

    In the ``auto`` mode a language is valid when at least one backend knows
    it.  Raises :class:`ConfigError` when none of them does.
    """
    backend = (cfg.backend or "auto").strip().lower()
    problems: list[ConfigError] = []
    if backend in ("auto", "services"):
        try:
            resolve_service_language(cfg.language, path="translate.language")
            resolve_service_language(cfg.source, path="translate.source", allow_auto=True)
        except ConfigError as e:
            if backend == "services":
                raise
            problems.append(e)
    if backend in ("auto", "googletrans"):
        try:
            tables = language_tables()
        except ConfigError:
            tables = None  # the package is missing — that is not a language problem
        if tables is not None:
            try:
                resolve_language(cfg.language, path="translate.language", tables=tables)
                resolve_language(cfg.source, path="translate.source", allow_auto=True, tables=tables)
            except ConfigError as e:
                if backend == "googletrans":
                    raise
                problems.append(e)
    if problems:
        raise problems[0]


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
