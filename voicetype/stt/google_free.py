"""Google Web Speech (free, undocumented) via SpeechRecognition — see docs/adr/0005."""

from __future__ import annotations

import socket
import threading
import time
import urllib.error
from collections.abc import Callable

from ..audio import SAMPLE_RATE, SAMPLE_WIDTH
from ..log import get_logger
from .base import (
    CancelToken,
    STTBackend,
    STTError,
    STTNetworkError,
    STTRateLimitError,
    STTResult,
    STTUnavailableError,
    STTUnintelligibleError,
)
from .chunking import transcribe_chunked

log = get_logger("stt.google")

RETRY_DELAYS = (0.5, 1.5)  # up to 2 retries, network / 5xx only


def classify_exception(exc: BaseException) -> STTError:
    """Map SpeechRecognition / urllib failures onto our taxonomy."""
    name = type(exc).__name__
    if name == "UnknownValueError":
        return STTUnintelligibleError()
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return STTRateLimitError(str(exc))
        return STTNetworkError(f"HTTP {exc.code}")
    if isinstance(exc, urllib.error.URLError | socket.timeout | TimeoutError | ConnectionError | OSError):
        return STTNetworkError(str(exc))
    if name == "RequestError":
        text = str(exc)
        if "429" in text or "quota" in text.lower() or "rate" in text.lower():
            return STTRateLimitError(text)
        return STTNetworkError(text)
    return STTError(f"{name}: {exc}")


class GoogleFreeBackend(STTBackend):
    name = "google_free"

    def __init__(
        self,
        language: str = "fa-IR",
        timeout: float = 60.0,
        *,
        recognize: Callable[[bytes, str], str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.language = language
        self.timeout = timeout
        self._sleep = sleep
        self._recognize = recognize or self._default_recognize
        self._sr_recognizer = None

    def _default_recognize(self, pcm: bytes, language: str) -> str:
        try:
            import speech_recognition as sr
        except ImportError as e:  # pragma: no cover - env dependent
            raise STTUnavailableError("pip install SpeechRecognition") from e
        if self._sr_recognizer is None:
            self._sr_recognizer = sr.Recognizer()
            self._sr_recognizer.operation_timeout = self.timeout
        audio = sr.AudioData(pcm, SAMPLE_RATE, SAMPLE_WIDTH)
        return self._sr_recognizer.recognize_google(audio, language=language)

    def _call_with_deadline(self, pcm: bytes, cancel: CancelToken | None) -> str:
        """Run the blocking call in a thread so cancel/timeout return promptly.
        (An in-flight HTTP request can't be aborted; its result is simply discarded.)"""
        box: dict[str, object] = {}
        done = threading.Event()

        def work() -> None:
            try:
                box["text"] = self._recognize(pcm, self.language)
            except BaseException as e:  # noqa: BLE001 - transported to caller
                box["exc"] = e
            finally:
                done.set()

        threading.Thread(target=work, daemon=True, name="stt-google").start()
        deadline = time.monotonic() + self.timeout
        while not done.wait(0.05):
            if cancel:
                cancel.raise_if_cancelled()
            if time.monotonic() > deadline:
                raise STTNetworkError("request timed out")
        if "exc" in box:
            raise classify_exception(box["exc"])  # type: ignore[arg-type]
        return str(box["text"])

    def _transcribe_one(self, pcm: bytes, cancel: CancelToken | None) -> STTResult:
        attempt = 0
        while True:
            try:
                text = self._call_with_deadline(pcm, cancel)
                return STTResult(text.strip(), True)
            except STTNetworkError as e:
                if attempt >= len(RETRY_DELAYS):
                    raise
                log.warning("google_free network error (%s); retry %d", e.detail, attempt + 1)
                self._sleep(RETRY_DELAYS[attempt])
                attempt += 1
                if cancel:
                    cancel.raise_if_cancelled()

    def transcribe(self, pcm: bytes, cancel: CancelToken | None = None) -> STTResult:
        # Chunking happens above retry so each ≤50 s piece retries independently.
        return transcribe_chunked(_Single(self), pcm, cancel)


class _Single(STTBackend):
    """Adapter exposing the retrying single-request call to :func:`transcribe_chunked`."""

    def __init__(self, inner: GoogleFreeBackend) -> None:
        self.inner = inner

    def transcribe(self, pcm: bytes, cancel: CancelToken | None = None) -> STTResult:
        return self.inner._transcribe_one(pcm, cancel)
