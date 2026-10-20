import json
import struct
import threading
import time
import urllib.error

import pytest

from voicetype.config import Config, parse_config
from voicetype.metrics import cer, normalize, wer
from voicetype.stt import registry
from voicetype.stt.base import (
    CancelToken,
    STTBackend,
    STTCancelled,
    STTError,
    STTNetworkError,
    STTRateLimitError,
    STTResult,
    STTUnavailableError,
    STTUnintelligibleError,
)
from voicetype.stt.chunking import split_pcm, transcribe_chunked
from voicetype.stt.google_free import GoogleFreeBackend, classify_exception
from voicetype.stt.vosk_backend import VoskBackend
from voicetype.stt.whisper_backend import WhisperBackend

PCM_1S = struct.pack("<h", 500) * 16000


class UnknownValueError(Exception):
    pass


class RequestError(Exception):
    pass


# ------------------------------------------------------------------ google_free
def test_google_returns_text():
    b = GoogleFreeBackend("fa-IR", recognize=lambda pcm, lang: "سلام دنیا" if lang == "fa-IR" else "")
    assert b.transcribe(PCM_1S).text == "سلام دنیا"


def test_google_unintelligible():
    def rec(pcm, lang):
        raise UnknownValueError()

    with pytest.raises(STTUnintelligibleError):
        GoogleFreeBackend(recognize=rec).transcribe(PCM_1S)


def test_google_retries_network_then_succeeds():
    calls = []

    def rec(pcm, lang):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.URLError("down")
        return "ok"

    sleeps = []
    b = GoogleFreeBackend(recognize=rec, sleep=sleeps.append)
    assert b.transcribe(PCM_1S).text == "ok"
    assert len(calls) == 3 and sleeps == [0.5, 1.5]


def test_google_gives_up_after_two_retries():
    def rec(pcm, lang):
        raise urllib.error.URLError("down")

    with pytest.raises(STTNetworkError):
        GoogleFreeBackend(recognize=rec, sleep=lambda s: None).transcribe(PCM_1S)


def test_google_does_not_retry_unintelligible():
    calls = []

    def rec(pcm, lang):
        calls.append(1)
        raise UnknownValueError()

    with pytest.raises(STTUnintelligibleError):
        GoogleFreeBackend(recognize=rec, sleep=lambda s: None).transcribe(PCM_1S)
    assert len(calls) == 1


def test_google_cancel_midflight():
    started = threading.Event()

    def rec(pcm, lang):
        started.set()
        time.sleep(2)
        return "late"

    cancel = CancelToken()
    b = GoogleFreeBackend(recognize=rec, timeout=10)
    threading.Timer(0.2, cancel.cancel).start()
    t0 = time.time()
    with pytest.raises(STTCancelled):
        b.transcribe(PCM_1S, cancel)
    assert time.time() - t0 < 1.0 and started.is_set()


def test_google_timeout():
    def rec(pcm, lang):
        time.sleep(1)
        return "x"

    with pytest.raises(STTNetworkError, match="timed out"):
        GoogleFreeBackend(recognize=rec, timeout=0.2, sleep=lambda s: None).transcribe(PCM_1S)


@pytest.mark.parametrize(
    "exc,cls",
    [
        (UnknownValueError(), STTUnintelligibleError),
        (urllib.error.HTTPError("u", 429, "x", {}, None), STTRateLimitError),
        (urllib.error.HTTPError("u", 503, "x", {}, None), STTNetworkError),
        (urllib.error.URLError("x"), STTNetworkError),
        (TimeoutError(), STTNetworkError),
        (RequestError("recognition request failed: Too Many Requests quota"), STTRateLimitError),
        (RequestError("connection failed"), STTNetworkError),
        (ValueError("weird"), STTError),
    ],
)
def test_error_classification(exc, cls):
    assert type(classify_exception(exc)) is cls


# ------------------------------------------------------------------ chunking
def test_short_audio_single_chunk():
    assert split_pcm(PCM_1S) == [PCM_1S]


def test_two_minutes_split_under_limit_and_lossless():
    pcm = struct.pack("<h", 800) * (16000 * 120)
    chunks = split_pcm(pcm)
    assert len(chunks) == 3
    assert b"".join(chunks) == pcm
    assert all(len(c) <= 50 * 32000 for c in chunks)


def test_split_prefers_quiet_frame():
    loud, quiet = struct.pack("<h", 9000), struct.pack("<h", 0)
    sec = lambda v, n: v * (16000 * n)  # noqa: E731
    pcm = sec(loud, 47) + sec(quiet, 1) + sec(loud, 12)  # quiet gap at 47–48 s
    first = split_pcm(pcm)[0]
    assert 47 * 32000 <= len(first) <= 48 * 32000


def test_transcribe_chunked_two_minutes_no_error():
    class Echo(STTBackend):
        def transcribe(self, pcm, cancel=None):
            return STTResult("x")

    res = transcribe_chunked(Echo(), struct.pack("<h", 800) * (16000 * 120))
    assert res.text == "x x x"


def test_transcribe_chunked_skips_unintelligible_chunk():
    seq = iter([STTUnintelligibleError(), "b", "c"])

    class Flaky(STTBackend):
        def transcribe(self, pcm, cancel=None):
            v = next(seq)
            if isinstance(v, Exception):
                raise v
            return STTResult(v)

    assert transcribe_chunked(Flaky(), struct.pack("<h", 800) * (16000 * 120)).text == "b c"


# ------------------------------------------------------------------ vosk / whisper
class FakeRecognizer:
    def __init__(self, model, rate):
        self.n = 0

    def AcceptWaveform(self, data):  # noqa: N802
        self.n += 1
        return self.n % 3 == 0

    def Result(self):  # noqa: N802
        return json.dumps({"text": f"final{self.n}"})

    def PartialResult(self):  # noqa: N802
        return json.dumps({"partial": f"part{self.n}"})

    def FinalResult(self):  # noqa: N802
        return json.dumps({"text": "end"})


def test_vosk_stream_partials_and_final(tmp_path):
    b = VoskBackend(str(tmp_path), model_factory=lambda p: object(), recognizer_factory=FakeRecognizer)
    assert b.streaming
    s = b.open_stream()
    assert s.feed(b"x") == "part1"
    assert s.feed(b"x") == "part2"
    assert s.feed(b"x") == "final3"
    assert s.feed(b"x") == "final3 part4"
    assert s.finish().text == "final3 end"


def test_vosk_missing_model_message(tmp_path):
    b = VoskBackend(str(tmp_path / "nope"), model_factory=lambda p: object(), recognizer_factory=FakeRecognizer)
    with pytest.raises(STTUnavailableError, match="models install"):
        b.open_stream()


def test_vosk_transcribe_batch(tmp_path):
    b = VoskBackend(str(tmp_path), model_factory=lambda p: object(), recognizer_factory=FakeRecognizer)
    assert b.transcribe(PCM_1S * 3).text.endswith("end")


def test_whisper_backend_with_fake_model():
    class Seg:
        def __init__(self, t):
            self.text = t

    class FakeModel:
        def __init__(self, name, device, compute_type):
            self.args = (name, device, compute_type)

        def transcribe(self, audio, language, vad_filter):
            assert language == "fa"
            return iter([Seg(" سلام "), Seg("دنیا")]), None

    b = WhisperBackend("small", "cpu", "fa-IR", model_factory=FakeModel)
    assert b.transcribe(PCM_1S).text == "سلام دنیا"
    assert b._model.args == ("small", "cpu", "int8")


# ------------------------------------------------------------------ registry / fallback
def test_registry_lists_builtins_and_creates():
    assert {"google_free", "google_cloud", "vosk", "whisper"} <= set(registry.available())
    assert registry.create("google_free", registry.default_options()).name == "google_free"
    with pytest.raises(STTUnavailableError):
        registry.create("nope", registry.default_options())


def test_custom_provider_registration_needs_no_dispatcher_change():
    class Mine(STTBackend):
        name = "mine"

        def transcribe(self, pcm, cancel=None):
            return STTResult("mine")

    registry.register("mine", lambda o: Mine())
    assert registry.create("mine", registry.default_options()).transcribe(b"").text == "mine"


class Failing(STTBackend):
    name = "failing"

    def transcribe(self, pcm, cancel=None):
        raise STTNetworkError("x")


class Good(STTBackend):
    name = "good"
    streaming = True

    def transcribe(self, pcm, cancel=None):
        return STTResult("fallback text")


def test_fallback_after_three_consecutive_failures():
    notes = []
    fb = registry.FallbackBackend(Failing(), Good, after=3, on_fallback=notes.append)
    for _ in range(2):
        with pytest.raises(STTNetworkError):
            fb.transcribe(b"")
    assert fb.transcribe(b"").text == "fallback text"  # third failure triggers switch and serves the request
    assert fb.using_fallback and fb.streaming and len(notes) == 1 and "good" in notes[0]
    assert fb.transcribe(b"").text == "fallback text"
    assert len(notes) == 1


def test_fallback_counter_resets_on_success_and_ignores_unintelligible():
    state = {"n": 0}

    class Sometimes(STTBackend):
        name = "s"

        def transcribe(self, pcm, cancel=None):
            state["n"] += 1
            if state["n"] in (1, 2, 4, 5):
                raise STTNetworkError()
            if state["n"] == 6:
                raise STTUnintelligibleError()
            return STTResult("ok")

    fb = registry.FallbackBackend(Sometimes(), Good, after=3)
    for _ in range(2):
        with pytest.raises(STTNetworkError):
            fb.transcribe(b"")
    assert fb.transcribe(b"").text == "ok"  # resets
    for _ in range(2):
        with pytest.raises(STTNetworkError):
            fb.transcribe(b"")
    with pytest.raises(STTUnintelligibleError):
        fb.transcribe(b"")
    assert not fb.using_fallback


def test_fallback_unavailable_keeps_raising_primary_error():
    def bad():
        raise STTUnavailableError("no vosk")

    fb = registry.FallbackBackend(Failing(), bad, after=1)
    with pytest.raises(STTNetworkError):
        fb.transcribe(b"")
    assert not fb.using_fallback


def test_build_backend_wraps_when_fallback_configured():
    cfg = parse_config({})
    assert isinstance(registry.build_backend(cfg), registry.FallbackBackend)
    cfg2 = parse_config({"stt": {"fallback_provider": ""}})
    assert not isinstance(registry.build_backend(cfg2), registry.FallbackBackend)
    assert isinstance(Config(), Config)


# ------------------------------------------------------------------ metrics
def test_wer_cer():
    assert wer("سلام دنیا", "سلام دنیا") == 0
    assert wer("a b c d", "a b x d") == 0.25
    assert wer("", "") == 0 and wer("", "x") == 1
    assert cer("abcd", "abxd") == 0.25
    assert normalize("كتاب،  ي!") == "کتاب ی"
