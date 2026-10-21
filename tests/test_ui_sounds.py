"""Tests for the overlay's audible cues.

Nothing here plays audio: the cues are rendered into a WAV file and read
back with :mod:`wave`, and the player is replaced by a fake.  The tests
therefore run on a machine without an audio device.
"""

from __future__ import annotations

import math
import struct
import wave

import pytest

from voicetype.ui.sounds import PLAYERS, SAMPLE_RATE, SOUNDS, SoundPlayer, render_wav, write_wav

# ------------------------------------------------------------------ helpers


def read_wav(path):
    """Read a WAV file back as ``(rate, frames)``."""
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    return rate, frames


def goertzel(samples, rate, frequency):
    """The energy of ``samples`` at ``frequency`` (the Goertzel filter)."""
    coefficient = 2.0 * math.cos(2.0 * math.pi * frequency / rate)
    previous = older = 0.0
    for sample in samples:
        current = sample + coefficient * previous - older
        older, previous = previous, current
    return previous * previous + older * older - coefficient * previous * older


def dominant_frequency(samples, rate, low=200.0, high=900.0):
    """The frequency with the most energy in ``samples``."""
    best = (0.0, -1.0)
    for candidate in range(int(low), int(high) + 1):
        power = goertzel(samples, rate, float(candidate))
        if power > best[1]:
            best = (float(candidate), power)
    return best[0]


class FakeProcess:
    """A player process that exited with a given code."""

    def __init__(self, code: int = 0) -> None:
        self.code = code

    def wait(self) -> int:
        return self.code


class FakeRunner:
    """Records the commands a player was asked to run."""

    def __init__(self, code: int = 0) -> None:
        self.commands: list[list[str]] = []
        self.code = code

    def __call__(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        return FakeProcess(self.code)


class BrokenRunner:
    """A player that blows up on the spot."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, cmd, **kwargs):
        self.calls += 1
        raise OSError("no such device")


def drain(player: SoundPlayer) -> None:
    """Wait for the background cue threads to finish."""
    for thread in list(player._threads):
        thread.join(timeout=5)


# ------------------------------------------------------------------ synthesis


def test_up_is_a_600_hz_tone_of_150_ms(tmp_path):
    path = write_wav(tmp_path / "up.wav", SOUNDS["up"])
    rate, frames = read_wav(path)

    assert rate == SAMPLE_RATE
    assert len(frames) / 2 / rate == pytest.approx(0.150, abs=0.005)

    samples = [value / 32768.0 for value in _unpack(frames)]
    assert dominant_frequency(samples, rate) == pytest.approx(600.0, abs=3.0)


def test_down_is_a_400_hz_tone_of_150_ms(tmp_path):
    path = write_wav(tmp_path / "down.wav", SOUNDS["down"])
    rate, frames = read_wav(path)

    assert len(frames) / 2 / rate == pytest.approx(0.150, abs=0.005)

    samples = [value / 32768.0 for value in _unpack(frames)]
    assert dominant_frequency(samples, rate) == pytest.approx(400.0, abs=3.0)


def test_error_is_two_400_hz_tones_of_340_ms(tmp_path):
    path = write_wav(tmp_path / "error.wav", SOUNDS["error"])
    rate, frames = read_wav(path)

    assert len(frames) / 2 / rate == pytest.approx(0.340, abs=0.005)

    samples = [value / 32768.0 for value in _unpack(frames)]
    assert dominant_frequency(samples, rate) == pytest.approx(400.0, abs=3.0)


def test_the_cue_durations_are_the_documented_ones():
    assert SOUNDS["up"].duration_ms == 150
    assert SOUNDS["down"].duration_ms == 150
    assert SOUNDS["error"].duration_ms == 340
    assert SOUNDS["up"].frequency == 600.0
    assert SOUNDS["down"].frequency == 400.0
    assert SOUNDS["error"].frequency == 400.0


def test_render_wav_returns_a_readable_wav():
    data = render_wav(SOUNDS["up"])
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"


def test_the_cue_is_not_silent(tmp_path):
    path = write_wav(tmp_path / "up.wav", SOUNDS["up"])
    _rate, frames = read_wav(path)
    samples = [value / 32768.0 for value in _unpack(frames)]
    assert max(abs(value) for value in samples) > 0.1


# ------------------------------------------------------------------ playing


def test_an_unknown_sound_name_is_ignored(tmp_path):
    runner = FakeRunner()
    player = SoundPlayer(enabled=True, which=lambda name: "/usr/bin/" + name, run=runner)

    player.play("nonsense")
    drain(player)

    assert runner.commands == []


def test_a_disabled_player_does_nothing(tmp_path):
    runner = FakeRunner()
    player = SoundPlayer(enabled=False, which=lambda name: "/usr/bin/" + name, run=runner)

    player.play("up")
    drain(player)

    assert runner.commands == []


def test_a_missing_player_is_a_no_op():
    runner = FakeRunner()
    player = SoundPlayer(enabled=True, which=lambda name: None, run=runner)

    player.play("up")
    drain(player)

    assert runner.commands == []


def test_a_failing_player_does_not_raise():
    runner = BrokenRunner()
    player = SoundPlayer(enabled=True, which=lambda name: "/usr/bin/" + name, run=runner)

    player.play("up")
    drain(player)
    player.close()

    assert runner.calls == 1


def test_a_player_that_exits_with_an_error_does_not_raise():
    runner = FakeRunner(code=1)
    player = SoundPlayer(enabled=True, which=lambda name: "/usr/bin/" + name, run=runner)

    player.play("up")
    drain(player)
    player.close()

    assert len(runner.commands) == 1


def test_the_first_available_player_is_used():
    runner = FakeRunner()
    available = {"paplay": "/usr/bin/paplay", "ffplay": "/usr/bin/ffplay"}
    player = SoundPlayer(
        enabled=True,
        which=lambda name: available.get(name),
        run=runner,
    )

    player.play("up")
    drain(player)
    player.close()

    assert runner.commands[0][0] == "/usr/bin/paplay"


def test_the_player_order_prefers_pipewire():
    assert PLAYERS[0] == "pw-play"
    assert set(PLAYERS) == {"pw-play", "paplay", "aplay", "ffplay"}


def test_the_cue_is_rendered_into_a_file_the_player_can_read(tmp_path):
    runner = FakeRunner()
    player = SoundPlayer(
        enabled=True,
        which=lambda name: "/usr/bin/" + name,
        run=runner,
        directory=tmp_path,
    )

    player.play("up")
    drain(player)

    played = runner.commands[0][-1]
    rate, frames = read_wav(played)
    assert rate == SAMPLE_RATE
    assert frames


def test_close_removes_the_rendered_cues(tmp_path):
    player = SoundPlayer(
        enabled=True,
        which=lambda name: "/usr/bin/" + name,
        run=FakeRunner(),
        directory=tmp_path,
    )
    player.play("up")
    drain(player)
    assert list(tmp_path.glob("*.wav"))

    player.close()
    assert not list(tmp_path.glob("*.wav"))


def _unpack(frames: bytes) -> list[int]:
    """The signed 16-bit samples of a mono frame buffer."""
    count = len(frames) // 2
    return list(struct.unpack(f"<{count}h", frames[: count * 2]))
