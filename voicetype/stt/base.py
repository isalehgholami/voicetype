"""STT backend interface, result type and error taxonomy."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class STTResult:
    text: str
    is_final: bool = True
    confidence: float | None = None


class STTError(RuntimeError):
    """Base class; ``user_message`` is safe to show in the overlay."""

    user_message = "Speech recognition failed"

    #: Whether the failure deserves a desktop notification.  Most errors do;
    #: "nothing was said" does not — the overlay already says it.
    notify = True

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.user_message)
        self.detail = detail


class STTNetworkError(STTError):
    user_message = "Network error — check your connection"


class STTRateLimitError(STTError):
    user_message = "Speech service is rate-limiting requests"


class STTUnintelligibleError(STTError):
    """Audio was processed but no words were recognised."""

    user_message = "Could not understand the audio"
    # Staying silent is not a failure the user needs a notification for: the
    # overlay already reports it and the daemon goes straight back to idle.
    notify = False


class STTUnavailableError(STTError):
    """Backend cannot run at all (package/model missing)."""

    user_message = "Speech backend is not available"


class STTCancelled(STTError):
    user_message = "Cancelled"


class CancelToken:
    """Cooperative cancellation flag shared between the dispatcher and a transcription."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise STTCancelled()


class StreamSession(ABC):
    """Incremental recognition for streaming backends (Vosk)."""

    @abstractmethod
    def feed(self, pcm: bytes) -> str:
        """Feed audio; returns the current partial transcript ('' if none)."""

    @abstractmethod
    def finish(self) -> STTResult:
        """Flush and return the final transcript."""

    def abort(self) -> None:  # noqa: B027 - optional hook
        """Stop background work without producing a result (cancel)."""


class STTBackend(ABC):
    name = "base"
    streaming = False

    @abstractmethod
    def transcribe(self, pcm: bytes, cancel: CancelToken | None = None) -> STTResult:
        """Transcribe a whole utterance of 16 kHz mono int16 PCM. May raise :class:`STTError`."""

    def open_stream(self) -> StreamSession | None:
        """Streaming backends return a session; others return None."""
        return None

    def close(self) -> None:  # noqa: B027 - optional hook
        pass
