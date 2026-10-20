"""Offline Whisper via faster-whisper (CPU/CUDA). Non-streaming."""

from __future__ import annotations

import array

from .base import CancelToken, STTBackend, STTResult, STTUnavailableError

INSTALL_HINT = "faster-whisper is not installed. Run: pip install faster-whisper"


def pcm16_to_float(pcm: bytes) -> list[float]:
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    return [s / 32768.0 for s in a]


class WhisperBackend(STTBackend):
    name = "whisper"

    def __init__(self, model: str = "small", device: str = "cpu", language: str = "fa-IR", *, model_factory=None):
        self.model_name = model
        self.device = device
        self.language = language.split("-")[0]
        self._model_factory = model_factory
        self._model = None

    def _load(self):
        if self._model is None:
            factory = self._model_factory
            if factory is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as e:
                    raise STTUnavailableError(INSTALL_HINT) from e
                factory = WhisperModel
            compute = "float16" if self.device == "cuda" else "int8"
            self._model = factory(self.model_name, device=self.device, compute_type=compute)
        return self._model

    def transcribe(self, pcm: bytes, cancel: CancelToken | None = None) -> STTResult:
        model = self._load()
        try:
            import numpy as np

            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        except ImportError:  # numpy is a faster-whisper dependency; fall back for tests
            audio = pcm16_to_float(pcm)
        segments, _info = model.transcribe(audio, language=self.language, vad_filter=True)
        parts: list[str] = []
        for seg in segments:  # generator: lets us cancel between segments
            if cancel:
                cancel.raise_if_cancelled()
            parts.append(seg.text.strip())
        return STTResult(" ".join(p for p in parts if p), True)
