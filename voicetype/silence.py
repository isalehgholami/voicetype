"""Adaptive silence detection (pattern from hyprwhspr app/silence.py).

* Calibrates the noise floor from the first ~0.6 s of audio.
* Only counts silence *after* speech was heard ("armed"), so it can't fire while the
  user is still composing the first sentence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CALIBRATION_SAMPLES = 6  # × 100 ms
MIN_THRESHOLD = 2e-4


@dataclass
class SilenceDetector:
    """Feed one RMS level per ``poll_interval`` seconds; ``feed`` returns True when it's time to stop."""

    timeout_seconds: float
    poll_interval: float = 0.1
    fixed_threshold: float = 0.0  # >0 disables calibration
    threshold: float = field(default=0.0, init=False)
    armed: bool = field(default=False, init=False)
    _calib: list[float] = field(default_factory=list, init=False)
    _silent: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.fixed_threshold > 0:
            self.threshold = self.fixed_threshold
        # round(), not int(): 0.3 / 0.1 == 2.9999999999999996 would stop one tick early
        self._needed = max(1, round(self.timeout_seconds / self.poll_interval))

    @property
    def calibrated(self) -> bool:
        return self.threshold > 0

    def feed(self, level: float) -> bool:
        if not self.calibrated:
            self._calib.append(level)
            if len(self._calib) >= CALIBRATION_SAMPLES:
                self.threshold = max(min(self._calib) * 2, MIN_THRESHOLD)
            return False
        if level >= self.threshold:
            self.armed = True
            self._silent = 0
            return False
        if self.armed:
            self._silent += 1
            return self._silent >= self._needed
        return False


class LevelAccumulator:
    """Averages variable-size chunk levels into fixed 100 ms ticks for :class:`SilenceDetector`."""

    def __init__(self, samples_per_tick: int = 1600) -> None:
        self.samples_per_tick = samples_per_tick
        self._energy = 0.0
        self._count = 0

    def add(self, level: float, n_samples: int) -> list[float]:
        """Add a chunk's RMS ``level`` covering ``n_samples``; returns ticks completed (usually 0 or 1)."""
        out: list[float] = []
        self._energy += level * level * n_samples
        self._count += n_samples
        while self._count >= self.samples_per_tick:
            mean_sq = self._energy / self._count
            out.append(mean_sq**0.5)
            self._energy = 0.0
            self._count -= self.samples_per_tick
            if self._count:  # carry remainder with same energy density
                self._energy = mean_sq * self._count
        return out
