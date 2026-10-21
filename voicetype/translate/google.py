"""Google Translate backend built on py-googletrans (the ``googletrans`` package).

py-googletrans is asynchronous (httpx under the hood), while VoiceType calls
the translator from a plain worker thread.  The translator therefore owns a
small event-loop thread: the HTTP client stays alive between translations and
the calling thread only waits for the result.

The library talks to Google's free endpoint — the transcript is sent to
Google, so translation is opt-in (``[translate] enabled = false`` by default).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent import futures
from typing import Any

from ..config import ConfigError
from ..log import get_logger
from .base import TranslateError

log = get_logger("translate.google")

#: The direct API endpoint — unlike the web app it needs no token.
SERVICE_URLS = ("translate.googleapis.com",)

#: How to install the optional dependency.
INSTALL_HINT = "pip install googletrans   (or: pip install 'voicetype[translate]')"

#: (code → name, name → code) of py-googletrans.
LanguageTables = tuple[dict[str, str], dict[str, str]]


def language_tables() -> LanguageTables:
    """The language tables of py-googletrans.  Raises :class:`ConfigError` when
    the optional package is missing."""
    try:
        from googletrans.constants import LANGCODES, LANGUAGES
    except ImportError as e:
        raise ConfigError(f"config: translation needs the googletrans package: {INSTALL_HINT}") from e
    return LANGUAGES, LANGCODES


def resolve_language(
    value: str,
    *,
    path: str = "translate.language",
    allow_auto: bool = False,
    tables: LanguageTables | None = None,
) -> str:
    """Map a configured language to a py-googletrans language code.

    Accepts a code (``en``, ``zh-CN``), a name (``English``, ``persian``) and a
    regional code (``en-US`` → ``en``).  ``auto`` is only allowed for the source
    language — Google then detects it.
    """
    raw = (value or "").strip().lower()
    if allow_auto and raw in ("auto", ""):
        return "auto"
    if not raw:
        raise ConfigError(f"config: '{path}' must not be empty")
    codes, names = tables if tables is not None else language_tables()
    if raw in codes:
        return raw
    if raw in names:  # "english" → "en"
        return names[raw]
    base = raw.replace("_", "-").split("-", 1)[0]
    if base in codes:
        return base
    raise ConfigError(
        f"config: '{path}' is not a known language: {value!r} (use a code like 'en' or a name like 'English')"
    )


def language_label(code: str, tables: LanguageTables | None = None) -> str:
    """The English name of a language code, upper-cased (``de`` → ``GERMAN``).

    The overlay names the language of a translation in a small label.  The
    tables of py-googletrans carry the names; without them the code itself
    is the best label there is.
    """
    key = (code or "").strip().lower()
    if not key:
        return ""
    try:
        codes, _names = tables if tables is not None else language_tables()
    except ConfigError:
        return key.upper()
    name = codes.get(key)
    if name is None:
        name = codes.get(key.replace("_", "-").split("-", 1)[0])
    return str(name or key).upper()


def _default_client_factory(proxy: str, timeout: float) -> Callable[[], Any]:
    """Build the py-googletrans client (imported lazily: it is optional)."""

    def factory() -> Any:
        try:
            from googletrans import Translator
        except ImportError as e:  # pragma: no cover - the daemon checks this upfront
            raise TranslateError(f"the googletrans package is missing: {INSTALL_HINT}") from e
        kwargs: dict[str, Any] = {
            "service_urls": list(SERVICE_URLS),
            "raise_exception": True,  # a failed request must not look like a translation
            "timeout": timeout,
        }
        if proxy:
            kwargs["proxy"] = proxy
        return Translator(**kwargs)

    return factory


async def _aclose(client: Any) -> None:
    """Close an httpx client (the library keeps it in ``client.client``)."""
    target = client
    if not hasattr(target, "aclose") and hasattr(target, "client"):
        target = target.client
    close = getattr(target, "aclose", None)
    if close is not None:
        await close()


class GoogleTranslator:
    """Translates text with py-googletrans (Google Translate's free endpoint).

    A failure never eats the transcript: the original text is returned and the
    problem is reported through ``on_warning`` (the overlay).
    """

    def __init__(
        self,
        target: str,
        source: str = "auto",
        timeout: float = 15.0,
        proxy: str = "",
        on_warning: Callable[[str], None] | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.target = target
        self.source = source
        self.timeout = timeout
        self.proxy = proxy
        self.on_warning = on_warning
        #: The name of the target language, as the overlay shows it.
        self.label = language_label(target)
        self._factory = client_factory or _default_client_factory(proxy, timeout)
        self._client: Any = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="translate")
        self._thread.start()

    # ------------------------------------------------------------------ event loop
    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _client_or_create(self) -> Any:
        if self._client is None:
            self._client = self._factory()
        return self._client

    async def _translate(self, text: str) -> str:
        client = await self._client_or_create()
        result = await client.translate(text, dest=self.target, src=self.source)
        if isinstance(result, str):
            return result
        return getattr(result, "text", "") or ""

    # ------------------------------------------------------------------ public API
    def translate(self, text: str) -> str:
        """Translate ``text``; the original text is returned when it fails."""
        if not text.strip():
            return text
        try:
            future = asyncio.run_coroutine_threadsafe(self._translate(text), self._loop)
            out = future.result(timeout=self.timeout + 5)
        except TimeoutError:
            self._warn(f"translation timed out after {self.timeout:g}s")
            return text
        except (asyncio.CancelledError, futures.CancelledError):
            self._warn("translation cancelled")
            return text
        except Exception as e:  # noqa: BLE001 - a broken translation must not eat the transcript
            self._warn(f"translation failed: {e}")
            return text
        return out.strip() or text

    def _warn(self, message: str) -> None:
        log.warning("%s", message)
        if self.on_warning is None:
            return
        try:
            self.on_warning(message)
        except Exception:  # noqa: BLE001 - the overlay must never break the daemon
            log.exception("the translation warning callback failed")

    def close(self) -> None:
        """Release the HTTP client and stop the loop thread."""
        client, self._client = self._client, None
        if client is not None and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(_aclose(client), self._loop).result(timeout=5)
            except Exception:  # noqa: BLE001
                log.debug("closing the translation client failed", exc_info=True)
        if not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._cancel_pending(), self._loop).result(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:  # pragma: no cover - the loop is already gone
            pass
        self._thread.join(timeout=2)

    async def _cancel_pending(self) -> None:
        """Cancel a translation that is still running (the loop is about to stop)."""
        for task in asyncio.all_tasks(self._loop):
            task.cancel()
