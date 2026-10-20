"""Audio capture: 16 kHz mono int16 via sounddevice, bounded buffering, level metering."""

from __future__ import annotations

import array
import collections
import math
import sys
import threading
import wave
from collections.abc import Callable
from pathlib import Path

from .log import get_logger

log = get_logger("audio")

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # int16
INT16_MAX = 32768.0


class AudioError(RuntimeError):
    """User-facing audio problem (no device, permission denied, ...)."""


def rms_level(pcm: bytes) -> float:
    """Normalized RMS (0..1) of little-endian int16 mono PCM."""
    n = len(pcm) // SAMPLE_WIDTH
    if n == 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm[: n * SAMPLE_WIDTH])
    if sys.byteorder == "big":
        samples.byteswap()
    acc = 0
    for s in samples:
        acc += s * s
    return min(1.0, math.sqrt(acc / n) / INT16_MAX)


class BoundedPcmBuffer:
    """Thread-safe FIFO of PCM chunks capped at ``max_bytes``; drops the oldest data on overflow."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._chunks: collections.deque[bytes] = collections.deque()
        self._size = 0
        self._lock = threading.Lock()
        self.dropped_bytes = 0

    def push(self, chunk: bytes) -> None:
        with self._lock:
            self._chunks.append(chunk)
            self._size += len(chunk)
            while self._size > self.max_bytes and len(self._chunks) > 1:
                old = self._chunks.popleft()
                self._size -= len(old)
                self.dropped_bytes += len(old)

    def get_all(self) -> bytes:
        with self._lock:
            return b"".join(self._chunks)

    def drain(self) -> bytes:
        with self._lock:
            data = b"".join(self._chunks)
            self._chunks.clear()
            self._size = 0
            return data

    def __len__(self) -> int:
        with self._lock:
            return self._size


def save_wav(path: Path, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


def _resolve_device(sd, device: str):
    """Map the config string to a sounddevice device id (None = default)."""
    if not device:
        return None
    if device.isdigit():
        return int(device)
    needle = device.lower()
    for idx, info in enumerate(sd.query_devices()):
        if info.get("max_input_channels", 0) > 0 and needle in info["name"].lower():
            return idx
    raise AudioError(f"no input device matching '{device}' (see: python -m sounddevice)")


class AudioCapture:
    """Streams microphone audio. ``on_chunk(bytes)`` runs on the audio thread — keep it fast.

    ``stream_factory`` lets tests inject a fake instead of opening real hardware.
    """

    def __init__(
        self,
        on_chunk: Callable[[bytes], None],
        *,
        device: str = "",
        blocksize: int = 1600,
        max_buffer_seconds: float = 120.0,
        stream_factory: Callable[..., object] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.on_chunk = on_chunk
        self.on_error = on_error
        self.device = device
        self.blocksize = blocksize
        self.buffer = BoundedPcmBuffer(int(max_buffer_seconds * SAMPLE_RATE * SAMPLE_WIDTH))
        self._stream_factory = stream_factory
        self._stream = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            self.buffer.drain()
            try:
                if self._stream_factory is not None:
                    factory = self._stream_factory
                    device = self.device or None
                else:
                    import sounddevice as sd  # lazy: tests/CI have no PortAudio

                    factory = sd.RawInputStream
                    device = _resolve_device(sd, self.device)
                stream = factory(
                    samplerate=SAMPLE_RATE,
                    blocksize=self.blocksize,
                    dtype="int16",
                    channels=1,
                    device=device,
                    callback=self._callback,
                    finished_callback=self._finished,
                )
                stream.start()  # type: ignore[attr-defined]
            except AudioError:
                raise
            except Exception as e:  # PortAudioError, ImportError, OSError ...
                raise AudioError(f"cannot open microphone: {e}") from e
            self._stream = stream

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.warning("audio status: %s", status)
        chunk = bytes(indata)
        self.buffer.push(chunk)
        try:
            self.on_chunk(chunk)
        except Exception:  # never let a consumer bug kill the audio thread
            log.exception("on_chunk failed")

    def _finished(self) -> None:
        """PortAudio calls this when the stream ends. If we didn't ask for it, the device went away."""
        if self._stream is not None and self.on_error is not None:
            log.error("audio stream ended unexpectedly (device unplugged?)")
            try:
                self.on_error("Microphone disconnected")
            except Exception:  # noqa: BLE001
                log.exception("on_error failed")

    def stop(self) -> bytes:
        """Stop capturing and return everything buffered (bounded by ``max_buffer_seconds``)."""
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()  # type: ignore[attr-defined]
                stream.close()  # type: ignore[attr-defined]
            except Exception:
                log.exception("closing audio stream failed")
        return self.buffer.drain()
