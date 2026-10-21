"""Free translation through the ``translators`` package, with a fallback chain.

The package (https://github.com/UlionTse/translators) drives the free web
endpoints of Google, Bing, Yandex and Alibaba — no API key, no account.  One
service is not enough: the free endpoints rate-limit (HTTP 429) and block
(HTTP 403) regularly, so the services are tried in turn and the first answer
wins.

The package is optional and imported lazily: the daemon starts without it and
only pays for it when a transcript is translated.
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from ..config import ConfigError
from ..log import get_logger
from .base import TranslateError

log = get_logger("translate.services")

#: The services are tried in this order: Google has the best quality, the
#: others take over when it rate-limits (HTTP 429) or blocks (HTTP 403).
SERVICE_ORDER: tuple[str, ...] = ("google", "bing", "yandex", "alibaba")

#: How to install the optional dependency.
INSTALL_HINT = "pip install translators   (or: pip install 'voicetype[translate]')"

#: The language codes the configuration accepts, with their English names.
#: The name is what the overlay shows next to a translated sentence.
LANGUAGES: dict[str, str] = {
    "af": "Afrikaans",
    "am": "Amharic",
    "ar": "Arabic",
    "az": "Azerbaijani",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "bs": "Bosnian",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "eo": "Esperanto",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fil": "Filipino",
    "fr": "French",
    "ga": "Irish",
    "gl": "Galician",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "km": "Khmer",
    "kn": "Kannada",
    "ko": "Korean",
    "ku": "Kurdish",
    "ky": "Kyrgyz",
    "la": "Latin",
    "lo": "Lao",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mn": "Mongolian",
    "mr": "Marathi",
    "ms": "Malay",
    "my": "Burmese",
    "ne": "Nepali",
    "nl": "Dutch",
    "no": "Norwegian",
    "pa": "Punjabi",
    "pl": "Polish",
    "ps": "Pashto",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "so": "Somali",
    "sq": "Albanian",
    "sr": "Serbian",
    "su": "Sundanese",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tl": "Tagalog",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "yo": "Yoruba",
    "zh": "Chinese",
    "zu": "Zulu",
}

#: The English names, lower-cased ("english" → "en").
LANGUAGE_CODES: dict[str, str] = {name.lower(): code for code, name in LANGUAGES.items()}

#: The regions that are a language of their own, not a variant of it.
#: The free services call the two Chinese scripts differently.
REGION_LANGUAGES: dict[str, str] = {
    "zh-tw": "zh-Hant",  # traditional Chinese
    "zh-hk": "zh-Hant",
    "zh-hant": "zh-Hant",
    "zh-cn": "zh",  # simplified Chinese
    "zh-hans": "zh",
}

#: How long a single failure is described in the warning (characters).
_REASON_LIMIT = 90


def resolve_service_language(
    value: str,
    *,
    path: str = "translate.language",
    allow_auto: bool = False,
) -> str:
    """Map a configured language to a code the free services know.

    Accepts a code (``en``), a name (``English``) and a regional code
    (``en-US`` → ``en``).  ``auto`` is only allowed for the source language —
    the service then detects the language that was spoken.
    """
    raw = (value or "").strip().lower()
    if allow_auto and raw in ("auto", ""):
        return "auto"
    if not raw:
        raise ConfigError(f"config: '{path}' must not be empty")
    if raw in LANGUAGES:
        return raw
    if raw in LANGUAGE_CODES:
        return LANGUAGE_CODES[raw]
    if raw in REGION_LANGUAGES:
        return REGION_LANGUAGES[raw]
    base = raw.replace("_", "-").split("-", 1)[0]
    if base in LANGUAGES:
        return base
    raise ConfigError(
        f"config: '{path}' is not a known language: {value!r} "
        "(use a code like 'en' or a name like 'English')"
    )


def service_language_label(code: str) -> str:
    """The English name of a language code, upper-cased (``en`` → ``ENGLISH``)."""
    key = (code or "").strip().lower()
    if not key:
        return ""
    name = LANGUAGES.get(key) or LANGUAGES.get(key.replace("_", "-").split("-", 1)[0])
    return str(name or key).upper()


def services_available() -> bool:
    """True when the ``translators`` package is installed."""
    try:
        return importlib.util.find_spec("translators") is not None
    except (ImportError, ValueError):  # pragma: no cover - a broken installation
        return False


def _import_translators() -> Any:
    """Import the optional ``translators`` package.

    The package looks the server region up over the network *while it is
    imported* — without a timeout, so it can hang the daemon, and it raises
    when the lookup fails.  The lookup is skipped when the region is known:
    ``translators_default_region`` is the switch the package itself
    documents.
    """
    os.environ.setdefault("translators_default_region", "EN")
    try:
        import translators as ts
    except ImportError as e:
        raise TranslateError(f"the translators package is missing: {INSTALL_HINT}") from e
    except Exception as e:  # noqa: BLE001 - a broken package must not break the daemon
        raise TranslateError(f"the translators package could not be loaded: {e}") from e
    return ts


def _default_translate(
    text: str,
    *,
    translator: str,
    from_language: str,
    to_language: str,
    timeout: float,
    proxies: dict[str, str] | None,
) -> str:
    """One request to one service of the ``translators`` package."""
    ts = _import_translators()
    return ts.translate_text(
        text,
        translator=translator,
        from_language=from_language,
        to_language=to_language,
        timeout=timeout,
        proxies=proxies,
        sleep_seconds=0,
        if_print_warning=False,
    )


def _reason(error: BaseException) -> str:
    """A short, readable reason for a failed service."""
    text = str(error).strip().splitlines()
    detail = text[0] if text else type(error).__name__
    if len(detail) > _REASON_LIMIT:
        detail = detail[: _REASON_LIMIT - 1] + "…"
    return detail or type(error).__name__


def translate_with_fallback(
    text: str,
    from_language: str = "fa",
    to_language: str = "en",
    *,
    services: Sequence[str] = SERVICE_ORDER,
    timeout: float = 15.0,
    proxies: dict[str, str] | None = None,
    on_warning: Callable[[str], None] | None = None,
    translate: Callable[..., str] | None = None,
) -> str:
    """Translate ``text``; the first service that answers wins.

    Every service is tried in turn: a service that fails (HTTP 429, HTTP 403,
    a timeout, an unsupported language) is skipped and the next one takes
    over.  The services share the time budget of ``timeout`` seconds, so a
    blocked service cannot hold the daemon up.  When every service fails the
    original text is returned — a translation never eats a transcript.
    """
    if not text or not isinstance(text, str):
        return ""
    call = translate or _default_translate
    deadline = time.monotonic() + timeout
    failures: list[str] = []
    for service in services:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.warning("the translation budget of %gs is used up", timeout)
            break
        try:
            result = call(
                text,
                translator=service,
                from_language=from_language,
                to_language=to_language,
                timeout=remaining,
                proxies=proxies,
            )
        except Exception as e:  # noqa: BLE001 - a broken service must not stop the chain
            log.warning("the '%s' translation service failed: %s", service, _reason(e))
            failures.append(f"{service} ({_reason(e)})")
            continue
        out = (result or "").strip()
        if out:
            if failures:
                log.info("the translation came from '%s' (%s failed)", service, ", ".join(failures))
            return out
        log.warning("the '%s' translation service returned nothing", service)
        failures.append(f"{service} (no answer)")
    message = "translation failed: " + (", ".join(failures) if failures else "no service answered")
    log.warning("%s", message)
    if on_warning is not None:
        try:
            on_warning(message)
        except Exception:  # noqa: BLE001 - the overlay must never break the daemon
            log.exception("the translation warning callback failed")
    return text


def translate_fa_to_en(text: str) -> str:
    """Translate a Persian text into English.

    The services are tried in turn (Google, Bing, Yandex, Alibaba) and the
    first answer wins; when every service fails the original text is
    returned.  An empty text stays empty.
    """
    if not text or not isinstance(text, str):
        return ""
    return translate_with_fallback(text, "fa", "en")


def probe_services(timeout: float = 8.0) -> tuple[bool, str]:
    """Try a short translation; returns ``(ok, detail)``.

    The doctor uses it to show that the free services really answer — the
    packages can be installed and still be blocked (HTTP 429, HTTP 403).
    """
    text = "سلام"
    try:
        out = translate_with_fallback(text, "fa", "en", timeout=timeout)
    except Exception as e:  # noqa: BLE001 - a diagnostic must never crash
        return False, _reason(e)
    if out == text:
        return False, "no service answered"
    return True, out


class ServiceTranslator:
    """Translates a transcript with the ``translators`` package.

    A failure never eats the transcript: the original text is returned and the
    problem is reported through ``on_warning`` (the overlay).
    """

    def __init__(
        self,
        target: str,
        source: str = "auto",
        timeout: float = 15.0,
        proxies: dict[str, str] | None = None,
        on_warning: Callable[[str], None] | None = None,
        services: Sequence[str] = SERVICE_ORDER,
        translate: Callable[..., str] | None = None,
    ) -> None:
        self.target = target
        self.source = source
        self.timeout = timeout
        self.proxies = proxies
        self.on_warning = on_warning
        self.services = tuple(services)
        #: The name of the target language, as the overlay shows it.
        self.label = service_language_label(target)
        self._translate = translate
        # The package keeps one session per service; two threads must not
        # share them.
        self._lock = threading.Lock()

    def translate(self, text: str) -> str:
        """Translate ``text``; the original text is returned when every service fails."""
        if not text.strip():
            return text
        with self._lock:
            out = translate_with_fallback(
                text,
                self.source,
                self.target,
                services=self.services,
                timeout=self.timeout,
                proxies=self.proxies,
                on_warning=self.on_warning,
                translate=self._translate,
            )
        return out or text

    def close(self) -> None:
        """Nothing to release: the package keeps no per-translator state."""
