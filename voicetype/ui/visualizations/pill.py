"""Compact monochrome pill visualization for the VoiceType overlay.

Ported from hyprwhspr's ``lib/mic_osd/visualizations/pill.py``. The numpy
array maths were replaced with plain Python lists so VoiceType keeps its
"no heavy dependencies" promise (ADR-0001).

The pill is a small black lozenge that floats above the transcript preview.
It carries thirteen white bars that react to the microphone, and it morphs
into a checkmark when a dictation succeeds.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from typing import NamedTuple

from ..animations import AutoGain, StateManager, VisualizerState
from .base import BaseVisualization, rounded_rect

__all__ = ["PillVisualization", "PillBar"]


class PillBar(NamedTuple):
    """One bar of the pill, expressed in surface pixels."""

    x: float
    y: float
    width: float
    height: float
    opacity: float


def _resample(values: Sequence[float], count: int) -> list[float]:
    """Resample ``values`` down to ``count`` points with linear interpolation.

    The source grid and the target grid are both uniform over ``0..1``, so a
    plain linear interpolation reproduces ``numpy.interp`` exactly.
    """
    n = len(values)
    if count <= 0:
        return []
    if n == 0:
        return [0.0] * count
    if n == count:
        return [float(v) for v in values]
    if count == 1:
        return [float(values[0])]

    out: list[float] = []
    for i in range(count):
        pos = i * (n - 1) / (count - 1)
        low = int(pos)
        high = min(low + 1, n - 1)
        frac = pos - low
        out.append(float(values[low]) * (1.0 - frac) + float(values[high]) * frac)
    return out


class PillVisualization(BaseVisualization):
    """A compact black pill with white audio bars and state animations."""

    style = "pill"

    PILL_WIDTH = 126
    PILL_HEIGHT = 42
    #: Surfaces this tall are reserving space for the transcript preview above.
    PREVIEW_HEIGHT_THRESHOLD = 76
    NUM_BARS = 13
    BAR_WIDTH = 3.0
    BAR_GAP = 3.0
    MIN_HEIGHT = 3.0
    MAX_HEIGHT = 22.0
    NOISE_GATE = 0.006
    INPUT_GAIN = 18.0

    #: The tick shape: vertical offset of every bar, in pixels. The first
    #: stroke dips down, the second rises again -- that is the checkmark.
    _CHECK_OFFSETS = (
        0.0, 0.0, 0.0, -1.0, 1.5, 4.0, 6.5,
        3.0, -0.5, -4.0, -7.5, 0.0, 0.0,
    )
    #: The tick shape: per-bar opacity, so the stroke fades in and back out.
    _CHECK_OPACITY = (
        0.0, 0.0, 0.0, 0.45, 0.70, 0.92, 1.0,
        1.0, 1.0, 0.95, 0.72, 0.0, 0.0,
    )

    #: Radians per second the animation phase advances.
    PHASE_RATE = 7.2
    #: How long the bars take to morph into the checkmark.
    MORPH_SECONDS = 0.22
    #: How long the finished tick is held before it fades.
    SUCCESS_HOLD = 0.72
    #: How long the tick takes to fade out.
    SUCCESS_FADE = 0.28
    #: The bar height the morph settles on.
    CHECK_HEIGHT = 5.5
    #: Opacity of a fully lit bar.
    BAR_OPACITY = 0.94

    def __init__(
        self,
        palette=None,
        clock: Callable[[], float] = time.monotonic,
        animated: bool = True,
    ) -> None:
        super().__init__(palette, animated=animated)
        self._clock = clock
        # The state manager shares the injected clock so the success morph can
        # be driven deterministically in tests.
        self.state_manager = StateManager(self.palette, clock, animated)
        self.bar_heights: list[float] = [0.0] * self.NUM_BARS
        self._last_update = clock()
        # INPUT_GAIN was the old fixed value, now the floor for hot mics
        self.auto_gain = AutoGain(min_gain=self.INPUT_GAIN, noise_floor=0.002)

    # ------------------------------------------------------------------ data
    def update(self, level: float, samples=None) -> None:
        """Feed a new audio frame and advance the bar animation."""
        phase_before = self.state_manager.animation_phase
        super().update(level, samples)

        now = self._clock()
        dt = min(0.05, max(0.001, now - self._last_update))
        self._last_update = now

        # The base class advances the shared phase on every frame; the pill
        # runs on its own 7.2 rad/s clock so the wave speed does not depend on
        # the frame rate.  With the animations off the phase is frozen, so the
        # pill holds still.
        if self.state_manager.animated:
            phase = (phase_before + dt * self.PHASE_RATE) % (2 * math.pi)
        else:
            phase = phase_before
        self.state_manager.animation_phase = phase

        positions = [i / (self.NUM_BARS - 1) for i in range(self.NUM_BARS)]
        state = self.state_manager.current_state

        if state == VisualizerState.RECORDING:
            target = self._recording_target(samples, positions)
        elif state == VisualizerState.PROCESSING:
            wave = [0.5 + 0.5 * math.sin(phase - p * 2.6 * math.pi) for p in positions]
            second = [0.5 + 0.5 * math.sin(phase * 0.72 + p * 1.7 * math.pi) for p in positions]
            target = [
                0.16 + 0.70 * (0.72 * w + 0.28 * s) for w, s in zip(wave, second, strict=True)
            ]
        elif state == VisualizerState.ERROR:
            blink = 0.25 + 0.75 * abs(math.sin(phase * 2.5))
            target = [blink] * self.NUM_BARS
        elif state == VisualizerState.PAUSED:
            target = [0.0] * self.NUM_BARS
        else:  # Success morphs into a checkmark while drawing.
            target = [0.18] * self.NUM_BARS

        rise = 1.0 - math.exp(-dt * 18.0)
        fall = 1.0 - math.exp(-dt * 9.0)
        heights = self.bar_heights
        for i, value in enumerate(target):
            blend = rise if value > heights[i] else fall
            heights[i] += (value - heights[i]) * blend

    def _recording_target(self, samples, positions: list[float]) -> list[float]:
        """Energy per bar for the recording state."""
        audio = _resample([abs(float(s)) for s in samples], self.NUM_BARS) if samples is not None else []
        from_samples = any(audio)
        if not from_samples:
            # level is pre-scaled (raw RMS x10), so this substitute is on a
            # different scale than the buckets -- keep it out of the gain
            # envelope, which would read it as a 10x louder mic.
            audio = [max(0.0, float(self.audio_level))] * self.NUM_BARS

        # Gate mic noise out of the RMS feed, then compress so speech fills
        # the pill. Both track the input's own level: a fixed gate would
        # swallow a quiet mic whole, whose speech sits under it.
        if from_samples:
            gain = self.auto_gain.update(max(audio))
            gate = self.auto_gain.gate(self.NOISE_GATE)
        else:
            gain, gate = self.INPUT_GAIN, self.NOISE_GATE

        energy = []
        for value in audio:
            scaled = max(value - gate, 0.0) * gain
            energy.append(math.sqrt(min(1.0, max(0.0, scaled))))

        # The middle of the pill is emphasised so speech reads as a hill.
        return [
            e * (0.62 + 0.38 * math.sin(math.pi * p))
            for e, p in zip(energy, positions, strict=True)
        ]

    # ------------------------------------------------------------------ paint
    def pill_geometry(self, width: int, height: int) -> tuple[float, float, float, float]:
        """Return ``(x, y, pill_width, pill_height)`` in surface pixels."""
        pill_w = min(self.PILL_WIDTH, width - 4)
        pill_h = min(self.PILL_HEIGHT, height - 4)
        x = (width - pill_w) / 2.0

        # A taller surface reserves space for the live text above the pill.
        if height >= self.PREVIEW_HEIGHT_THRESHOLD:
            y = height - pill_h - 4.0
        else:
            y = (height - pill_h) / 2.0

        return x, y, pill_w, pill_h

    def success_fade(self) -> float:
        """Opacity of the pill while the success checkmark is on screen."""
        if self.state_manager.current_state != VisualizerState.SUCCESS:
            return 1.0
        elapsed = self.state_manager.elapsed
        if elapsed <= self.SUCCESS_HOLD:
            return 1.0
        return max(0.0, 1.0 - (elapsed - self.SUCCESS_HOLD) / self.SUCCESS_FADE)

    def bar_specs(self, width: int, height: int) -> list[PillBar]:
        """The bars of the current frame: position, size and opacity."""
        x, y, pill_w, pill_h = self.pill_geometry(width, height)
        center_x = x + pill_w / 2.0
        center_y = y + pill_h / 2.0
        total_width = self.NUM_BARS * self.BAR_WIDTH + (self.NUM_BARS - 1) * self.BAR_GAP
        start_x = center_x - total_width / 2.0

        state = self.state_manager.current_state
        fade = self.success_fade()

        morph = 0.0
        if state == VisualizerState.SUCCESS:
            t = min(1.0, self.state_manager.elapsed / self.MORPH_SECONDS)
            morph = 1.0 - (1.0 - t) ** 3

        specs: list[PillBar] = []
        for i in range(self.NUM_BARS):
            normalized = min(1.0, max(0.0, self.bar_heights[i]))
            bar_h = self.MIN_HEIGHT + normalized * (self.MAX_HEIGHT - self.MIN_HEIGHT)
            bar_center_y = center_y
            opacity = self.BAR_OPACITY

            if state == VisualizerState.SUCCESS:
                bar_h = bar_h * (1.0 - morph) + self.CHECK_HEIGHT * morph
                bar_center_y += self._CHECK_OFFSETS[i] * morph
                opacity *= ((1.0 - morph) + self._CHECK_OPACITY[i] * morph) * fade
            elif state == VisualizerState.ERROR:
                opacity *= 0.55 + 0.45 * abs(math.sin(self.state_manager.animation_phase * 2.5))

            bar_x = start_x + i * (self.BAR_WIDTH + self.BAR_GAP)
            specs.append(
                PillBar(
                    x=bar_x,
                    y=bar_center_y - bar_h / 2.0,
                    width=self.BAR_WIDTH,
                    height=bar_h,
                    opacity=opacity,
                )
            )
        return specs

    def draw_background(self, cr, width: int, height: int) -> None:
        """Paint the pill: drop shadow, body and hairline border.

        The body is near-black rather than the palette's background colour —
        the pill floats on the desktop instead of filling a panel — so it
        carries its own alpha.  ``overlay.opacity`` scales it, otherwise the
        setting would have no visible effect on the default style.
        """
        x, y, pill_w, pill_h = self.pill_geometry(width, height)
        alpha = self.success_fade() * self.surface_opacity

        rounded_rect(cr, x, y + 2.0, pill_w, pill_h, pill_h / 2.0)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.30 * alpha)
        cr.fill()

        rounded_rect(cr, x, y, pill_w, pill_h, pill_h / 2.0)
        cr.set_source_rgba(0.015, 0.015, 0.018, 0.96 * alpha)
        cr.fill_preserve()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.075 * alpha)
        cr.set_line_width(1.0)
        cr.stroke()

    def draw(self, cr, width: int, height: int) -> None:
        """Paint the bars of the pill."""
        for bar in self.bar_specs(width, height):
            if bar.opacity <= 0.01:
                continue
            radius = min(self.BAR_WIDTH / 2.0, bar.height / 2.0)
            rounded_rect(cr, bar.x, bar.y, bar.width, bar.height, radius)
            cr.set_source_rgba(1.0, 1.0, 1.0, bar.opacity)
            cr.fill()
