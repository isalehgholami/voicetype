"""Horizontal VU meter visualization.

Ported from hyprwhspr's ``lib/mic_osd/visualizations/vu_meter.py``. The numpy
array maths were replaced with plain Python arithmetic so VoiceType keeps its
"no heavy dependencies" promise (ADR-0001).

The meter is a single horizontal bar: the fill is a gradient across the
themed accent colours and a white marker holds the loudest recent peak.
"""

from __future__ import annotations

from ..animations import AutoGain
from .base import BaseVisualization

__all__ = ["VUMeterVisualization"]


class VUMeterVisualization(BaseVisualization):
    """A simple VU meter that displays the audio level as a horizontal bar.

    The bar fills from left to right based on the audio input level, with
    smooth decay for a more natural appearance.
    """

    style = "vu_meter"

    padding = 8.0
    #: Colour of the unfilled part of the track.
    track_color = (0.15, 0.15, 0.15, 1.0)
    #: How fast the bar follows the input.
    smooth_factor = 0.3
    #: How fast the peak marker falls.
    peak_decay = 0.95
    #: Width of the peak marker, in pixels.
    peak_width = 3.0

    def __init__(self, palette=None) -> None:
        super().__init__(palette)
        self.smoothed_level = 0.0
        self.peak_level = 0.0
        # level arrives pre-scaled (raw RMS x10), so 1.0 is the old fixed gain
        self.auto_gain = AutoGain(min_gain=1.0, noise_floor=0.02)

    # ------------------------------------------------------------------ data
    def update(self, level: float, samples=None) -> None:
        """Update with smoothing and peak hold."""
        super().update(level, samples)

        scaled = min(1.0, self.audio_level * self.auto_gain.update(self.audio_level))

        # Smooth the main level
        self.smoothed_level = (
            self.smooth_factor * scaled + (1 - self.smooth_factor) * self.smoothed_level
        )

        # Update the peak with decay
        if scaled > self.peak_level:
            self.peak_level = scaled
        else:
            self.peak_level *= self.peak_decay

    # ------------------------------------------------------------------ paint
    def _level_gradient(self, x0: float, x1: float):
        """Build the horizontal gradient the level bar is filled with."""
        import cairo  # noqa: PLC0415 - the pattern type is only needed at paint time

        gradient = cairo.LinearGradient(x0, 0, x1, 0)
        gradient.add_color_stop_rgba(0.0, *self.palette.bar_left)
        gradient.add_color_stop_rgba(1.0, *self.palette.bar_right)
        return gradient

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
        """Draw the VU meter."""
        padding = self.padding
        bar_height = height - (padding * 2)
        bar_width = width - (padding * 2)
        bar_x = padding
        bar_y = padding

        # Background track
        cr.set_source_rgba(*self.track_color)
        cr.rectangle(bar_x, bar_y, bar_width, bar_height)
        cr.fill()

        # Level bar with a gradient across the themed accent colours
        level_width = bar_width * self.smoothed_level
        if level_width > 0:
            gradient = self._level_gradient(bar_x, bar_x + bar_width)

            cr.set_source(gradient)
            cr.rectangle(bar_x, bar_y, level_width, bar_height)
            cr.fill()

        # Peak indicator
        if self.peak_level > 0.01:
            peak_x = bar_x + (bar_width * self.peak_level) - 2
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.8)
            cr.rectangle(peak_x, bar_y, self.peak_width, bar_height)
            cr.fill()

        # Border around the track
        cr.set_source_rgba(*self.palette.border)
        cr.set_line_width(1)
        cr.rectangle(bar_x, bar_y, bar_width, bar_height)
        cr.stroke()
