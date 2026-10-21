"""Tests for the overlay visualizations.

Everything here runs headless: no compositor, no GTK, no audio device. The
Cairo smoke tests are skipped when ``pycairo`` is not installed.
"""

from __future__ import annotations

import math
import time

import pytest

from voicetype.ui.animations import AutoGain, VisualizerState
from voicetype.ui.visualizations import STYLES, create_visualization
from voicetype.ui.visualizations.pill import PillVisualization
from voicetype.ui.visualizations.vu_meter import VUMeterVisualization
from voicetype.ui.visualizations.waveform import WaveformVisualization, bucket_rms


class FakeClock:
    """A manually advanced monotonic clock."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def settle(viz: PillVisualization, clock: FakeClock, phase: float, steps: int = 20) -> None:
    """Run the pill forward while pinning the animation phase.

    The pill advances its phase by ``dt * PHASE_RATE`` on every frame, so the
    phase is pre-rolled here to keep it at ``phase`` for the whole run.
    """
    step = 0.05
    for _ in range(steps):
        clock.advance(step)
        viz.state_manager.animation_phase = phase - step * PillVisualization.PHASE_RATE
        viz.update(0.0)


# ---------------------------------------------------------------- registry


def test_styles_registry_has_all_three():
    assert set(STYLES) == {"pill", "waveform", "vu_meter"}
    assert STYLES["pill"] is PillVisualization
    assert STYLES["waveform"] is WaveformVisualization
    assert STYLES["vu_meter"] is VUMeterVisualization


@pytest.mark.parametrize("style", ["pill", "waveform", "vu_meter"])
def test_every_style_instantiates(style):
    viz = create_visualization(style)
    assert viz.style == style
    assert viz.palette is not None


def test_unknown_style_falls_back_to_pill():
    viz = create_visualization("does-not-exist")
    assert isinstance(viz, PillVisualization)
    assert viz.style == "pill"


def test_create_visualization_is_case_insensitive():
    assert isinstance(create_visualization("  WAVEFORM "), WaveformVisualization)


# ---------------------------------------------------------------- pill


def test_pill_geometry_short_surface():
    viz = PillVisualization()
    x, y, pill_w, pill_h = viz.pill_geometry(200, 60)
    assert pill_w == 126
    assert pill_h == 42
    assert x == pytest.approx(37.0)
    assert y == pytest.approx(9.0)


def test_pill_geometry_tall_surface_reserves_preview_space():
    viz = PillVisualization()
    x, y, pill_w, pill_h = viz.pill_geometry(200, 120)
    assert pill_w == 126
    assert pill_h == 42
    assert x == pytest.approx(37.0)
    # Tall surfaces push the pill to the bottom, leaving room for the
    # transcript preview above it.
    assert y == pytest.approx(120 - 42 - 4)


def test_pill_geometry_clamps_to_small_surfaces():
    viz = PillVisualization()
    _x, _y, pill_w, pill_h = viz.pill_geometry(40, 20)
    assert pill_w == 36
    assert pill_h == 16


def test_pill_recording_raises_bars_and_silence_decays_them():
    clock = FakeClock()
    viz = PillVisualization(clock=clock)

    for _ in range(10):
        clock.advance(0.05)
        viz.update(0.2)

    assert max(viz.bar_heights) > 0.1
    assert max(viz.bar_heights) <= 1.0

    for _ in range(60):
        clock.advance(0.05)
        viz.update(0.0)

    assert max(viz.bar_heights) < 0.01


def test_pill_bars_use_samples_when_present():
    clock = FakeClock()
    viz = PillVisualization(clock=clock)
    samples = [0.5] * 64

    for _ in range(10):
        clock.advance(0.05)
        viz.update(0.0, samples)

    assert max(viz.bar_heights) > 0.1


def test_pill_processing_pattern_is_non_uniform_and_moves():
    clock = FakeClock()
    viz = PillVisualization(clock=clock)
    viz.set_state("processing")

    settle(viz, clock, 0.0)
    first = list(viz.bar_heights)

    settle(viz, clock, 1.9)
    second = list(viz.bar_heights)

    assert max(first) - min(first) > 0.05, "processing bars should not be uniform"
    assert first != second, "processing bars should move with the phase"


def test_pill_error_blinks():
    clock = FakeClock()
    viz = PillVisualization(clock=clock)
    viz.set_state("error")

    settle(viz, clock, 0.0)
    dim = list(viz.bar_heights)

    settle(viz, clock, math.pi / 2 / 2.5)
    bright = list(viz.bar_heights)

    assert dim != bright
    assert bright[0] > dim[0]


def test_pill_error_modulates_bar_opacity():
    clock = FakeClock()
    viz = PillVisualization(clock=clock)
    viz.set_state("error")

    viz.state_manager.animation_phase = 0.0
    dim_opacity = min(bar.opacity for bar in viz.bar_specs(200, 60))

    viz.state_manager.animation_phase = math.pi / 2 / 2.5
    bright_opacity = min(bar.opacity for bar in viz.bar_specs(200, 60))

    assert bright_opacity > dim_opacity


def test_pill_success_morphs_into_a_checkmark():
    clock = FakeClock()
    viz = PillVisualization(clock=clock)
    viz.set_state("success")

    # Part way through the morph the bars are between the floor and the tick.
    clock.advance(0.05)
    partial = viz.bar_specs(200, 60)
    assert PillVisualization.MIN_HEIGHT <= partial[0].height <= PillVisualization.CHECK_HEIGHT

    # After the morph the bars sit at the tick height.
    clock.advance(0.25)
    morphed = viz.bar_specs(200, 60)
    assert all(bar.height == pytest.approx(PillVisualization.CHECK_HEIGHT) for bar in morphed)

    # The tick is shaped: the first bars are hidden, the middle ones are lit.
    opacities = [bar.opacity for bar in morphed]
    assert opacities[0] == pytest.approx(0.0)
    assert max(opacities) == pytest.approx(0.94)


def test_pill_success_fade_expires():
    clock = FakeClock()
    viz = PillVisualization(clock=clock)
    viz.set_state("success")

    clock.advance(0.3)
    assert viz.success_fade() == pytest.approx(1.0)

    clock.advance(0.9)  # 1.2 s after the state change
    assert viz.success_fade() == pytest.approx(0.0)


def test_pill_paused_flattens_the_bars():
    clock = FakeClock()
    viz = PillVisualization(clock=clock)
    for _ in range(5):
        clock.advance(0.05)
        viz.update(0.5)
    assert max(viz.bar_heights) > 0.05

    viz.set_state("paused")
    for _ in range(80):
        clock.advance(0.05)
        viz.update(0.5)
    assert max(viz.bar_heights) < 0.01


def test_a_silent_feed_leaves_the_bars_on_the_floor():
    """What ``overlay.show_audio_meter = false`` feeds the visualization."""
    clock = FakeClock()
    viz = PillVisualization(clock=clock)

    for _ in range(10):
        clock.advance(0.05)
        viz.update(0.5)
    assert max(viz.bar_heights) > 0.05

    for _ in range(80):
        clock.advance(0.05)
        viz.update(0.0, [])
    assert max(viz.bar_heights) < 0.01
    assert viz.audio_samples == []


def test_the_bars_hold_still_without_the_animations():
    clock = FakeClock()
    viz = PillVisualization(clock=clock)
    viz.set_animated(False)

    viz.state_manager.animation_phase = 1.0
    clock.advance(0.05)
    viz.update(0.5)
    assert viz.state_manager.animation_phase == pytest.approx(1.0)


# ---------------------------------------------------------------- waveform


def test_waveform_has_32_bars():
    viz = WaveformVisualization()
    assert viz.num_bars == 32
    assert len(viz.bar_heights) == 32


def test_bucket_rms_matches_hand_computed_example():
    samples = [0.0, 1.0, 0.0, 0.5]
    assert bucket_rms(samples, 2) == pytest.approx([math.sqrt(0.5), math.sqrt(0.125)])


def test_bucket_rms_edge_cases():
    assert bucket_rms([], 4) == [0.0, 0.0, 0.0, 0.0]
    assert bucket_rms([1.0, 2.0, 3.0], 8) == [0.0] * 8
    assert bucket_rms([1.0, 1.0, 1.0], 1) == pytest.approx([1.0])


def test_waveform_bars_follow_the_samples():
    viz = WaveformVisualization()
    viz.update(0.5, [0.4] * 320)
    assert max(viz.bar_heights) > 0.1

    # No audio decays the bars back down.
    for _ in range(60):
        viz.update(0.0, None)
    assert max(viz.bar_heights) < 0.01


def test_waveform_processing_pattern_is_non_uniform_and_moves():
    viz = WaveformVisualization()
    viz.set_state("processing")

    viz.state_manager.animation_phase = 0.0
    first = viz.bar_specs(300, 80)
    viz.state_manager.animation_phase = 1.7
    second = viz.bar_specs(300, 80)

    heights = [bar.height for bar in first]
    assert max(heights) - min(heights) > 1.0, "processing bars should not be uniform"
    assert [bar.height for bar in first] != [bar.height for bar in second]


def test_waveform_success_pulses_and_fades():
    viz = WaveformVisualization()
    viz.set_state("success")
    viz.bar_heights = [0.5] * viz.num_bars

    viz.state_manager.animation_phase = 0.0
    heights = [bar.height for bar in viz.bar_specs(300, 80)]
    assert all(bar.opacity > 0.0 for bar in viz.bar_specs(300, 80))

    # The success envelope is gone a second after the state change.
    viz.state_manager.state_changed_at = time.monotonic() - 1.5
    assert all(bar.opacity == pytest.approx(0.0) for bar in viz.bar_specs(300, 80))
    assert heights  # sanity: the bars were rendered before the fade


# ---------------------------------------------------------------- vu meter


def test_vu_meter_smooths_and_holds_the_peak():
    viz = VUMeterVisualization()
    viz.update(0.5)
    assert viz.smoothed_level > 0.0
    assert viz.peak_level >= viz.smoothed_level

    peak = viz.peak_level
    for _ in range(20):
        viz.update(0.0)
    assert viz.peak_level < peak


# ---------------------------------------------------------------- surface opacity


class RecordingContext:
    """A Cairo context that only records the colours it is given."""

    def __init__(self) -> None:
        self.sources: list[tuple] = []

    def set_source_rgba(self, *args) -> None:
        self.sources.append(args)

    def set_source(self, *args) -> None:
        self.sources.append(args)

    def set_line_width(self, width) -> None:
        pass

    def new_sub_path(self) -> None:
        pass

    def arc(self, *args) -> None:
        pass

    def close_path(self) -> None:
        pass

    def rectangle(self, *args) -> None:
        pass

    def fill(self) -> None:
        pass

    def fill_preserve(self) -> None:
        pass

    def stroke(self) -> None:
        pass


def test_the_pill_body_scales_with_the_surface_opacity():
    viz = PillVisualization()
    viz.set_surface_opacity(0.5)

    context = RecordingContext()
    viz.draw_background(context, 200, 60)

    body = [source for source in context.sources if source[:3] == (0.015, 0.015, 0.018)]
    assert body
    assert body[0][3] == pytest.approx(0.96 * 0.5)


def test_a_fully_transparent_pill_draws_nothing():
    viz = PillVisualization()
    viz.set_surface_opacity(0.0)

    context = RecordingContext()
    viz.draw_background(context, 200, 60)

    assert all(source[3] == 0.0 for source in context.sources)


def test_the_surface_opacity_defaults_to_the_palette_value():
    viz = PillVisualization()
    assert viz.surface_opacity == 1.0


def test_the_surface_opacity_is_clamped():
    viz = PillVisualization()
    viz.set_surface_opacity(2.0)
    assert viz.surface_opacity == 1.0
    viz.set_surface_opacity(-1.0)
    assert viz.surface_opacity == 0.0


# ---------------------------------------------------------------- state


@pytest.mark.parametrize("style", ["pill", "waveform", "vu_meter"])
def test_set_state_round_trip(style):
    viz = create_visualization(style)
    assert viz.set_state("processing") is True
    assert viz.state is VisualizerState.PROCESSING
    assert viz.set_state("processing") is False

    viz.state_manager.set_state_from_string("error")
    assert viz.state is VisualizerState.ERROR

    # Unknown states keep the current one.
    viz.state_manager.set_state_from_string("nonsense")
    assert viz.state is VisualizerState.ERROR


# ---------------------------------------------------------------- cairo smoke


@pytest.fixture
def context():
    cairo = pytest.importorskip("cairo")
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 120)
    return cairo.Context(surface)


@pytest.mark.parametrize("style", ["pill", "waveform", "vu_meter"])
@pytest.mark.parametrize("state", ["recording", "paused", "processing", "error", "success"])
def test_draw_does_not_raise(context, style, state):
    viz = create_visualization(style)
    viz.set_state(state)
    viz.update(0.4, [0.2] * 128)
    viz.draw(context, 300, 120)
    viz.draw_background(context, 300, 120)


@pytest.mark.parametrize("style", ["pill", "waveform", "vu_meter"])
def test_draw_handles_degenerate_sizes(context, style):
    viz = create_visualization(style)
    viz.update(0.4)
    viz.draw(context, 1, 1)
    viz.draw_background(context, 1, 1)


# ---------------------------------------------------------------- auto gain


def test_auto_gain_raises_quiet_input_but_leaves_hot_input_alone():
    quiet = AutoGain(min_gain=1.0, noise_floor=0.002)
    quiet_gain = quiet.update(0.01)
    assert quiet_gain > 1.0

    hot = AutoGain(min_gain=1.0, noise_floor=0.002)
    hot_gain = hot.update(0.9)
    assert hot_gain == pytest.approx(1.0)


def test_auto_gain_ignores_silence():
    gain = AutoGain(min_gain=4.0, noise_floor=0.002)
    assert gain.update(0.0) == pytest.approx(4.0)
    assert gain.gate(0.006) == pytest.approx(0.006)
