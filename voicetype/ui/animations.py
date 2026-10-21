"""Animation primitives for the overlay.

The overlay is a state machine, and every visual state has an envelope: a
value in ``0..1`` that says how "active" the state is right now. The recording
state breathes, processing flows, errors strobe and success fades away. The
visualizations read those envelopes and turn them into pixels.

All of it is clock-driven and injectable, so the timings can be unit-tested
without a compositor or a real clock.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from enum import Enum

from .easing import clamp, ease_out_cubic
from .theme import TOKYO_NIGHT, Palette

__all__ = [
    "VisualizerState",
    "StateManager",
    "AutoGain",
    "FadeEnvelope",
]


class VisualizerState(Enum):
    """The visual states the overlay can be in."""

    RECORDING = "recording"
    PAUSED = "paused"
    PROCESSING = "processing"
    ERROR = "error"
    SUCCESS = "success"


class StateManager:
    """Tracks the current visual state and its animation envelope.

    ``animation_phase`` is a free-running phase accumulator; the per-state
    envelopes are built from it, so switching states never produces a jump in
    the phase (only the envelope itself changes).
    """

    #: Radians added per second to :attr:`animation_phase`.
    PHASE_RATE = 9.42  # ~1.5 Hz

    def __init__(
        self,
        palette: Palette | None = None,
        clock: Callable[[], float] = time.monotonic,
        animated: bool = True,
    ) -> None:
        self.palette = palette or TOKYO_NIGHT
        self._clock = clock
        self.animated = bool(animated)
        self.current_state = VisualizerState.RECORDING
        self.state_changed_at = clock()
        self.animation_phase = 0.0

    # ------------------------------------------------------------------ state
    def set_state(self, new_state: VisualizerState) -> bool:
        """Switch state. Returns ``True`` when the state actually changed."""
        if new_state == self.current_state:
            return False
        self.current_state = new_state
        self.state_changed_at = self._clock()
        return True

    def set_animated(self, animated: bool) -> None:
        """Turn the state animations on or off.

        With the animations off the phase stops advancing and every envelope
        is constant, so the overlay holds still instead of breathing, strobing
        or pulsing.  This is what ``overlay.animations = false`` switches off.
        """
        self.animated = bool(animated)

    def set_state_from_string(self, state: str) -> bool:
        """Switch state from its string form; unknown values keep the current state."""
        try:
            new_state = VisualizerState((state or "").strip().lower())
        except ValueError:
            return False
        return self.set_state(new_state)

    # ------------------------------------------------------------------ animation
    def update(self, dt: float = 1 / 60) -> None:
        """Advance the phase. ``dt`` is in seconds."""
        if not self.animated:
            return
        self.animation_phase = (self.animation_phase + self.PHASE_RATE * dt) % (2 * math.pi)

    @property
    def elapsed(self) -> float:
        """Seconds since the current state was entered."""
        return max(0.0, self._clock() - self.state_changed_at)

    def get_state_color(self) -> tuple:
        """The palette colour that represents the current state."""
        return {
            VisualizerState.RECORDING: self.palette.recording,
            VisualizerState.PAUSED: self.palette.paused,
            VisualizerState.PROCESSING: self.palette.processing,
            VisualizerState.ERROR: self.palette.error,
            VisualizerState.SUCCESS: self.palette.success,
        }[self.current_state]

    def get_animation_value(self) -> float:
        """The ``0..1`` envelope of the current state.

        * recording — a gentle 1.5 Hz breath between 0.7 and 1.0
        * paused — flat at 1.0 (nothing is happening, so nothing moves)
        * processing— a slow swell between 0.6 and 1.0
        * error     — a fast 6 Hz strobe between 0.3 and 1.0
        * success   — a pulse that fades to0 over one second

        With the animations switched off every envelope is a constant 1.0:
        the state is still shown, it simply does not move.
        """
        if not self.animated:
            return 1.0
        phase = self.animation_phase
        if self.current_state == VisualizerState.RECORDING:
            return 0.7 + 0.3 * math.sin(phase)
        if self.current_state == VisualizerState.PAUSED:
            return 1.0
        if self.current_state == VisualizerState.PROCESSING:
            return 0.6 + 0.4 * math.sin(phase)
        if self.current_state == VisualizerState.ERROR:
            return 0.3 + 0.7 * abs(math.sin(phase * 4))
        if self.current_state == VisualizerState.SUCCESS:
            elapsed = self.elapsed
            if elapsed > 1.0:
                return 0.0
            fade = 1.0 - elapsed
            pulse = 0.7 + 0.3 * math.sin(phase)
            return fade * pulse
        return 1.0


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
