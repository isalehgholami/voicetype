"""Dispatcher: the IDLE → LISTENING → TRANSCRIBING → INJECTING → IDLE state machine.

All transitions happen under one lock and are logged. UI calls go through the ``Overlay``
protocol, which implementations make thread-safe (GLib.idle_add), so no GUI thread is needed here.
"""

from __future__ import annotations

import enum
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .audio import SAMPLE_RATE, SAMPLE_WIDTH, AudioError, rms_level, save_wav
from .config import Config
from .log import default_log_dir, get_logger
from .silence import LevelAccumulator, SilenceDetector
from .stt.base import (
    CancelToken,
    StreamSession,
    STTBackend,
    STTCancelled,
    STTError,
    STTResult,
    STTUnintelligibleError,
)

log = get_logger("dispatcher")

ERROR_DISPLAY_SECONDS = 2.0


class State(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    INJECTING = "injecting"


class Overlay(Protocol):
    def show_listening(self) -> None: ...
    def set_partial(self, text: str) -> None: ...
    def set_audio_level(self, level: float) -> None: ...
    def show_processing(self) -> None: ...
    def show_error(self, message: str) -> None: ...
    def show_warning(self, message: str) -> None: ...
    def hide(self) -> None: ...


class NullOverlay:
    """Does nothing; used when the overlay is disabled or GTK is unavailable."""

    def show_listening(self) -> None: ...
    def set_partial(self, text: str) -> None: ...
    def set_audio_level(self, level: float) -> None: ...
    def show_processing(self) -> None: ...
    def show_error(self, message: str) -> None: ...
    def show_warning(self, message: str) -> None: ...
    def hide(self) -> None: ...


@dataclass
class Components:
    """Everything that depends on the active Config; swapped atomically on reload."""

    config: Config
    backend: STTBackend
    injector: Any  # .inject(text)
    pipeline: Callable[[str], str]


class Dispatcher:
    def __init__(
        self,
        components: Components,
        audio_factory: Callable[[Callable[[bytes], None], Config, Callable[[str], None]], Any],
        overlay: Overlay | None = None,
        build: Callable[[Config], Components] | None = None,
        error_display_seconds: float = ERROR_DISPLAY_SECONDS,
        media: Any = None,
        notifier: Any = None,
    ) -> None:
        self._c = components
        self._audio_factory = audio_factory
        self.overlay: Overlay = overlay or NullOverlay()
        self._build = build
        self._media = media
        self._notifier = notifier
        self._error_display = error_display_seconds
        self._lock = threading.RLock()
        self._state = State.IDLE
        self._session = 0
        self._audio: Any = None
        self._cancel: CancelToken | None = None
        self._stream: StreamSession | None = None
        self._stream_q: queue.Queue[bytes | None] | None = None
        self._stream_thread: threading.Thread | None = None
        self._silence: SilenceDetector | None = None
        self._acc = LevelAccumulator()
        self._samples = 0
        self._pending: Components | None = None
        self._worker: threading.Thread | None = None

    # ------------------------------------------------------------------ introspection
    @property
    def state(self) -> State:
        return self._state

    @property
    def components(self) -> Components:
        return self._c

    def status(self) -> str:
        return f"{self._state.value} provider={self._c.backend.name}"

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until the current session fully finished (used by tests and shutdown)."""
        deadline = time.monotonic() + timeout
        while self._state is not State.IDLE and time.monotonic() < deadline:
            time.sleep(0.005)
        return self._state is State.IDLE

    def _set_state(self, new: State, why: str) -> None:
        log.info("state %s -> %s (%s)", self._state.value, new.value, why)
        self._state = new

    # ------------------------------------------------------------------ commands (thread-safe)
    def toggle(self) -> str:
        with self._lock:
            if self._state is State.IDLE:
                return self.start()
            if self._state is State.LISTENING:
                return self.stop()
            log.info("toggle ignored while %s", self._state.value)
            return f"busy {self._state.value}"

    def start(self) -> str:
        with self._lock:
            if self._state is not State.IDLE:
                return f"busy {self._state.value}"
            cfg = self._c.config
            self._session += 1
            session = self._session
            self._cancel = CancelToken()
            self._stream = self._stream_q = self._stream_thread = None
            self._silence = SilenceDetector(cfg.stt.silence_threshold_seconds)
            self._acc = LevelAccumulator(int(0.1 * SAMPLE_RATE))
            self._samples = 0
            # LISTENING must be visible *before* the stream starts, otherwise the first chunks
            # (delivered from the audio thread) would be dropped by _on_chunk.
            self._set_state(State.LISTENING, "start")
            try:
                self._open_stream_if_supported()
                audio = self._audio_factory(
                    lambda chunk, s=session: self._on_chunk(s, chunk),
                    cfg,
                    lambda msg, s=session: self.on_audio_error(s, msg),
                )
                self._audio = audio
                audio.start()
            except (AudioError, STTError) as e:
                self._audio = None
                self._abort_stream()
                self._show_error(session, getattr(e, "user_message", None) or str(e), str(e))
                return f"error {e}"
            self.overlay.show_listening()
            if cfg.audio.pause_media and self._media is not None:
                self._media.pause()
            return "ok listening"

    def stop(self) -> str:
        """Finish listening and transcribe."""
        with self._lock:
            if self._state is not State.LISTENING:
                return f"not listening ({self._state.value})"
            session = self._session
            pcm = self._audio.stop()
            self._audio = None
            self._release_media()
            self._set_state(State.TRANSCRIBING, "stop")
            self.overlay.show_processing()
            if self._c.config.audio.debug_save_audio:
                self._save_debug(pcm)
            worker = threading.Thread(target=self._transcribe_and_inject, args=(session, pcm), daemon=True, name="stt-worker")
            worker.start()  # start before publishing: wait_idle() must never join an unstarted thread
            self._worker = worker
            return "ok transcribing"

    def cancel(self) -> str:
        with self._lock:
            if self._state is State.IDLE:
                return "ok idle"
            if self._state is State.INJECTING:
                return "busy injecting"
            log.info("cancel requested in %s", self._state.value)
            if self._cancel:
                self._cancel.cancel()
            if self._audio is not None:
                self._audio.stop()
                self._audio = None
            self._abort_stream()
            self._release_media()
            self._session += 1  # invalidate any in-flight worker result
            self._set_state(State.IDLE, "cancel")
            self.overlay.hide()
            self._apply_pending_locked()
            return "ok cancelled"

    def reload_config(self, new_config: Config) -> str:
        """Rebuild components for ``new_config``; applied now if idle, otherwise after the cycle."""
        if self._build is None:
            return "error reload unsupported"
        comps = self._build(new_config)  # may raise → caller reports, old config stays
        with self._lock:
            self._pending = comps
            if self._state is State.IDLE:
                self._apply_pending_locked()
                return "ok reloaded"
            return "ok reload queued"

    def _apply_pending_locked(self) -> None:
        if self._pending is not None and self._state is State.IDLE:
            old, self._c, self._pending = self._c.backend, self._pending, None
            if old is not self._c.backend:
                try:
                    old.close()
                except Exception:  # noqa: BLE001
                    log.exception("closing old backend failed")
            log.info("configuration applied")

    # ------------------------------------------------------------------ audio path (audio thread)
    def _on_chunk(self, session: int, chunk: bytes) -> None:
        if session != self._session or self._state is not State.LISTENING:
            return
        self._samples += len(chunk) // SAMPLE_WIDTH
        level = rms_level(chunk)
        self.overlay.set_audio_level(min(1.0, level * 4))  # speech RMS is small; boost for the meter
        if self._stream_q is not None:
            self._stream_q.put(chunk)
        stop = False
        for tick in self._acc.add(level, len(chunk) // SAMPLE_WIDTH):
            if self._silence and self._silence.feed(tick):
                stop = True
        max_samples = self._c.config.audio.max_buffer_seconds * SAMPLE_RATE
        if self._samples >= max_samples:
            log.info("max recording length reached")
            stop = True
        if stop:
            reason = "silence"
            threading.Thread(target=self._auto_stop, args=(session, reason), daemon=True).start()

    def on_audio_error(self, session: int, message: str) -> None:
        """Microphone vanished mid-recording: report it and return to IDLE (no half-recorded injection)."""
        with self._lock:
            if session != self._session or self._state is not State.LISTENING:
                return
            if self._cancel:
                self._cancel.cancel()
            self._audio = None
            self._abort_stream()
            self._release_media()
            self._show_error(session, message, message)

    def _auto_stop(self, session: int, reason: str) -> None:
        with self._lock:
            if session == self._session and self._state is State.LISTENING:
                log.info("auto-stop (%s)", reason)
                self.stop()

    # ------------------------------------------------------------------ streaming
    def _open_stream_if_supported(self) -> None:
        backend = self._c.backend
        if not backend.streaming:
            return
        stream = backend.open_stream()
        if stream is None:
            return
        self._stream = stream
        q: queue.Queue[bytes | None] = queue.Queue()
        self._stream_q = q
        show_partial = self._c.config.overlay.show_partial_text
        session = self._session

        def pump() -> None:
            last = ""
            while True:
                chunk = q.get()
                if chunk is None:
                    return
                try:
                    text = stream.feed(chunk)
                except Exception:  # noqa: BLE001
                    log.exception("stream feed failed")
                    return
                if show_partial and text != last and session == self._session:
                    last = text
                    self.overlay.set_partial(text)

        self._stream_thread = threading.Thread(target=pump, daemon=True, name="stt-stream")
        self._stream_thread.start()

    def _abort_stream(self) -> None:
        if self._stream_q is not None:
            self._stream_q.put(None)
        self._stream = self._stream_q = None

    # ------------------------------------------------------------------ worker
    def _transcribe_and_inject(self, session: int, pcm: bytes) -> None:
        cancel = self._cancel
        try:
            result = self._run_stt(pcm, cancel)
            if session != self._session:
                return  # cancelled meanwhile
            text = self._c.pipeline(result.text)
            if not text.strip():
                raise STTUnintelligibleError("empty after post-processing")
            with self._lock:
                if session != self._session:
                    return
                self._set_state(State.INJECTING, "stt done")
            self.overlay.hide()  # hide first so focus/paint can't interfere with the paste
            self._c.injector.inject(text)
        except STTCancelled:
            log.info("transcription cancelled")
        except STTError as e:
            if session == self._session:
                self._show_error(session, e.user_message, e.detail or repr(e))
            return
        except Exception as e:  # noqa: BLE001 - injector / unexpected: never wedge the daemon
            log.exception("session failed")
            if session == self._session:
                self._show_error(session, f"Error: {e}", repr(e))
            return
        with self._lock:
            if session == self._session:
                self._set_state(State.IDLE, "done")
                self._apply_pending_locked()

    def _run_stt(self, pcm: bytes, cancel: CancelToken | None) -> STTResult:
        stream, thread, q = self._stream, self._stream_thread, self._stream_q
        if stream is not None and q is not None:
            q.put(None)
            if thread:
                thread.join(5)
            self._stream = self._stream_q = None
            return stream.finish()
        if not pcm:
            raise STTUnintelligibleError("no audio captured")
        return self._c.backend.transcribe(pcm, cancel)

    # ------------------------------------------------------------------ errors / helpers
    def _show_error(self, session: int, message: str, detail: str) -> None:
        log.warning("error: %s", detail)
        with self._lock:
            if session != self._session:
                return
            self._set_state(State.IDLE, f"error: {detail}")
            self._release_media()
            self.overlay.show_error(message)
            if self._notifier is not None and self._c.config.notify_errors:
                self._notifier.error(message)
            self._apply_pending_locked()

        def later() -> None:
            with self._lock:
                if session == self._session and self._state is State.IDLE:
                    self.overlay.hide()

        timer = threading.Timer(self._error_display, later)
        timer.daemon = True
        timer.start()

    def _release_media(self) -> None:
        if self._media is not None:
            self._media.resume()

    def warn(self, message: str) -> None:
        """Surface a non-fatal warning (e.g. provider fallback) in the overlay."""
        self.overlay.show_warning(message)

    def _save_debug(self, pcm: bytes) -> None:
        try:
            save_wav(default_log_dir() / "last.wav", pcm)
        except OSError:
            log.exception("could not save debug audio")

    def shutdown(self) -> None:
        self.cancel()
        self.wait_idle(2)
        try:
            self._c.backend.close()
        except Exception:  # noqa: BLE001
            log.exception("backend close failed")
