import struct
import threading
import time
import wave

import pytest

from voicetype import audio
from voicetype.audio import (
    AudioCapture,
    AudioError,
    BoundedPcmBuffer,
    PipeWireCapture,
    create_capture,
    pipewire_available,
    rms_level,
    save_wav,
)
from voicetype.silence import LevelAccumulator, SilenceDetector


def pcm(value, n=1600):
    return struct.pack("<h", value) * n


def test_rms_silence_and_full_scale():
    assert rms_level(b"") == 0.0
    assert rms_level(pcm(0)) == 0.0
    assert rms_level(pcm(32767)) > 0.99
    assert 0.0 <= rms_level(pcm(-32768)) <= 1.0


def test_rms_is_monotonic_and_stable():
    a, b = rms_level(pcm(1000)), rms_level(pcm(5000))
    assert 0 < a < b < 1
    assert rms_level(pcm(1000)) == a


def test_buffer_drops_oldest():
    buf = BoundedPcmBuffer(max_bytes=10)
    for i in range(5):
        buf.push(bytes([i]) * 4)
    assert len(buf) <= 10 + 4
    data = buf.get_all()
    assert data.endswith(bytes([4]) * 4)
    assert buf.dropped_bytes > 0
    assert buf.drain() == data and len(buf) == 0


class FakeStream:
    def __init__(self, **kw):
        self.kw = kw
        self.started = self.closed = False
        FakeStream.last = self

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


def test_capture_delivers_chunks_and_returns_buffer():
    got = []
    cap = AudioCapture(got.append, stream_factory=FakeStream)
    cap.start()
    assert cap.running and FakeStream.last.started
    assert FakeStream.last.kw["samplerate"] == 16000 and FakeStream.last.kw["channels"] == 1
    cb = FakeStream.last.kw["callback"]
    cb(pcm(1), 1600, None, None)
    cb(pcm(2), 1600, None, None)
    data = cap.stop()
    assert len(got) == 2 and len(data) == 2 * 3200
    assert not cap.running and FakeStream.last.closed


def test_capture_survives_consumer_exception():
    def boom(_):
        raise RuntimeError("x")

    cap = AudioCapture(boom, stream_factory=FakeStream)
    cap.start()
    FakeStream.last.kw["callback"](pcm(1), 1600, None, None)  # must not raise
    assert len(cap.stop()) == 3200


def test_capture_open_failure_is_readable():
    def bad(**kw):
        raise OSError("no such device")

    cap = AudioCapture(lambda c: None, stream_factory=bad)
    with pytest.raises(AudioError, match="cannot open microphone"):
        cap.start()
    assert not cap.running


def test_capture_10_minutes_of_audio_no_deadlock():
    """600 s of audio pushed from a thread while the consumer reads; buffer stays bounded."""
    cap = AudioCapture(lambda c: None, stream_factory=FakeStream, max_buffer_seconds=30)
    cap.start()
    cb = FakeStream.last.kw["callback"]
    chunk = pcm(10)
    done = threading.Event()

    def producer():
        for _ in range(6000):  # 6000 × 0.1 s = 600 s
            cb(chunk, 1600, None, None)
        done.set()

    t = threading.Thread(target=producer)
    t0 = time.time()
    t.start()
    while not done.is_set():
        cap.buffer.get_all()
    t.join(5)
    assert not t.is_alive() and time.time() - t0 < 30
    assert len(cap.buffer) <= 30 * 32000 + 3200
    cap.stop()


def test_save_wav_roundtrip(tmp_path):
    p = tmp_path / "a" / "x.wav"
    save_wav(p, pcm(7, 160))
    with wave.open(str(p)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 160)


def test_silence_waits_for_speech_then_stops():
    d = SilenceDetector(timeout_seconds=0.5)  # 5 ticks
    for _ in range(6):  # calibration on noise 0.001
        assert not d.feed(0.001)
    assert d.calibrated and d.threshold == pytest.approx(0.002)
    for _ in range(50):  # long silence before speech never stops
        assert not d.feed(0.001)
    assert d.feed(0.1) is False and d.armed
    results = [d.feed(0.001) for _ in range(5)]
    assert results == [False, False, False, False, True]


def test_silence_speech_resets_counter():
    d = SilenceDetector(timeout_seconds=0.3, fixed_threshold=0.01)
    d.feed(0.5)
    assert not d.feed(0.0) and not d.feed(0.0)
    d.feed(0.5)
    assert not d.feed(0.0) and not d.feed(0.0)
    assert d.feed(0.0)


def test_silence_noisy_room_not_stopped_early():
    d = SilenceDetector(timeout_seconds=0.3)
    for _ in range(6):
        d.feed(0.05)  # noisy room → threshold 0.1
    assert d.threshold == pytest.approx(0.1)
    d.feed(0.3)
    assert not d.feed(0.08) and not d.feed(0.08)
    assert d.feed(0.08)


def test_level_accumulator_ticks():
    acc = LevelAccumulator(1600)
    assert acc.add(0.1, 800) == []
    ticks = acc.add(0.1, 800)
    assert len(ticks) == 1 and ticks[0] == pytest.approx(0.1)
    assert len(acc.add(0.2, 3200)) == 2


# ------------------------------------------------------------------ PipeWire capture
class FakeStdout:
    """Mimics ``pw-cat``: it streams the given bytes and then keeps the pipe open."""

    def __init__(self, data: bytes, closed: threading.Event):
        self._data = data
        self._pos = 0
        self._closed = closed

    def read(self, n: int = -1) -> bytes:
        if self._pos >= len(self._data):
            self._closed.wait(timeout=5)
            return b""
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


class FakeStderr:
    def __init__(self, data: bytes):
        self._lines = [line + b"\n" for line in data.splitlines()]

    def __iter__(self):
        return iter(self._lines)


class FakeProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0, alive: bool = True):
        self.closed = threading.Event()
        self.stdout = FakeStdout(stdout, self.closed)
        self.stderr = FakeStderr(stderr)
        self.returncode = returncode
        self.signals: list[int] = []
        self.killed = False
        if not alive:  # the process died on its own
            self.closed.set()

    def wait(self, timeout=None) -> int:
        return self.returncode

    def send_signal(self, sig) -> None:
        self.signals.append(sig)
        self.closed.set()

    def kill(self) -> None:
        self.killed = True
        self.closed.set()


def fake_popen(proc: FakeProc, calls: list | None = None):
    def popen(cmd, **kwargs):
        if calls is not None:
            calls.append((cmd, kwargs))
        return proc

    return popen


def test_pipewire_capture_delivers_chunks_and_returns_the_buffer():
    got: list[bytes] = []
    proc = FakeProc(stdout=pcm(5) + pcm(7))
    calls: list = []
    cap = PipeWireCapture(got.append, popen=fake_popen(proc, calls))
    cap.start()
    time.sleep(0.2)
    data = cap.stop()
    assert b"".join(got) == pcm(5) + pcm(7)
    assert data == pcm(5) + pcm(7)
    assert not cap.running


def test_pipewire_capture_asks_pw_cat_for_16k_mono_s16():
    calls: list = []
    cap = PipeWireCapture(lambda c: None, popen=fake_popen(FakeProc(), calls))
    cap.start()
    time.sleep(0.1)
    cap.stop()
    cmd = calls[0][0]
    assert cmd[0] == "pw-cat"
    assert "--record" in cmd and "--raw" in cmd
    assert cmd[cmd.index("--rate") + 1] == "16000"
    assert cmd[cmd.index("--channels") + 1] == "1"
    assert cmd[cmd.index("--format") + 1] == "s16"
    assert cmd[-1] == "-"  # raw samples on stdout


def test_pipewire_capture_targets_a_named_node():
    calls: list = []
    cap = PipeWireCapture(
        lambda c: None,
        device="alsa_input.pci-0000_00_1f.3.analog-stereo",
        popen=fake_popen(FakeProc(), calls),
    )
    cap.start()
    time.sleep(0.1)
    cap.stop()
    cmd = calls[0][0]
    assert cmd[cmd.index("--target") + 1] == "alsa_input.pci-0000_00_1f.3.analog-stereo"


def test_pipewire_capture_reports_a_missing_tool():
    def popen(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    cap = PipeWireCapture(lambda c: None, popen=popen)
    with pytest.raises(AudioError, match="pipewire-audio"):
        cap.start()
    assert not cap.running


def test_pipewire_capture_reports_an_early_death():
    errors: list[str] = []
    proc = FakeProc(stderr=b"stream node 210 error: no target node available", returncode=1, alive=False)
    cap = PipeWireCapture(lambda c: None, on_error=errors.append, popen=fake_popen(proc))
    cap.start()
    time.sleep(0.3)
    assert len(errors) == 1
    assert errors[0].startswith("Microphone unavailable:")
    assert "no target node available" in errors[0]


def test_pipewire_capture_stays_quiet_when_asked_to_stop():
    errors: list[str] = []
    proc = FakeProc(stdout=pcm(1))
    cap = PipeWireCapture(lambda c: None, on_error=errors.append, popen=fake_popen(proc))
    cap.start()
    time.sleep(0.1)
    cap.stop()
    time.sleep(0.2)
    assert errors == []


def test_pipewire_capture_keeps_whole_samples_only():
    got: list[bytes] = []
    proc = FakeProc(stdout=pcm(3) + b"\x01")  # a trailing half sample
    cap = PipeWireCapture(got.append, popen=fake_popen(proc))
    cap.start()
    time.sleep(0.2)
    cap.stop()
    assert all(len(c) % 2 == 0 for c in got)


# ------------------------------------------------------------------ backend selection
def test_pipewire_available_uses_the_session_socket(tmp_path):
    assert pipewire_available({"XDG_RUNTIME_DIR": str(tmp_path)}) is False
    (tmp_path / "pipewire-0").write_text("")
    assert pipewire_available({"XDG_RUNTIME_DIR": str(tmp_path)}) is True
    assert pipewire_available({"PIPEWIRE_REMOTE": "pipewire-0"}) is True


def test_create_capture_prefers_pipewire_on_a_pipewire_desktop(monkeypatch):
    monkeypatch.setattr(audio, "pipewire_available", lambda env=None: True)
    assert isinstance(create_capture(lambda c: None), PipeWireCapture)


def test_create_capture_falls_back_to_portaudio_without_pipewire(monkeypatch):
    monkeypatch.setattr(audio, "pipewire_available", lambda env=None: False)
    assert isinstance(create_capture(lambda c: None), AudioCapture)


def test_create_capture_honours_a_pinned_device(monkeypatch):
    monkeypatch.setattr(audio, "pipewire_available", lambda env=None: True)
    assert isinstance(create_capture(lambda c: None, device="hw:0,0"), AudioCapture)


def test_create_capture_honours_an_explicit_backend(monkeypatch):
    monkeypatch.setattr(audio, "pipewire_available", lambda env=None: False)
    assert isinstance(create_capture(lambda c: None, backend="pipewire"), PipeWireCapture)
    assert isinstance(create_capture(lambda c: None, backend="portaudio"), AudioCapture)


def test_create_capture_ignores_an_unknown_backend(monkeypatch):
    monkeypatch.setattr(audio, "pipewire_available", lambda env=None: False)
    assert isinstance(create_capture(lambda c: None, backend="nonsense"), AudioCapture)
