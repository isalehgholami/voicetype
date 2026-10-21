import struct
import threading
import time

import pytest

from tests.test_dispatcher import Backend, FakeAudio, Injector, Overlay, make
from voicetype.config import Config, parse_config
from voicetype.dispatcher import Components, Dispatcher, State
from voicetype.stt.base import STTBackend, STTNetworkError, STTResult, STTUnintelligibleError
from voicetype.stt.segmenting import SegmentedStream

LOUD = struct.pack("<h", 6000) * 1600  # 100 ms of speech
QUIET = struct.pack("<h", 3) * 1600  # 100 ms of room noise
VERY_QUIET = struct.pack("<h", 1) * 1600  # below the minimum threshold


class Scripted(STTBackend):
    """Returns s0, s1, … in call order and records the audio length of each call."""

    name = "scripted"

    def __init__(self, fail_first=0, exc=None, unintelligible=()):
        self.calls: list[int] = []
        self.fail_first = fail_first
        self.exc = exc or STTNetworkError("down")
        self.unintelligible = set(unintelligible)

    def transcribe(self, pcm, cancel=None):
        n = len(self.calls)
        self.calls.append(len(pcm))
        if n < self.fail_first:
            raise self.exc
        if n in self.unintelligible:
            raise STTUnintelligibleError()
        return STTResult(f"s{n}")


def feed(stream, pattern):
    out = ""
    for ch in pattern:
        out = stream.feed({"Q": QUIET, "L": LOUD, "V": VERY_QUIET}[ch])
    return out


def inline(backend=None, **kw):
    kw.setdefault("interim_seconds", 0)
    return SegmentedStream(backend or Scripted(), threaded=False, **kw)


# ------------------------------------------------------------------ segmentation
def test_segment_is_sent_at_the_pause_not_at_the_end():
    b = Scripted()
    s = inline(b)
    assert feed(s, "QQQQQQ" + "LLLLLLLL") == ""  # still talking: nothing sent yet
    assert b.calls == []
    assert feed(s, "QQQQQQQ") == "s0"  # 0.7 s of silence ≥ 0.6 s pause → sent immediately
    assert len(b.calls) == 1
    r = s.finish()
    assert r.text == "s0" and len(b.calls) == 1  # nothing left to do at the end


def test_multiple_sentences_are_joined_in_order():
    b = Scripted()
    s = inline(b)
    feed(s, "QQQQQQ" + "LLLLL" + "QQQQQQQ" + "LLLLL" + "QQQQQQQ" + "LLL")
    assert s.finish().text == "s0 s1 s2"
    assert len(b.calls) == 3
    assert b.calls[2] < b.calls[0]  # the tail is short: that is the whole point


def test_speaking_from_the_very_first_instant_loses_no_audio():
    """Calibration window overlaps your first words — they must still be transcribed."""
    b = Scripted()
    s = inline(b)
    feed(s, "LLLLLLLLLL" + "QQQQQQQ")
    assert s.finish().text == "s0"
    assert b.calls[0] >= 10 * 3200  # all ten speech chunks are in the segment


def test_very_short_utterance_shorter_than_calibration_window():
    b = Scripted()
    s = inline(b)
    feed(s, "LLLL")
    assert s.finish().text == "s0" and b.calls == [4 * 3200]


def test_long_monologue_is_cut_at_max_segment_length():
    b = Scripted()
    s = inline(b, max_segment_seconds=2.0)
    feed(s, "Q" * 6 + "L" * 45)  # 4.5 s of unbroken speech
    s.finish()
    assert len(b.calls) >= 3 and all(c <= 2.0 * 32000 + 3200 for c in b.calls)


def test_vad_blind_recording_still_gets_transcribed_whole():
    b = Scripted()
    s = inline(b)
    feed(s, "V" * 12)  # nothing crosses the threshold
    assert s.finish().text == "s0"
    assert b.calls == [12 * 3200]


def test_pure_silence_returns_empty_without_a_request():
    b = Scripted()
    s = inline(b)
    feed(s, "Q" * 3)  # shorter than 0.5 s of audio
    assert s.finish().text == "" and b.calls == []


# ------------------------------------------------------------------ interim (live) text
def test_interim_text_grows_while_you_talk_and_is_replaced_by_the_final():
    now = [0.0]
    b = Scripted()
    s = SegmentedStream(b, threaded=False, interim_seconds=1.0, clock=lambda: now[0])
    seen = []
    for ch in "Q" * 6 + "L" * 30:
        now[0] += 0.1
        seen.append(s.feed(LOUD if ch == "L" else QUIET))
    shown = [t for t in seen if t]
    assert shown and shown[0] == "s0" and shown[-1] != "s0"  # the sentence in progress was refreshed
    assert len(b.calls) >= 3 and b.calls == sorted(b.calls)  # each interim carries more audio
    now[0] += 0.1
    for _ in range(7):
        now[0] += 0.1
        s.feed(QUIET)  # pause → final
    final = s.finish().text
    assert final.count("s") == 1  # the interim texts are not appended to the result, only the final one


def test_interim_disabled_sends_only_at_pauses():
    b = Scripted()
    s = inline(b, interim_seconds=0)
    feed(s, "Q" * 6 + "L" * 40)
    assert b.calls == []


def test_no_interim_requests_pile_up_while_one_is_in_flight():
    now = [0.0]
    gate = threading.Event()

    class Slow(Scripted):
        def transcribe(self, pcm, cancel=None):
            gate.wait(2)
            return super().transcribe(pcm, cancel)

    b = Slow()
    s = SegmentedStream(b, interim_seconds=0.5, clock=lambda: now[0])
    for _ in range(40):
        now[0] += 0.1
        s.feed(LOUD)
    time.sleep(0.1)
    assert len(b.calls) <= 1  # the first interim is still running; no second one queued
    gate.set()
    s.finish()


# ------------------------------------------------------------------ resilience
def test_failed_segment_is_retried_at_the_end_and_order_is_kept():
    b = Scripted(fail_first=1)  # the first (final) request fails
    s = inline(b)
    feed(s, "Q" * 6 + "L" * 5 + "Q" * 7 + "L" * 5 + "Q" * 7)
    r = s.finish()
    assert r.text == "s2 s1"  # segment 0 was retried last (s2) but still comes first
    assert len(b.calls) == 3


def test_all_requests_failing_raises_the_network_error():
    s = inline(Scripted(fail_first=99))
    feed(s, "Q" * 6 + "L" * 5 + "Q" * 7)
    with pytest.raises(STTNetworkError):
        s.finish()


def test_unintelligible_segments_are_skipped_not_fatal():
    s = inline(Scripted(unintelligible={0}))
    feed(s, "Q" * 6 + "L" * 5 + "Q" * 7 + "L" * 5 + "Q" * 7)
    assert s.finish().text == "s1"


def test_backend_bug_is_reported_and_does_not_kill_the_worker_thread():
    class Boom(STTBackend):
        def transcribe(self, pcm, cancel=None):
            raise RuntimeError("boom")

    s = SegmentedStream(Boom(), interim_seconds=0)
    feed(s, "Q" * 6 + "L" * 5 + "Q" * 7)
    with pytest.raises(RuntimeError, match="boom"):
        s.finish()


def test_abort_stops_all_work():
    b = Scripted()
    s = SegmentedStream(b, interim_seconds=0)
    s.abort()
    feed(s, "Q" * 6 + "L" * 5 + "Q" * 7)
    time.sleep(0.1)
    assert b.calls == []


def test_threaded_mode_waits_for_slow_requests_at_finish():
    class Slow(Scripted):
        def transcribe(self, pcm, cancel=None):
            time.sleep(0.2)
            return super().transcribe(pcm, cancel)

    b = Slow()
    s = SegmentedStream(b, interim_seconds=0)
    feed(s, "Q" * 6 + "L" * 5 + "Q" * 7 + "L" * 3)
    t0 = time.time()
    assert s.finish().text == "s0 s1"
    assert time.time() - t0 >= 0.15


# ------------------------------------------------------------------ through the dispatcher
def stream_cfg(**stt):
    return parse_config({"stt": {"stream_interim_seconds": 0, "fallback_provider": "", **stt}})


def push(chars):
    for ch in chars:
        FakeAudio.last.push(LOUD if ch == "L" else QUIET)


def wait_for(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_text_appears_in_the_overlay_while_still_recording():
    d, backend, inj, ov = make(cfg=stream_cfg())
    d.start()
    push("Q" * 6 + "L" * 8 + "Q" * 7)  # a sentence, then a pause — user has NOT stopped yet
    assert wait_for(lambda: any(e[0] == "set_partial" and e[1] == "سلام دنیا" for e in ov.events))
    assert d.state is State.LISTENING and inj.texts == []
    assert len(backend.calls) == 1
    d.stop()
    d.wait_idle()
    time.sleep(0.1)
    assert inj.texts == ["سلام دنیا"]
    assert len(backend.calls) == 1  # nothing re-sent at the end: the text was ready already


def test_batch_mode_still_available():
    d, backend, inj, ov = make(cfg=stream_cfg(streaming=False))
    d.start()
    push("Q" * 6 + "L" * 8)
    d.stop()
    d.wait_idle()
    assert backend.calls == [14 * 3200]  # one request with the whole recording
    assert not any(e[0] == "set_partial" for e in ov.events)


def test_native_streaming_backend_is_preferred_over_segmenting():
    from tests.test_dispatcher import StreamBackend

    d, backend, inj, ov = make(backend=StreamBackend(), cfg=stream_cfg())
    d.start()
    push("Q" * 6 + "L" * 8 + "Q" * 7)
    time.sleep(0.1)
    d.stop()
    d.wait_idle()
    assert inj.texts == ["final text"] and backend.calls == []


def test_cancel_while_streaming_sends_nothing_more():
    d, backend, inj, ov = make(cfg=stream_cfg())
    d.start()
    push("Q" * 6 + "L" * 8)
    d.cancel()
    push("Q" * 7)
    time.sleep(0.2)
    assert d.state is State.IDLE and inj.texts == []


def test_network_error_in_a_segment_surfaces_after_stop():
    d, backend, inj, ov = make(backend=Backend(exc=STTNetworkError("x")), cfg=stream_cfg())
    d.start()
    push("Q" * 6 + "L" * 8 + "Q" * 7)
    time.sleep(0.2)
    d.stop()
    d.wait_idle()
    assert d.state is State.IDLE and inj.texts == []
    assert any(e[0] == "show_error" and "Network" in e[1] for e in ov.events)


def test_defaults_enable_streaming_and_validate():
    c = Config().stt
    assert c.streaming and c.stream_interim_seconds == 1.5 and c.stream_pause_seconds == 0.6
    with pytest.raises(Exception, match="stream_pause_seconds"):
        parse_config({"stt": {"stream_pause_seconds": 99}})


def test_unused_imports_guard():
    assert Components and Dispatcher and Injector and Overlay
