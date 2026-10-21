import struct
import threading
import time

import pytest

from voicetype.audio import AudioError
from voicetype.config import Config, parse_config
from voicetype.dispatcher import Components, Dispatcher, State
from voicetype.stt.base import (
    CancelToken,
    StreamSession,
    STTBackend,
    STTCancelled,
    STTNetworkError,
    STTResult,
    STTUnintelligibleError,
)

LOUD = struct.pack("<h", 6000) * 1600  # 100 ms
QUIET = struct.pack("<h", 3) * 1600


class FakeAudio:
    last = None

    def __init__(self, on_chunk, fail=False):
        self.on_chunk = on_chunk
        self.fail = fail
        self.buf = []
        self.started = self.stopped = False
        FakeAudio.last = self

    def start(self):
        if self.fail:
            raise AudioError("cannot open microphone: busy")
        self.started = True

    def push(self, chunk):
        self.buf.append(chunk)
        self.on_chunk(chunk)

    def stop(self):
        self.stopped = True
        data, self.buf = b"".join(self.buf), []
        return data


class Overlay:
    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        def rec(*a):
            self.events.append((name, *a))

        return rec

    def names(self):
        return [e[0] for e in self.events]


class Backend(STTBackend):
    name = "fake"

    def __init__(self, text="سلام دنیا", exc=None, delay=0.0):
        self.text, self.exc, self.delay = text, exc, delay
        self.calls = []

    def transcribe(self, pcm, cancel=None):
        self.calls.append(len(pcm))
        end = time.time() + self.delay
        while time.time() < end:
            if cancel:
                cancel.raise_if_cancelled()
            time.sleep(0.01)
        if self.exc:
            raise self.exc
        return STTResult(self.text)


class Injector:
    def __init__(self, exc=None):
        self.texts, self.exc = [], exc

    def inject(self, text):
        if self.exc:
            raise self.exc
        self.texts.append(text)


def make(backend=None, injector=None, pipeline=lambda t: t, cfg=None, audio_fail=False, build=None):
    backend = backend or Backend()
    injector = injector or Injector()
    overlay = Overlay()
    comps = Components(cfg or Config(), backend, injector, pipeline)
    d = Dispatcher(
        comps,
        lambda on_chunk, c, on_err: FakeAudio(on_chunk, fail=audio_fail),
        overlay,
        build=build,
        error_display_seconds=0.05,
    )
    return d, backend, injector, overlay


def speak(n=5):
    for _ in range(n):
        FakeAudio.last.push(LOUD)


def test_full_cycle():
    d, backend, inj, ov = make()
    assert d.start() == "ok listening" and d.state is State.LISTENING
    speak()
    assert d.stop() == "ok transcribing"
    assert d.wait_idle()
    assert inj.texts == ["سلام دنیا"] and backend.calls == [5 * 3200]
    names = ov.names()
    assert names[0] == "show_listening" and "show_processing" in names
    # the overlay is hidden before the paste, then confirms the injection
    assert names.index("hide") < names.index("show_success")
    assert any(e[0] == "set_audio_level" for e in ov.events)


def test_toggle_semantics_and_busy_states():
    d, backend, inj, ov = make(backend=Backend(delay=0.3))
    d.toggle()
    assert d.state is State.LISTENING
    speak()
    d.toggle()
    assert d.state is State.TRANSCRIBING
    assert d.toggle().startswith("busy")  # ignored while transcribing
    assert d.start().startswith("busy")
    assert d.wait_idle()
    assert len(inj.texts) == 1


def test_stop_when_not_listening():
    d, *_ = make()
    assert d.stop().startswith("not listening")


def test_cancel_while_listening_injects_nothing():
    d, backend, inj, ov = make()
    d.start()
    speak()
    assert d.cancel() == "ok cancelled"
    assert d.state is State.IDLE and inj.texts == [] and backend.calls == []
    assert FakeAudio.last.stopped and ov.names()[-1] == "hide"


def test_cancel_during_transcription_is_fast_and_silent():
    d, backend, inj, ov = make(backend=Backend(delay=5))
    d.start()
    speak()
    d.stop()
    time.sleep(0.1)
    t0 = time.time()
    d.cancel()
    assert d.state is State.IDLE and time.time() - t0 < 0.3
    d.wait_idle()
    time.sleep(0.1)
    assert inj.texts == []


def test_cancel_when_idle_is_noop():
    d, *_ = make()
    assert d.cancel() == "ok idle"


def test_new_session_after_cancelled_one_is_not_polluted():
    d, backend, inj, ov = make(backend=Backend(delay=0.2))
    d.start()
    speak()
    d.stop()
    d.cancel()
    d.start()  # new session while the old worker is still winding down
    speak()
    d.stop()
    d.wait_idle(3)
    time.sleep(0.4)
    assert inj.texts == ["سلام دنیا"]


@pytest.mark.parametrize(
    "exc,fragment",
    [
        (STTNetworkError("x"), "Network"),
        (STTUnintelligibleError(), "understand"),
        (RuntimeError("boom"), "boom"),
    ],
)
def test_stt_errors_return_to_idle_with_overlay_message(exc, fragment):
    d, backend, inj, ov = make(backend=Backend(exc=exc))
    d.start()
    speak()
    d.stop()
    d.wait_idle()
    assert d.state is State.IDLE and inj.texts == []
    errs = [e for e in ov.events if e[0] == "show_error"]
    assert errs and fragment in errs[0][1]
    time.sleep(0.2)
    assert ov.names()[-1] == "hide"
    assert d.start() == "ok listening"  # not wedged


def test_injector_failure_returns_to_idle():
    d, backend, inj, ov = make(injector=Injector(exc=RuntimeError("ydotoold not running")))
    d.start()
    speak()
    d.stop()
    d.wait_idle()
    assert d.state is State.IDLE
    assert any(e[0] == "show_error" and "ydotoold" in e[1] for e in ov.events)


def test_audio_open_failure():
    d, backend, inj, ov = make(audio_fail=True)
    assert d.start().startswith("error")
    assert d.state is State.IDLE
    assert any(e[0] == "show_error" for e in ov.events)


def test_empty_recording_is_reported_not_stuck():
    d, backend, inj, ov = make()
    d.start()
    d.stop()
    d.wait_idle()
    assert d.state is State.IDLE and backend.calls == []


def test_postprocessing_to_empty_means_no_injection():
    d, backend, inj, ov = make(pipeline=lambda t: "")
    d.start()
    speak()
    d.stop()
    d.wait_idle()
    assert inj.texts == [] and d.state is State.IDLE


def test_pipeline_applied_before_injection():
    d, backend, inj, ov = make(pipeline=lambda t: t + "!")
    d.start()
    speak()
    d.stop()
    d.wait_idle()
    assert inj.texts == ["سلام دنیا!"]


def test_auto_stop_after_silence():
    cfg = parse_config({"stt": {"silence_threshold_seconds": 0.5}})
    d, backend, inj, ov = make(cfg=cfg)
    d.start()
    for _ in range(8):
        FakeAudio.last.push(QUIET)  # calibration (0.6 s)
    speak(3)  # speech arms the detector
    for _ in range(6):  # 0.6 s of silence ≥ 0.5 s timeout
        FakeAudio.last.push(QUIET)
    deadline = time.time() + 3
    while d.state is State.LISTENING and time.time() < deadline:
        time.sleep(0.01)
    d.wait_idle(3)
    time.sleep(0.2)
    assert inj.texts == ["سلام دنیا"]


def test_no_auto_stop_before_speech():
    cfg = parse_config({"stt": {"silence_threshold_seconds": 0.5}})
    d, *_ = make(cfg=cfg)
    d.start()
    for _ in range(40):
        FakeAudio.last.push(QUIET)
    time.sleep(0.1)
    assert d.state is State.LISTENING
    d.cancel()


def test_max_recording_length_stops():
    cfg = parse_config({"audio": {"max_buffer_seconds": 5}})
    d, backend, inj, ov = make(cfg=cfg)
    d.start()
    for _ in range(52):
        FakeAudio.last.push(LOUD)
    deadline = time.time() + 3
    while d.state is State.LISTENING and time.time() < deadline:
        time.sleep(0.01)
    d.wait_idle(3)
    assert d.state is not State.LISTENING


class FakeStream(StreamSession):
    def __init__(self):
        self.n = 0

    def feed(self, pcm):
        self.n += 1
        return f"partial{self.n}"

    def finish(self):
        return STTResult("final text")


class StreamBackend(Backend):
    streaming = True

    def open_stream(self):
        return FakeStream()


def test_streaming_backend_shows_partials_and_uses_final():
    d, backend, inj, ov = make(backend=StreamBackend())
    d.start()
    speak(3)
    time.sleep(0.1)
    d.stop()
    d.wait_idle()
    assert inj.texts == ["final text"] and backend.calls == []  # batch path not used
    partials = [e[1] for e in ov.events if e[0] == "set_partial"]
    assert partials and partials[0].startswith("partial")


def test_streaming_partials_hidden_when_disabled():
    cfg = parse_config({"overlay": {"show_partial_text": False}})
    d, backend, inj, ov = make(backend=StreamBackend(), cfg=cfg)
    d.start()
    speak(2)
    time.sleep(0.05)
    d.stop()
    d.wait_idle()
    assert not any(e[0] == "set_partial" for e in ov.events)


def test_reload_applies_immediately_when_idle_and_after_cycle_when_busy():
    new_backend = Backend(text="new")

    def build(cfg):
        return Components(cfg, new_backend, Injector(), lambda t: t)

    d, old_backend, inj, ov = make(backend=Backend(delay=0.2), build=build)
    d.start()
    speak()
    d.stop()
    assert d.reload_config(Config()) == "ok reload queued"
    assert d.components.backend is old_backend
    d.wait_idle()
    time.sleep(0.1)
    assert d.components.backend is new_backend
    assert d.reload_config(Config()) == "ok reloaded"


def test_reload_build_failure_keeps_old_config():
    def build(cfg):
        raise RuntimeError("bad config")

    d, old, *_ = make(build=build)
    with pytest.raises(RuntimeError):
        d.reload_config(Config())
    assert d.components.backend is old


def test_concurrent_toggles_never_corrupt_state():
    d, backend, inj, ov = make(backend=Backend(delay=0.01))
    errors = []

    def hammer():
        try:
            for _ in range(30):
                d.toggle()
                if FakeAudio.last and d.state is State.LISTENING:
                    speak(1)
                time.sleep(0.002)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=hammer) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    d.cancel()
    d.wait_idle(3)
    assert not errors and d.state is State.IDLE


def test_shutdown_cancels_and_closes_backend():
    closed = []
    backend = Backend()
    backend.close = lambda: closed.append(1)
    d, *_ = make(backend=backend)
    d.start()
    d.shutdown()
    assert d.state is State.IDLE and closed == [1]


def test_cancel_token_object():
    t = CancelToken()
    t.raise_if_cancelled()
    t.cancel()
    with pytest.raises(STTCancelled):
        t.raise_if_cancelled()
