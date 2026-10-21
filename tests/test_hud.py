"""Tests for the floating HUD: its states, its layout and its bars.

The HUD is a pure module: it draws on a Cairo context it is handed, so the
whole visual language can be checked without a compositor.
"""

import math

import pytest

from voicetype.ui.hud import (
    BUSY_STATES,
    ColorTween,
    Crossfade,
    HudContent,
    HudMeasures,
    HudMetrics,
    HudState,
    TextTicker,
    Waveform,
    draw_card,
    draw_waveform,
    hud_color,
    hud_font_sizes,
    hud_label,
    hud_layout,
    inner_surface,
    rounded_rect,
)
from voicetype.ui.theme import PALETTES, resolve_palette

# ---------------------------------------------------------------- states


def test_every_state_has_a_label():
    for state in HudState:
        label = hud_label(state)
        assert label
        assert label == label.upper()


def test_the_labels_are_the_ones_the_design_names():
    assert hud_label(HudState.LISTENING) == "LISTENING"
    assert hud_label(HudState.TRANSCRIBING) == "TRANSCRIBING"
    assert hud_label(HudState.TRANSLATING) == "TRANSLATING"
    assert hud_label(HudState.TRANSLATED) == "TRANSLATED"
    assert hud_label(HudState.ERROR) == "ERROR"


def test_the_live_and_the_finished_states_are_mint():
    palette = resolve_palette("nocturne")
    assert hud_color(palette, HudState.LISTENING) == palette.success
    assert hud_color(palette, HudState.TRANSLATED) == palette.success


def test_the_working_states_are_violet():
    palette = resolve_palette("nocturne")
    assert hud_color(palette, HudState.TRANSCRIBING) == palette.accent
    assert hud_color(palette, HudState.TRANSLATING) == palette.accent
    assert set(BUSY_STATES) == {HudState.TRANSCRIBING, HudState.TRANSLATING}


def test_the_failure_is_its_own_colour():
    palette = resolve_palette("nocturne")
    assert hud_color(palette, HudState.ERROR) == palette.error
    # the quiet states stay out of the way
    assert hud_color(palette, HudState.READY) == palette.text_dim


# ---------------------------------------------------------------- typography


def test_the_font_sizes_land_in_the_band_the_card_was_designed_for():
    """The main text is 14–16 px and the status label 10–12 px."""
    primary, status, secondary = hud_font_sizes(0.0, 11.0)
    assert 13.0 <= primary * 96 / 72 <= 17.0
    assert 9.0 <= status * 96 / 72 <= 13.0
    assert secondary < primary
    assert status < secondary


def test_a_configured_size_wins():
    primary, status, _ = hud_font_sizes(14.0, 11.0)
    assert primary == 14.0
    assert status == pytest.approx(14.0 * 0.72)


def test_the_system_size_is_clamped_into_the_band():
    for system in (8.0, 9.0, 11.0, 14.0, 24.0):
        primary, _, _ = hud_font_sizes(0.0, system)
        assert 10.0 <= primary <= 12.5


# ---------------------------------------------------------------- layout


def _measures(**kwargs) -> HudMeasures:
    base = {
        "status_width": 90.0,
        "status_height": 12.0,
        "language_width": 0.0,
    }
    base.update(kwargs)
    return HudMeasures(**base)


def test_the_card_has_the_size_it_was_configured_with():
    """The card does not follow the text: it has the size it was given."""
    content = HudContent(state=HudState.LISTENING, primary="hello")
    frame = hud_layout(content, _measures(), HudMetrics(), width=360.0, height=128.0)
    assert frame.width == 360.0
    assert frame.height == 128.0


def test_the_card_keeps_its_size_when_the_text_changes():
    """The text may be long or short — the card stays the same."""
    metrics = HudMetrics()
    short = hud_layout(
        HudContent(state=HudState.LISTENING, primary="hi"),
        _measures(),
        metrics,
        width=360.0,
        height=128.0,
    )
    long = hud_layout(
        HudContent(state=HudState.LISTENING, primary="a much longer sentence " * 10),
        _measures(),
        metrics,
        width=360.0,
        height=128.0,
    )
    assert (short.width, short.height) == (long.width, long.height)


def test_the_card_grows_when_the_configured_height_is_too_small():
    """A card that cannot hold one line of text is grown instead of clipped."""
    frame = hud_layout(
        HudContent(state=HudState.LISTENING),
        _measures(),
        HudMetrics(),
        width=360.0,
        height=48.0,
        line_height=20.0,
    )
    assert frame.height > 48.0
    assert frame.text_box[3] >= 20.0


def test_the_text_box_sits_between_the_status_row_and_the_waveform():
    metrics = HudMetrics()
    frame = hud_layout(
        HudContent(state=HudState.LISTENING),
        _measures(),
        metrics,
        width=360.0,
        height=128.0,
        line_height=20.0,
    )
    box_x, box_y, box_w, box_h = frame.text_box
    # the box is inside the card, with the padding on both sides
    assert box_x == pytest.approx(metrics.padding_x)
    assert box_w == pytest.approx(360.0 - 2 * metrics.padding_x)
    # below the status row
    assert box_y >= frame.status_y + frame.status_height
    # above the waveform
    assert box_y + box_h <= frame.wave[1]


def test_the_text_box_is_the_same_in_every_state():
    """The text must not move when the state does."""
    metrics = HudMetrics()
    boxes = []
    for state in HudState:
        frame = hud_layout(
            HudContent(state=state),
            _measures(),
            metrics,
            width=360.0,
            height=128.0,
            line_height=20.0,
        )
        boxes.append(frame.text_box)
    assert len(set(boxes)) == 1


def test_the_status_row_is_wide_enough_for_the_language_chip():
    content = HudContent(state=HudState.TRANSLATED, language="GERMAN")
    measures = _measures(language_width=48.0)
    frame = hud_layout(content, measures, HudMetrics(), width=360.0, height=128.0)
    # the chip sits behind the label and does not stick out of the card
    assert frame.language[0] + frame.language[2] <= frame.width - 16.0


def test_the_waveform_only_shows_while_there_is_something_to_show():
    metrics = HudMetrics()
    listening = hud_layout(
        HudContent(state=HudState.LISTENING), _measures(), metrics, width=360.0, height=128.0
    )
    assert listening.has_wave

    for state in (HudState.TRANSCRIBING, HudState.TRANSLATING, HudState.TRANSLATED):
        frame = hud_layout(HudContent(state=state), _measures(), metrics, width=360.0, height=128.0)
        assert frame.has_wave, state

    idle = hud_layout(HudContent(state=HudState.READY), _measures(), metrics, width=360.0, height=128.0)
    assert not idle.has_wave


def test_the_waveform_is_centred():
    metrics = HudMetrics()
    frame = hud_layout(HudContent(state=HudState.LISTENING), _measures(), metrics, width=360.0, height=128.0)
    left = frame.wave[0]
    right = frame.width - (frame.wave[0] + frame.wave[2])
    assert left == pytest.approx(right)


def test_the_waveform_keeps_its_place_when_the_state_changes():
    """The bars do not move when the card changes its state."""
    metrics = HudMetrics()
    listening = hud_layout(
        HudContent(state=HudState.LISTENING), _measures(), metrics, width=360.0, height=128.0
    )
    idle = hud_layout(HudContent(state=HudState.READY), _measures(), metrics, width=360.0, height=128.0)
    assert listening.wave[1] == idle.wave[1]


def test_the_status_dot_sits_on_the_status_row():
    metrics = HudMetrics()
    frame = hud_layout(HudContent(state=HudState.LISTENING), _measures(), metrics, width=360.0, height=128.0)
    dot_x, dot_y = frame.dot_center
    assert dot_x == pytest.approx(metrics.padding_x + metrics.dot_radius)
    assert metrics.padding_y <= dot_y <= metrics.padding_y + frame.status_height


# ---------------------------------------------------------------- ticker


def test_the_ticker_holds_the_text_it_was_given():
    ticker = TextTicker(HudMetrics())
    assert ticker.set("hello") is True
    assert ticker.current == ("hello", "")


def test_setting_the_same_text_changes_nothing():
    ticker = TextTicker(HudMetrics())
    assert ticker.set("hello") is True
    assert ticker.set("hello") is False


def test_a_replaced_text_rolls_out_and_the_new_one_rolls_in():
    """A text that is replaced does not cross-fade: it rolls."""
    now = [0.0]
    ticker = TextTicker(HudMetrics(), clock=lambda: now[0])
    ticker.set("old")
    ticker.set("new")
    assert ticker.previous == ("old", "")
    assert ticker.current == ("new", "")
    # the roll starts at the beginning
    assert ticker.progress == pytest.approx(0.0)

    now[0] = 0.1
    ticker.advance(0.1)
    assert 0.0 < ticker.progress < 1.0

    now[0] = 0.3
    ticker.advance(0.1)
    assert ticker.progress == pytest.approx(1.0)
    # the old text is gone
    assert ticker.previous is None


def test_a_growing_text_stays_where_it_is():
    """The live preview grows in place: it must not roll away."""
    ticker = TextTicker(HudMetrics())
    ticker.set("open", instant=True, live=True)
    ticker.set("open the", instant=True, live=True)
    assert ticker.previous is None
    assert ticker.progress == pytest.approx(1.0)
    assert ticker.current == ("open the", "")


def test_the_box_scrolls_to_the_newest_line():
    """Text that does not fit scrolls through the box."""
    ticker = TextTicker(HudMetrics())
    ticker.set("a long sentence", instant=True, live=True)
    ticker.set_overflow(40.0)
    for _ in range(60):
        ticker.advance(1 / 60)
    assert ticker.scroll == pytest.approx(40.0, abs=0.5)


def test_the_scroll_never_overshoots():
    ticker = TextTicker(HudMetrics())
    ticker.set_overflow(30.0)
    for _ in range(120):
        ticker.advance(1 / 60)
    assert ticker.scroll <= 30.0 + 1e-6


def test_a_finished_document_drifts_instead_of_jumping():
    """A rolled-in document is read from its beginning before it rolls."""
    ticker = TextTicker(HudMetrics())
    ticker.set("old")
    ticker.set("new")
    ticker.set_overflow(40.0)
    ticker.advance(1 / 60)
    # the drift is slow: the beginning of the text is still in sight
    assert ticker.scroll < 5.0


def test_without_motion_the_box_jumps_to_the_newest_line():
    ticker = TextTicker(HudMetrics())
    ticker.duration = 0.0
    ticker.set("hello", instant=True, live=True)
    ticker.set_overflow(40.0)
    ticker.advance(1 / 60)
    assert ticker.scroll == pytest.approx(40.0)


def test_a_replacement_starts_at_the_top_of_the_box():
    """The document that rolls in starts at the beginning of the text."""
    ticker = TextTicker(HudMetrics())
    ticker.set("old", instant=True, live=True)
    ticker.set_overflow(40.0)
    for _ in range(30):
        ticker.advance(1 / 60)
    assert ticker.scroll > 0.0

    ticker.set("new")
    assert ticker.scroll == pytest.approx(0.0)
    assert ticker.previous_scroll > 0.0


def test_an_empty_box_starts_at_the_top():
    """A new session starts from the top of the box, not where the last ended."""
    ticker = TextTicker(HudMetrics())
    ticker.set("old", instant=True, live=True)
    ticker.set_overflow(40.0)
    for _ in range(30):
        ticker.advance(1 / 60)
    assert ticker.scroll > 0.0

    ticker.reset()
    assert ticker.current == ("", "")
    assert ticker.scroll == pytest.approx(0.0)
    assert ticker.previous is None


# ---------------------------------------------------------------- crossfade


def test_a_crossfade_swaps_the_value_in_and_out():
    now = [0.0]
    fade = Crossfade(0.2, clock=lambda: now[0])
    fade.set("hello")
    assert fade.alphas() == (0.0, 1.0)

    fade.set("world")
    now[0] = 0.1
    old, new = fade.alphas()
    assert 0.0 < old < 1.0
    assert 0.0 < new < 1.0
    assert old + new == pytest.approx(1.0)

    now[0] = 0.25
    assert fade.alphas() == (0.0, 1.0)


def test_a_crossfade_without_a_previous_value_just_fades_in():
    now = [0.0]
    fade = Crossfade(0.2, clock=lambda: now[0])
    fade.set("hello")
    now[0] = 0.05
    assert fade.alphas() == (0.0, 1.0)


def test_an_instant_change_does_not_fade():
    now = [0.0]
    fade = Crossfade(0.2, clock=lambda: now[0])
    fade.set("hello")
    fade.set("hello world", instant=True)
    now[0] = 0.05
    assert fade.alphas() == (0.0, 1.0)
    assert fade.previous is None


def test_setting_the_same_value_changes_nothing():
    fade = Crossfade(0.2)
    assert fade.set("hello") is True
    assert fade.set("hello") is False


def test_a_colour_tween_eases_towards_the_new_colour():
    now = [0.0]
    tween = ColorTween(0.2, clock=lambda: now[0])
    tween.set((1.0, 0.0, 0.0, 1.0))
    assert tween.value() == pytest.approx((1.0, 0.0, 0.0, 1.0))

    tween.set((0.0, 1.0, 0.0, 1.0))
    now[0] = 0.1
    middle = tween.value()
    assert 0.0 < middle[0] < 1.0
    assert 0.0 < middle[1] < 1.0

    now[0] = 0.3
    assert tween.value() == pytest.approx((0.0, 1.0, 0.0, 1.0))


# ---------------------------------------------------------------- waveform


def test_the_waveform_follows_the_microphone():
    wave = Waveform(HudMetrics())
    wave.update(HudState.LISTENING, 0.0, [0.0] * 40, dt=1 / 60)
    quiet = max(wave.heights)

    wave.update(HudState.LISTENING, 1.0, [1.0] * 40, dt=1 / 60)
    for _ in range(30):
        wave.update(HudState.LISTENING, 1.0, [1.0] * 40, dt=1 / 60)
    loud = max(wave.heights)

    assert loud > quiet
    assert loud <= 1.0


def test_the_waveform_is_quiet_while_it_works():
    wave = Waveform(HudMetrics())
    for _ in range(30):
        wave.update(HudState.TRANSLATING, 0.0, None, dt=1 / 60)
    busy = max(wave.heights)

    wave = Waveform(HudMetrics())
    for _ in range(30):
        wave.update(HudState.LISTENING, 1.0, [1.0] * 40, dt=1 / 60)
    listening = max(wave.heights)

    assert busy < listening


def test_the_waveform_settles_when_the_work_is_done():
    wave = Waveform(HudMetrics())
    for _ in range(30):
        wave.update(HudState.LISTENING, 1.0, [1.0] * 40, dt=1 / 60)
    assert max(wave.heights) > 0.5

    for _ in range(120):
        wave.update(HudState.TRANSLATED, 0.0, None, dt=1 / 60)
    assert max(wave.heights) < 0.2


def test_the_waveform_stops_while_nothing_happens():
    wave = Waveform(HudMetrics())
    for _ in range(60):
        wave.update(HudState.READY, 0.0, None, dt=1 / 60)
    assert max(wave.heights) == pytest.approx(0.0)


def test_the_waveform_jumps_when_the_motion_is_switched_off():
    wave = Waveform(HudMetrics())
    wave.animated = False
    wave.update(HudState.LISTENING, 1.0, [1.0] * 40, dt=1 / 60)
    assert max(wave.heights) > 0.5


def test_the_waveform_has_the_number_of_bars_the_design_asks_for():
    wave = Waveform(HudMetrics())
    assert len(wave.heights) == 19
    assert 15 <= len(wave.heights) <= 23


def test_a_new_session_starts_from_silence():
    wave = Waveform(HudMetrics())
    for _ in range(30):
        wave.update(HudState.LISTENING, 1.0, [1.0] * 40, dt=1 / 60)
    wave.reset()
    assert max(wave.heights) == 0.0


# ---------------------------------------------------------------- painting


def _pixel(surface, x: int, y: int) -> tuple[int, int, int]:
    """The (red, green, blue) of a pixel of an ARGB32 surface.

    Cairo keeps the pixels in a premultiplied BGRA order on a little-endian
    machine, so the channels have to be read the right way round.
    """
    data = surface.get_data()
    offset = y * surface.get_stride() + x * 4
    blue, green, red = data[offset], data[offset + 1], data[offset + 2]
    return (red, green, blue)


def test_the_card_is_painted_with_the_palette_colours():
    cairo = pytest.importorskip("cairo")
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 120)
    cr = cairo.Context(surface)
    palette = resolve_palette("nocturne")

    draw_card(cr, 10, 10, 180, 100, 20.0, palette)

    # the body of the card carries the palette's background colour
    red, green, blue = _pixel(surface, 100, 60)
    assert (red, green, blue) == pytest.approx(
        (palette.background[0] * 255, palette.background[1] * 255, palette.background[2] * 255),
        abs=2,
    )
    # #191720 has a violet undertone, not a neutral grey
    assert blue > red


def test_the_waveform_is_painted_with_the_accent():
    cairo = pytest.importorskip("cairo")
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 120)
    cr = cairo.Context(surface)
    metrics = HudMetrics()
    palette = resolve_palette("nocturne")

    draw_waveform(cr, (10, 10, metrics.wave_width, metrics.wave_height), [1.0] * 19, palette.accent, metrics)

    red, green, blue = _pixel(surface, 11, 20)
    # the bars are violet: the blue channel is the strongest one
    assert blue > red
    assert blue > green


def test_the_inner_surface_is_darker_than_the_card():
    palette = resolve_palette("nocturne")
    inner = inner_surface(palette)
    assert sum(inner[:3]) < sum(palette.background[:3])


def test_the_rounded_rectangle_never_draws_a_self_intersecting_path():
    cairo = pytest.importorskip("cairo")
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 40, 40)
    cr = cairo.Context(surface)
    # a radius bigger than the shape must be clamped, not crash
    rounded_rect(cr, 0, 0, 10, 10, 50.0)
    cr.set_source_rgba(1, 1, 1, 1)
    cr.fill()


def test_the_palette_of_the_card_is_the_nocturne_one():
    palette = resolve_palette("nocturne")
    assert palette is PALETTES["nocturne"]
    assert palette.background[:3] == pytest.approx((0.098, 0.090, 0.125), abs=0.002)
    assert palette.accent[:3] == pytest.approx((0.651, 0.592, 1.0), abs=0.002)
    assert palette.success[:3] == pytest.approx((0.647, 0.910, 0.549), abs=0.002)


def test_the_waveform_width_fits_the_design():
    metrics = HudMetrics()
    assert metrics.wave_width < 130.0
    assert math.isclose(metrics.wave_width, 19 * 2.5 + 18 * 3.0)
