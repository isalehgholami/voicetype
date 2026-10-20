import struct
import threading
import time
import wave

import pytest

from voicetype.audio import AudioCapture, AudioError, BoundedPcmBuffer, rms_level, save_wav
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
