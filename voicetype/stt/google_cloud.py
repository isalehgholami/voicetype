"""Google Cloud Speech-to-Text (paid, optional) via SpeechRecognition."""

from __future__ import annotations

from collections.abc import Callable

from ..audio import SAMPLE_RATE, SAMPLE_WIDTH
from .base import CancelToken, STTBackend, STTResult, STTUnavailableError
from .chunking import transcribe_chunked
from .google_free import classify_exception


class _OneShot(STTBackend):
    def __init__(self, fn: Callable[[bytes], str]) -> None:
        self.fn = fn

    def transcribe(self, pcm: bytes, cancel: CancelToken | None = None) -> STTResult:
        try:
            return STTResult(self.fn(pcm).strip(), True)
        except Exception as e:  # noqa: BLE001
            from .base import STTError

            if isinstance(e, STTError):
                raise
            raise classify_exception(e) from e


class GoogleCloudBackend(STTBackend):
    name = "google_cloud"

    def __init__(self, keyfile: str, language: str = "fa-IR", *, recognize: Callable[[bytes], str] | None = None):
        self.keyfile = keyfile
        self.language = language
        self._recognize = recognize or self._default

    def _default(self, pcm: bytes) -> str:
        try:
            import speech_recognition as sr
        except ImportError as e:
            raise STTUnavailableError("pip install SpeechRecognition google-cloud-speech") from e
        audio = sr.AudioData(pcm, SAMPLE_RATE, SAMPLE_WIDTH)
        try:
            return sr.Recognizer().recognize_google_cloud(
                audio, credentials_json=open(self.keyfile, encoding="utf-8").read(), language=self.language
            )
        except (ImportError, AttributeError) as e:
            raise STTUnavailableError("google-cloud-speech is required: pip install google-cloud-speech") from e
        except OSError as e:
            raise STTUnavailableError(f"cannot read keyfile {self.keyfile}: {e}") from e

    def transcribe(self, pcm: bytes, cancel: CancelToken | None = None) -> STTResult:
        return transcribe_chunked(_OneShot(self._recognize), pcm, cancel)
