"""Integration: a fixed WAV file drives the whole pipeline (no microphone, no network)."""

import time
import wave
from pathlib import Path

from voicetype.config import Config
from voicetype.dispatcher import Components, Dispatcher, State
from voicetype.postprocess import Pipeline
from voicetype.stt.base import STTBackend, STTResult

FIXTURE = Path(__file__).parent / "fixtures" / "hello.wav"
BLOCK = 1600 * 2  # 100 ms of int16


class WavAudio:
    """Audio source that replays a WAV file as if it were the microphone."""

    def __init__(self, on_chunk, path=FIXTURE):
        self.on_chunk = on_chunk
        with wave.open(str(path)) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
            self.pcm = w.readframes(w.getnframes())
        self.buf = bytearray()

    def start(self):
        for i in range(0, len(self.pcm), BLOCK):
            chunk = self.pcm[i : i + BLOCK]
            self.buf += chunk
            self.on_chunk(chunk)

    def stop(self):
        return bytes(self.buf)


class RecordingBackend(STTBackend):
    name = "wav-fake"

    def __init__(self):
        self.received = 0

    def transcribe(self, pcm, cancel=None):
        self.received = len(pcm)
        return STTResult("  من از پایتون و qt استفاده می کنم ")


class Injector:
    def __init__(self):
        self.texts = []

    def inject(self, text):
        self.texts.append(text)


def test_wav_through_silence_detection_stt_postprocess_and_injection():
    backend, inj = RecordingBackend(), Injector()
    comps = Components(Config(), backend, inj, Pipeline(Config().postprocess))
    d = Dispatcher(comps, lambda cb, cfg, on_err: WavAudio(cb), error_display_seconds=0.01)
    assert d.start() == "ok listening"
    # the WAV ends with 3.5 s of silence → the 3 s silence timeout stops the session by itself
    deadline = time.time() + 5
    while d.state is State.LISTENING and time.time() < deadline:
        time.sleep(0.01)
    assert d.wait_idle(5)
    time.sleep(0.1)
    assert backend.received > 0
    assert inj.texts == ["من از Python و Qt استفاده می‌کنم."]
    assert d.state is State.IDLE
