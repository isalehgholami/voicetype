"""Offline Vosk backend with streaming partial results (API from vosk-api/python)."""

from __future__ import annotations

import json
from pathlib import Path

from ..audio import SAMPLE_RATE
from .base import CancelToken, StreamSession, STTBackend, STTResult, STTUnavailableError

INSTALL_HINT = "Vosk is not installed. Run: pip install vosk   (or pacman -S python-vosk)"


class VoskStream(StreamSession):
    def __init__(self, recognizer) -> None:
        self._rec = recognizer
        self._committed: list[str] = []

    def feed(self, pcm: bytes) -> str:
        if self._rec.AcceptWaveform(pcm):
            text = json.loads(self._rec.Result()).get("text", "")
            if text:
                self._committed.append(text)
            return " ".join(self._committed)
        partial = json.loads(self._rec.PartialResult()).get("partial", "")
        return " ".join([*self._committed, partial]).strip()

    def finish(self) -> STTResult:
        text = json.loads(self._rec.FinalResult()).get("text", "")
        if text:
            self._committed.append(text)
        return STTResult(" ".join(self._committed).strip(), True)


class VoskBackend(STTBackend):
    name = "vosk"
    streaming = True

    def __init__(self, model_path: str, *, model_factory=None, recognizer_factory=None) -> None:
        self.model_path = model_path
        self._model_factory = model_factory
        self._recognizer_factory = recognizer_factory
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        if self._model_factory is None:
            try:
                from vosk import KaldiRecognizer, Model, SetLogLevel
            except ImportError as e:
                raise STTUnavailableError(INSTALL_HINT) from e
            SetLogLevel(-1)
            self._model_factory = Model
            self._recognizer_factory = KaldiRecognizer
        if not Path(self.model_path).exists():
            raise STTUnavailableError(
                f"Vosk model not found at {self.model_path}. Run: voicetype models install fa-small"
            )
        self._model = self._model_factory(self.model_path)
        return self._model

    def open_stream(self) -> StreamSession:
        model = self._load()
        return VoskStream(self._recognizer_factory(model, SAMPLE_RATE))

    def transcribe(self, pcm: bytes, cancel: CancelToken | None = None) -> STTResult:
        stream = self.open_stream()
        step = SAMPLE_RATE * 2  # 1 s
        for i in range(0, len(pcm), step):
            if cancel:
                cancel.raise_if_cancelled()
            stream.feed(pcm[i : i + step])
        return stream.finish()
