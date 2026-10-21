"""Bar-style waveform visualization with a state indicator dot.

Ported from hyprwhspr's ``lib/mic_osd/visualizations/waveform.py``. The numpy
array maths were replaced with plain Python lists so VoiceType keeps its
"no heavy dependencies" promise (ADR-0001).

The waveform shows the raw microphone signal as a row of vertical bars, with
a pulsing state dot on the left. While the backend is transcribing, the bars
carry a travelling wave so the overlay reads as "busy" even in silence.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import NamedTuple

from ..animations import AutoGain, VisualizerState
from .base import BaseVisualization

__all__ = ["WaveformVisualization", "WaveBar", "bucket_rms"]


class WaveBar(NamedTuple):
    """One bar of the waveform, expressed in surface pixels."""

    x: float
    y: float
    width: float
    height: float
    r: float
    g: float
    b: float
    opacity: float


def bucket_rms(samples: Sequence[float], count: int) -> list[float]:
    """Split ``samples`` into ``count`` chunks and return each chunk's RMS.

    Any trailing samples that do not fill a whole chunk are dropped, so the
    result always has exactly ``count`` entries.
    """
    if count <= 0:
        return []
    total = len(samples)
    if total == 0:
        return [0.0] * count
    chunk = total // count
    if chunk <= 0:
        return [0.0] * count

    usable = samples[: chunk * count]
    out: list[float] = []
    for i in range(count):
        start = i * chunk
        square_sum = 0.0
        for value in usable[start : start + chunk]:
            sample = float(value)
            square_sum += sample * sample
        out.append(math.sqrt(square_sum / chunk))
    return out


class WaveformVisualization(BaseVisualization):
    """A bar-style audio visualization with a recording indicator."""

    style = "waveform"

    num_bars = 32
    bar_gap = 2.0
    min_bar_height = 2.0
    padding = 16.0
    indicator_width = 30.0

    def __init__(self, palette=None) -> None:
        super().__init__(palette)
        self.bar_heights: list[float] = [0.0] * self.num_bars
        # Smoothing for bar heights (makes the animation smoother)
        self.decay_rate = 0.85  # how fast bars fall
        self.rise_rate = 0.5  # how fast bars rise
        # Adaptive amplification; 4.0 was the old fixed value, now the floor
        self.auto_gain = AutoGain(min_gain=4.0, noise_floor=0.002)

    # ------------------------------------------------------------------ data
    def update(self, level: float, samples=None) -> None:
        """Update with new audio samples."""
        super().update(level, samples)

        if samples is not None and len(samples) > 0:
            chunk = len(samples) // self.num_bars
            if chunk > 0:
                # Use the RMS of each chunk for a smoother visualization
                rms = bucket_rms(samples, self.num_bars)
                gain = self.auto_gain.update(max(rms))
                new_heights = [min(1.0, value * gain) for value in rms]

                # Smooth transitions - rise fast, fall slow
                for i, new_height in enumerate(new_heights):
                    if new_height > self.bar_heights[i]:
                        # Rising - quick response
                        self.bar_heights[i] = (
                            self.rise_rate * new_height
                            + (1 - self.rise_rate) * self.bar_heights[i]
                        )
                    else:
                        # Falling - slow decay
                        self.bar_heights[i] *= self.decay_rate
                        if self.bar_heights[i] < new_height:
                            self.bar_heights[i] = new_height
        else:
            # No audio - decay all bars
            self.bar_heights = [height * self.decay_rate for height in self.bar_heights]

    # ------------------------------------------------------------------ paint
    def bar_specs(self, width: int, height: int) -> list[WaveBar]:
        """The bars of the current frame: geometry, colour and opacity."""
        padding = self.padding
        bars_start_x = padding + self.indicator_width
        bars_width = width - bars_start_x - padding
        bars_height = height - (padding * 2)
        center_y = height / 2

        bar_width = (bars_width - (self.num_bars - 1) * self.bar_gap) / self.num_bars
        total_bar_width = bar_width + self.bar_gap

        bar_left = self.palette.bar_left
        bar_right = self.palette.bar_right

        state = self.state_manager.current_state
        is_processing = state == VisualizerState.PROCESSING
        is_success = state == VisualizerState.SUCCESS
        wave_phase = self.state_manager.animation_phase if is_processing else 0.0
        pulse_value = self.state_manager.get_animation_value() if is_success else 1.0

        specs: list[WaveBar] = []
        for i in range(self.num_bars):
            # Interpolate the colour from left to right across the bars
            t = i / max(1, self.num_bars - 1)
            r = bar_left[0] * (1 - t) + bar_right[0] * t
            g = bar_left[1] * (1 - t) + bar_right[1] * t
            b = bar_left[2] * (1 - t) + bar_right[2] * t

            normalized_height = self.bar_heights[i]

            if is_processing:
                # A clean wave across all bars, with one subtle harmonic
                wave_pos = (i / max(1, self.num_bars - 1)) * 2 * math.pi + wave_phase
                primary_wave = 0.3 + 0.7 * math.sin(wave_pos)
                harmonic = 0.12 * math.sin(wave_pos * 2)
                wave_modulation = primary_wave + harmonic

                # Lift the baseline so the wave reaches recording-level
                # extremes even while the microphone is silent
                boosted = max(normalized_height, 0.7)
                normalized_height = max(0.0, min(1.0, boosted * wave_modulation))

                bar_h = max(self.min_bar_height, normalized_height * bars_height)
                # Simple opacity modulation (subtle, not distracting)
                opacity = 0.75 + 0.25 * (0.5 + 0.5 * math.sin(wave_pos))
            elif is_success:
                # All bars pulse together and fade out
                bar_h = max(self.min_bar_height, normalized_height * bars_height)
                bar_h *= 0.7 + 0.3 * pulse_value
                opacity = pulse_value
            else:
                bar_h = max(self.min_bar_height, normalized_height * bars_height)
                opacity = 1.0

            x = bars_start_x + i * total_bar_width
            specs.append(
                WaveBar(
                    x=x,
                    y=center_y - bar_h / 2,
                    width=bar_width,
                    height=bar_h,
                    r=r,
                    g=g,
                    b=b,
                    opacity=opacity,
                )
            )
        return specs

    def draw_background(self, cr, width: int, height: int) -> None:
        """Fill the surface and stroke the themed border."""
        cr.set_source_rgba(*self.palette.background)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        cr.set_source_rgba(*self.palette.border)
        cr.set_line_width(2)
        cr.rectangle(1, 1, width - 2, height - 2)
        cr.stroke()

    def draw(self, cr, width: int, height: int) -> None:
        """Draw the bar visualization with the recording indicator."""
        padding = self.padding
        center_y = height / 2

        # The recording indicator (just the dot) takes up the left side
        self.draw_state_dot(cr, padding + 6.0 + 4.0, center_y)

        for bar in self.bar_specs(width, height):
            # Glow effect
            cr.set_source_rgba(bar.r, bar.g, bar.b, 0.3 * bar.opacity)
            cr.rectangle(bar.x - 1, bar.y - 1, bar.width + 2, bar.height + 2)
            cr.fill()

            # Main bar
            cr.set_source_rgba(bar.r, bar.g, bar.b, 0.9 * bar.opacity)
            cr.rectangle(bar.x, bar.y, bar.width, bar.height)
            cr.fill()
