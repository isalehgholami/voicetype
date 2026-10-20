"""Provider registry + automatic fallback (adding a provider never touches the dispatcher)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..config import Config, SttConfig
from ..log import get_logger
from .base import (
    CancelToken,
    StreamSession,
    STTBackend,
    STTCancelled,
    STTError,
    STTResult,
    STTUnavailableError,
    STTUnintelligibleError,
)

log = get_logger("stt.registry")


@dataclass
class Options:
    """Everything a provider factory may need (a flattened view of SttConfig)."""

    language: str = "fa-IR"
    timeout: float = 60.0
    vosk_model_path: str = "/usr/share/vosk/vosk-model-fa-0.42"
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    google_cloud_keyfile: str = ""

    @classmethod
    def from_config(cls, stt: SttConfig) -> Options:
        return cls(
            language=stt.language,
            timeout=stt.timeout_seconds,
            vosk_model_path=stt.vosk.model_path,
            whisper_model=stt.whisper.model,
            whisper_device=stt.whisper.device,
            google_cloud_keyfile=stt.google_cloud.keyfile,
        )


Factory = Callable[[Options], STTBackend]
_FACTORIES: dict[str, Factory] = {}


def register(name: str, factory: Factory) -> None:
    _FACTORIES[name] = factory


def available() -> list[str]:
    return sorted(_FACTORIES)


def default_options() -> Options:
    return Options()


def create(name: str, opts: Options) -> STTBackend:
    try:
        factory = _FACTORIES[name]
    except KeyError:
        raise STTUnavailableError(f"unknown STT provider '{name}' (available: {', '.join(available())})") from None
    return factory(opts)


def _google_free(o: Options) -> STTBackend:
    from .google_free import GoogleFreeBackend

    return GoogleFreeBackend(o.language, o.timeout)


def _google_cloud(o: Options) -> STTBackend:
    from .google_cloud import GoogleCloudBackend

    return GoogleCloudBackend(o.google_cloud_keyfile, o.language)


def _vosk(o: Options) -> STTBackend:
    from .vosk_backend import VoskBackend

    return VoskBackend(o.vosk_model_path)


def _whisper(o: Options) -> STTBackend:
    from .whisper_backend import WhisperBackend

    return WhisperBackend(o.whisper_model, o.whisper_device, o.language)


register("google_free", _google_free)
register("google_cloud", _google_cloud)
register("vosk", _vosk)
register("whisper", _whisper)


class FallbackBackend(STTBackend):
    """Wraps a primary backend; after ``after`` consecutive failures switches to a fallback.

    ``on_fallback(message)`` is called once when the switch happens (overlay warning).
    """

    def __init__(
        self,
        primary: STTBackend,
        fallback_factory: Callable[[], STTBackend] | None,
        after: int = 3,
        on_fallback: Callable[[str], None] | None = None,
    ) -> None:
        self.primary = primary
        self._fallback_factory = fallback_factory
        self._fallback: STTBackend | None = None
        self.after = after
        self.failures = 0
        self.on_fallback = on_fallback
        self.using_fallback = False
        self.name = primary.name

    @property
    def streaming(self) -> bool:  # type: ignore[override]
        return self._active().streaming

    def _active(self) -> STTBackend:
        if self.using_fallback and self._fallback is not None:
            return self._fallback
        return self.primary

    def open_stream(self) -> StreamSession | None:
        return self._active().open_stream()

    def transcribe(self, pcm: bytes, cancel: CancelToken | None = None) -> STTResult:
        backend = self._active()
        try:
            res = backend.transcribe(pcm, cancel)
        except (STTCancelled, STTUnintelligibleError):
            raise  # not a provider failure
        except STTError:
            if backend is self.primary:
                self.failures += 1
                if self.failures >= self.after and self._switch():
                    return self._active().transcribe(pcm, cancel)
            raise
        if backend is self.primary:
            self.failures = 0
        return res

    def _switch(self) -> bool:
        if self._fallback_factory is None:
            return False
        try:
            self._fallback = self._fallback_factory()
        except STTError as e:
            log.warning("fallback provider unavailable: %s", e)
            self._fallback_factory = None
            return False
        self.using_fallback = True
        msg = f"{self.primary.name} failed {self.failures}× — switched to {self._fallback.name}"
        log.warning(msg)
        if self.on_fallback:
            self.on_fallback(msg)
        return True

    def close(self) -> None:
        self.primary.close()
        if self._fallback:
            self._fallback.close()


def build_backend(config: Config, on_fallback: Callable[[str], None] | None = None) -> STTBackend:
    stt = config.stt
    opts = Options.from_config(stt)
    primary = create(stt.provider, opts)
    fb = stt.fallback_provider
    if not fb or fb == stt.provider:
        return primary
    return FallbackBackend(primary, lambda: create(fb, opts), stt.fallback_after, on_fallback)
