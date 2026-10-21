"""Segmented streaming for batch providers (Google free, Whisper, …).

Those providers need a whole utterance per request, so they can't stream natively. This wrapper
makes them *feel* like streaming:

* the live audio is split at pauses (simple adaptive VAD); every finished segment is sent
  immediately, in the background, and its text is committed in order;
* while you are still talking, the current segment is re-sent every ``interim_seconds`` so the
  overlay shows the sentence growing;
* on stop only the last (short) segment is left to transcribe, so the text lands almost instantly.

Failed segments are retried at the end, and if the VAD never heard speech the whole recording is
transcribed as a fallback — streaming can only make the result arrive sooner, never lose audio.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

from ..audio import SAMPLE_RATE, SAMPLE_WIDTH, rms_level
from ..log import get_logger
from .base import (
    CancelToken,
    StreamSession,
    STTBackend,
    STTCancelled,
    STTError,
    STTResult,
    STTUnintelligibleError,
)
from .chunking import transcribe_chunked

log = get_logger("stt.segmenting")

BYTES_PER_SECOND = SAMPLE_RATE * SAMPLE_WIDTH
CALIBRATION_CHUNKS = 6
MIN_THRESHOLD = 2e-4
MAX_THRESHOLD = 0.03  # if you start talking during calibration, don't mistake your voice for the noise floor
MIN_INTERIM_GROWTH = 0.5  # seconds of new audio before an interim request is worth sending
PREROLL_SECONDS = 0.3  # keep a little audio before speech starts so the first syllable isn't clipped


class SegmentedStream(StreamSession):
    def __init__(
        self,
        backend: STTBackend,
        *,
        interim_seconds: float = 1.5,
        pause_seconds: float = 0.6,
        max_segment_seconds: float = 12.0,
        cancel: CancelToken | None = None,
        threaded: bool = True,
        clock: Callable[[], float] = time.monotonic,
        join_timeout: float = 70.0,
        on_update: Callable[[str], None] | None = None,
    ) -> None:
        self.backend = backend
        self.interim_seconds = interim_seconds
        self.pause_seconds = pause_seconds
        self.max_segment_seconds = max_segment_seconds
        self.cancel = cancel
        self._clock = clock
        self._join_timeout = join_timeout
        self._on_update = on_update
        self._lock = threading.RLock()

        # live audio state (feed() thread)
        self._buf = bytearray()
        self._all = bytearray()
        self._heard = False
        self._silent = 0.0
        self._calib: list[float] = []
        self._threshold = 0.0
        self._seg = 0  # id of the segment currently being recorded
        self._last_interim_t = clock()
        self._last_interim_len = 0

        # results (worker thread)
        self._results: dict[int, str] = {}
        self._failed: dict[int, bytes] = {}
        self._errors: list[STTError] = []
        self._interim = ""
        self._interim_seg = -1
        self._interim_inflight = False
        self._aborted = False
        self._fatal: Exception | None = None  # unexpected (non-STT) error from the backend
        self.requests = 0  # how many backend calls were made (for tests / diagnostics)

        self._q: queue.Queue[tuple[str, int, bytes] | None] = queue.Queue()
        self._threaded = threaded
        self._thread: threading.Thread | None = None
        if threaded:
            self._thread = threading.Thread(target=self._worker, daemon=True, name="stt-segments")
            self._thread.start()

    # ------------------------------------------------------------------ public
    @property
    def text(self) -> str:
        with self._lock:
            parts = [self._results[k] for k in sorted(self._results) if self._results[k]]
            if self._interim and self._interim_seg >= 0:
                parts.append(self._interim)
            return " ".join(parts)

    def feed(self, pcm: bytes) -> str:
        if self._aborted:
            return ""
        level = rms_level(pcm)
        seconds = len(pcm) / BYTES_PER_SECOND
        with self._lock:
            self._all += pcm
            self._buf += pcm
            if not self._threshold:
                self._calib.append(level)
                if len(self._calib) >= CALIBRATION_CHUNKS:
                    self._calibrate()
                # Until calibrated nothing is trimmed or cut: you may already be talking.
                return self.text
            speech = level >= self._threshold
            if speech:
                self._heard = True
                self._silent = 0.0
            else:
                self._silent += seconds

            if not self._heard:
                keep = int(PREROLL_SECONDS * BYTES_PER_SECOND)
                if len(self._buf) > keep:
                    del self._buf[:-keep]
            elif self._silent >= self.pause_seconds or len(self._buf) / BYTES_PER_SECOND >= self.max_segment_seconds:
                self._close_segment()
            else:
                self._maybe_interim()
        return self.text

    def _calibrate(self) -> None:
        """Pick the noise threshold from the first chunks, then look back: speech during calibration counts."""
        self._threshold = min(max(min(self._calib) * 2, MIN_THRESHOLD), MAX_THRESHOLD)
        loud = [i for i, lv in enumerate(self._calib) if lv >= self._threshold]
        if loud:
            self._heard = True
            self._silent = (len(self._calib) - 1 - loud[-1]) * 0.1  # trailing quiet chunks

    def finish(self) -> STTResult:
        with self._lock:
            if not self._threshold and self._calib:
                self._calibrate()  # very short recording: fewer chunks than the calibration window
            if self._heard:
                self._close_segment()
        self._drain()
        if not self._aborted:
            self._retry_failed()
        with self._lock:
            have_text = any(self._results.values())
            everything = bytes(self._all)
        if not have_text and self._seg == 0 and not self._aborted and not self._errors and self._fatal is None and len(everything) > BYTES_PER_SECOND // 2:
            # The VAD never fired (very quiet mic?) — transcribe the whole recording in one go.
            log.info("no speech segments detected; transcribing the full recording")
            return transcribe_chunked(self.backend, everything, self.cancel)
        if not have_text and self._fatal is not None:
            raise self._fatal
        if not have_text and self._errors:
            raise self._errors[0]
        return STTResult(self.text_final(), True)

    def text_final(self) -> str:
        with self._lock:
            return " ".join(self._results[k] for k in sorted(self._results) if self._results[k])

    def abort(self) -> None:
        self._aborted = True
        self._q.put(None)

    # ------------------------------------------------------------------ internals
    def _close_segment(self) -> None:
        audio = bytes(self._buf)
        seg = self._seg
        self._seg += 1
        self._buf = bytearray()
        self._heard = False
        self._silent = 0.0
        self._last_interim_len = 0
        self._last_interim_t = self._clock()
        self._submit(("final", seg, audio))

    def _maybe_interim(self) -> None:
        if self.interim_seconds <= 0 or self._interim_inflight:
            return
        grown = (len(self._buf) - self._last_interim_len) / BYTES_PER_SECOND
        if self._clock() - self._last_interim_t < self.interim_seconds or grown < MIN_INTERIM_GROWTH:
            return
        self._interim_inflight = True
        self._last_interim_t = self._clock()
        self._last_interim_len = len(self._buf)
        self._submit(("interim", self._seg, bytes(self._buf)))

    def _submit(self, job: tuple[str, int, bytes]) -> None:
        if self._threaded:
            self._q.put(job)
        else:
            self._process(job)

    def _worker(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            if not self._aborted:
                self._process(job)

    def _process(self, job: tuple[str, int, bytes]) -> None:
        kind, seg, audio = job
        if kind == "interim":
            with self._lock:
                stale = seg != self._seg  # the segment was closed while this job waited
                if stale:
                    self._interim_inflight = False
            if stale:
                return
        try:
            self.requests += 1
            text = self.backend.transcribe(audio, self.cancel).text.strip()
        except STTCancelled:
            return
        except STTUnintelligibleError:
            text = ""
        except STTError as e:
            with self._lock:
                if kind == "final":
                    self._failed[seg] = audio
                    self._errors.append(e)
                else:
                    self._interim_inflight = False
            log.warning("segment %d (%s) failed: %s", seg, kind, e.detail or e)
            return
        except Exception as e:  # noqa: BLE001 - a backend bug must not kill the worker thread
            log.exception("segment %d (%s) crashed", seg, kind)
            with self._lock:
                self._fatal = e
                self._interim_inflight = False
            return
        with self._lock:
            if kind == "final":
                self._results[seg] = text
                if self._interim_seg == seg:
                    self._interim, self._interim_seg = "", -1
            else:
                self._interim_inflight = False
                if seg == self._seg:
                    self._interim, self._interim_seg = text, seg
        self._notify()

    def _notify(self) -> None:
        if self._on_update is None or self._aborted:
            return
        try:
            self._on_update(self.text)
        except Exception:  # noqa: BLE001 - a UI problem must not break transcription
            log.exception("on_update failed")

    def _drain(self) -> None:
        """Wait until every queued segment has been processed."""
        if not self._threaded or self._thread is None:
            return
        self._q.put(None)  # sentinel: the worker finishes the queue, then exits
        self._thread.join(self._join_timeout)

    def _retry_failed(self) -> None:
        with self._lock:
            failed = sorted(self._failed.items())
        for seg, audio in failed:
            try:
                self.requests += 1
                text = self.backend.transcribe(audio, self.cancel).text.strip()
            except STTCancelled:
                return
            except STTUnintelligibleError:
                text = ""
            except STTError as e:
                log.warning("retry of segment %d failed: %s", seg, e.detail or e)
                continue
            with self._lock:
                self._results[seg] = text
                self._failed.pop(seg, None)
        with self._lock:
            if not self._failed:
                self._errors.clear()  # everything was recovered
