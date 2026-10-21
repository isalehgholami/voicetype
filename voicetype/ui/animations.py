"""Animation primitives for the overlay.

Two envelopes are left here: the fade that shows and hides the card, and the
adaptive gain that lets a quiet microphone still fill the waveform.  The rest
of the motion — the crossfades between the states, the colour of the status
dot, the travelling wave of the bars — lives with the card itself, in
:mod:`voicetype.ui.hud`.

All of it is clock-driven and injectable, so the timings can be unit-tested
without a compositor or a real clock.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .easing import clamp, ease_out_cubic

__all__ = [
    "AutoGain",
    "FadeEnvelope",
]


class AutoGain:
    """Adaptive gain so quiet microphones still fill the meter.

    Mic amplitude spans roughly 30 dB across hardware: a USB condenser with
    internal makeup gain peaks near full scale while a distant XLR mic through
    an interface preamp can sit two orders of magnitude lower. A fixed
    multiplier tuned for the former renders the latter as a flat line.

    The envelope has an instant attack and a slow release, and scales towards
    :attr:`TARGET_PEAK`. :attr:`min_gain` preserves the previous fixed-gain
    behaviour for hot microphones, so only quiet inputs are boosted. Room tone
    is left alone: an envelope under the noise floor means nobody is speaking,
    so no amplification.
    """

    TARGET_PEAK = 0.7
    MAX_GAIN = 80.0
    RELEASE = 0.995  # per-frame decay; ~3 s time constant at 60 FPS
    GATE_FRACTION = 0.2  # relaxed gate as a share of the envelope

    def __init__(self, min_gain: float, noise_floor: float) -> None:
        self.min_gain = min_gain
        self.noise_floor = noise_floor
        self.envelope = 0.0

    def update(self, peak: float) -> float:
        """Feed this frame's peak amplitude and get the gain to apply to it."""
        if peak > self.envelope:
            self.envelope = peak
        else:
            self.envelope *= self.RELEASE
        if self.envelope <= self.noise_floor:
            return self.min_gain
        gain = self.TARGET_PEAK / self.envelope
        return max(self.min_gain, min(self.MAX_GAIN, gain))

    def reset(self) -> None:
        """Forget the tracked envelope.

        The meter only runs while the overlay is visible, so a stale envelope
        would otherwise carry the previous recording's loudest moment into the
        next one and hold the gain down there.
        """
        self.envelope = 0.0

    def gate(self, default: float) -> float:
        """Scale a fixed noise gate down for quiet inputs.

        Quiet speech would otherwise sit entirely beneath the default gate.
        Silence keeps the full gate, so room tone is held down rather than
        boosted into the meter.
        """
        if self.envelope <= self.noise_floor:
            return default
        return min(default, self.envelope * self.GATE_FRACTION)


class FadeEnvelope:
    """A one-dimensional fade used for the overlay's show/hide transitions.

    The value moves towards the target with an ease-out curve, so the overlay
    appears promptly but settles softly. ``settled`` tells the frame loop when
    it can stop ticking.
    """

    def __init__(self, duration_ms: int = 140, clock: Callable[[], float] = time.monotonic) -> None:
        self.duration = max(0.0, duration_ms / 1000.0)
        self._clock = clock
        self._from = 0.0
        self._to = 0.0
        self._value = 0.0
        self._started_at = clock()

    @property
    def target(self) -> float:
        return self._to

    @property
    def value(self) -> float:
        """The current opacity in ``0..1``."""
        return self._value_at(self._clock())

    @property
    def settled(self) -> bool:
        """``True`` when the fade has reached its target."""
        return abs(self.value - self._to) < 1e-4

    def set_target(self, target: float) -> bool:
        """Start a fade towards ``target``. Returns ``True`` when it changed."""
        target = clamp(target, 0.0, 1.0)
        if abs(target - self._to) < 1e-6:
            return False
        now = self._clock()
        self._from = self._value_at(now)
        self._to = target
        self._started_at = now
        return True

    def _value_at(self, now: float) -> float:
        if self.duration <= 0.0:
            return self._to
        t = clamp((now - self._started_at) / self.duration, 0.0, 1.0)
        return self._from + (self._to - self._from) * ease_out_cubic(t)
