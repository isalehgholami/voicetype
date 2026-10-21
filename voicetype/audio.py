"""Audio capture: 16 kHz mono int16, bounded buffering, level metering.

Two backends are available:

* :class:`AudioCapture` -- PortAudio (``sounddevice``).
* :class:`PipeWireCapture` -- the ``pw-cat`` command-line client of the
  PipeWire server.

On a PipeWire desktop the second one is the correct choice; see
:class:`PipeWireCapture` for why.
"""

from __future__ import annotations

import array
import collections
import math
import os
import signal
import subprocess
import sys
import threading
import wave
from collections.abc import Callable, Mapping
from pathlib import Path

from .log import get_logger

log = get_logger("audio")

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # int16
INT16_MAX = 32768.0

#: The PipeWire capture tool, shipped by ``pipewire-audio``.
PIPEWIRE_TOOL = "pw-cat"


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


def pipewire_available(env: Mapping[str, str] | None = None) -> bool:
    """True when this session has a PipeWire server to talk to."""
    env = os.environ if env is None else env
    if env.get("PIPEWIRE_REMOTE"):
        return True
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime, "pipewire-0").exists()


class PipeWireCapture:
    """Streams the microphone through the PipeWire server.

    PortAudio's ALSA backend opens the raw codec (``hw:0,0``) and keeps it
    open exclusively.  PipeWire's own capture of the very same device then
    fails with ``EBUSY``, its input node drops into an error state, and the
    microphone disappears from the whole desktop -- it only works inside this
    program.  ``pw-cat`` is a native PipeWire client: the server keeps owning
    the hardware, so every other application can still record.

    The interface mirrors :class:`AudioCapture`.
    """

    def __init__(
        self,
        on_chunk: Callable[[bytes], None],
        *,
        device: str = "",
        blocksize: int = 1600,
        max_buffer_seconds: float = 120.0,
        on_error: Callable[[str], None] | None = None,
        tool: str = PIPEWIRE_TOOL,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self.on_chunk = on_chunk
        self.on_error = on_error
        self.device = device
        self.blocksize = blocksize
        self.buffer = BoundedPcmBuffer(int(max_buffer_seconds * SAMPLE_RATE * SAMPLE_WIDTH))
        self._tool = tool
        self._popen = popen
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr: list[str] = []
        self._lock = threading.Lock()
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._proc is not None

    def _command(self) -> list[str]:
        cmd = [
            self._tool,
            "--record",
            "--raw",
            "--rate",
            str(SAMPLE_RATE),
            "--channels",
            "1",
            "--format",
            "s16",
        ]
        if self.device:
            cmd += ["--target", self.device]
        cmd.append("-")
        return cmd

    def start(self) -> None:
        with self._lock:
            if self._proc is not None:
                return
            self.buffer.drain()
            self._stderr = []
            self._stopping = False
            try:
                self._proc = self._popen(
                    self._command(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
            except FileNotFoundError as e:
                raise AudioError(
                    f"cannot open microphone: {self._tool} is not installed (pacman -S pipewire-audio)"
                ) from e
            except OSError as e:
                raise AudioError(f"cannot open microphone: {e}") from e
            self._reader = threading.Thread(target=self._read, name="pipewire-capture", daemon=True)
            self._reader.start()

    def _read(self) -> None:
        """Move the raw stream into the buffer and hand every chunk to the consumer."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        pending = b""
        try:
            while True:
                chunk = proc.stdout.read(self.blocksize * SAMPLE_WIDTH)
                if not chunk:
                    break
                pending += chunk
                # only whole samples: the level meter counts frames, not bytes
                whole = len(pending) - (len(pending) % SAMPLE_WIDTH)
                ready, pending = pending[:whole], pending[whole:]
                if not ready:
                    continue
                self.buffer.push(ready)
                try:
                    self.on_chunk(ready)
                except Exception:  # never let a consumer bug kill the audio thread
                    log.exception("on_chunk failed")
        except Exception:
            log.exception("reading the PipeWire stream failed")
        finally:
            self._read_stderr(proc)
            self._finish(proc)

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        """Drain stderr; a full pipe would block the capture process."""
        if proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                text = line.decode("utf-8", "replace").strip()
                if text:
                    self._stderr.append(text)
        except (OSError, ValueError):
            pass

    def _finish(self, proc: subprocess.Popen) -> None:
        try:
            code = proc.wait()
        except OSError:
            code = -1
        if self._stopping:
            return
        detail = self._stderr[-1] if self._stderr else f"{self._tool} exited with code {code}"
        log.error("PipeWire capture ended unexpectedly: %s", detail)
        if self.on_error is not None:
            try:
                self.on_error(f"Microphone unavailable: {detail}")
            except Exception:  # noqa: BLE001
                log.exception("on_error failed")

    def stop(self) -> bytes:
        """Stop capturing and return everything buffered (bounded by ``max_buffer_seconds``)."""
        with self._lock:
            proc, self._proc = self._proc, None
            self._stopping = True
        if proc is not None:
            try:
                proc.send_signal(signal.SIGINT)
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.join(timeout=3)
        return self.buffer.drain()


def create_capture(
    on_chunk: Callable[[bytes], None],
    *,
    device: str = "",
    backend: str = "auto",
    blocksize: int = 1600,
    max_buffer_seconds: float = 120.0,
    on_error: Callable[[str], None] | None = None,
) -> AudioCapture | PipeWireCapture:
    """Pick the capture backend that fits this system.

    ``auto`` prefers PipeWire when the session has one and the user did not
    pin a device: PortAudio's ALSA backend takes the codec away from the
    audio server, which breaks the microphone for the entire desktop.
    """
    mode = (backend or "auto").strip().lower()
    if mode not in ("auto", "pipewire", "portaudio"):
        mode = "auto"
    if mode == "pipewire" or (mode == "auto" and not device and pipewire_available()):
        return PipeWireCapture(
            on_chunk,
            on_error=on_error,
            device=device,
            blocksize=blocksize,
            max_buffer_seconds=max_buffer_seconds,
        )
    return AudioCapture(
        on_chunk,
        on_error=on_error,
        device=device,
        blocksize=blocksize,
        max_buffer_seconds=max_buffer_seconds,
    )
