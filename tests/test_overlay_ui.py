"""Headless tests for the overlay's rendering plumbing.

Nothing here needs a compositor: the GTK class is only inspected, never
instantiated, and everything that is exercised is a pure function.
"""

import ast
import inspect
import os
import threading
from pathlib import Path

import pytest

from voicetype.config import DEFAULT_FONT, DEFAULT_FONT_SIZE, OverlayConfig
from voicetype.dispatcher import NullOverlay
from voicetype.overlay import (
    MODE_TO_STATE,
    STATUS_FONT_SCALE,
    TRANSPARENCY_CSS,
    Anchors,
    GtkOverlay,
    create_overlay,
    meter_audio,
    mode_sound,
    resolve_accent,
    resolve_anchors,
    resolve_overlay_font,
    resolve_settings,
    smooth,
    status_text_for,
    style_palette,
    theme_file,
)
from voicetype.ui.animations import FadeEnvelope
from voicetype.ui.hud import (
    ColorTween,
    Crossfade,
    HudMetrics,
    HudState,
    TextTicker,
    Waveform,
    hud_font_sizes,
    hud_label,
)
from voicetype.ui.theme import PALETTES, Theme, ThemeWatcher, hex_to_rgba, resolve_palette
from voicetype.ui.transcript import TranscriptAnimator, TranscriptConfig

# ---------------------------------------------------------------- creation


def test_create_overlay_disabled_returns_null_overlay():
    assert isinstance(create_overlay(OverlayConfig(enabled=False)), NullOverlay)


def test_create_overlay_enabled_never_raises_headless():
    # no compositor / no layer-shell in the test environment → must not raise
    overlay = create_overlay(OverlayConfig(enabled=True))
    assert overlay is not None


def test_create_overlay_handles_unknown_config_fields():
    # the new config keys may not have landed yet
    cfg = OverlayConfig(enabled=True)
    overlay = create_overlay(cfg)
    assert overlay is not None


# ---------------------------------------------------------------- public surface


def test_smooth_converges_monotonically():
    value = 0.0
    previous = -1.0
    for _ in range(60):
        value = smooth(value, 1.0)
        assert previous < value <= 1.0
        previous = value
    assert value > 0.999


@pytest.mark.parametrize(
    "name",
    [
        "show_listening",
        "set_partial",
        "set_audio_level",
        "show_processing",
        "show_translating",
        "show_error",
        "show_warning",
        "show_success",
        "hide",
    ],
)
def test_gtk_overlay_exposes_the_protocol_methods(name):
    assert hasattr(GtkOverlay, name)
    assert callable(getattr(GtkOverlay, name))


def test_set_audio_level_keeps_the_single_argument_protocol():
    parameters = list(inspect.signature(GtkOverlay.set_audio_level).parameters.values())[1:]
    required = [p for p in parameters if p.default is inspect.Parameter.empty]
    assert [p.name for p in required] == ["level"]
    # the sample buffer is an optional extension
    assert all(p.default is not inspect.Parameter.empty for p in parameters[1:])


def test_set_partial_takes_the_text():
    parameters = list(inspect.signature(GtkOverlay.set_partial).parameters)[1:]
    assert parameters == ["text"]


def test_the_overlay_can_show_the_source_and_the_result():
    """The card carries the source while it translates and the result after."""
    translating = list(inspect.signature(GtkOverlay.show_translating).parameters)[1:]
    assert translating == ["text"]
    success = list(inspect.signature(GtkOverlay.show_success).parameters)[1:]
    assert success == ["text", "language"]


# ---------------------------------------------------------------- mode → state


def test_mode_to_state_mapping():
    assert MODE_TO_STATE == {
        "listening": HudState.LISTENING,
        "processing": HudState.TRANSCRIBING,
        "translating": HudState.TRANSLATING,
        "success": HudState.TRANSLATED,
        "error": HudState.ERROR,
        "warning": HudState.NOTICE,
        "hidden": HudState.READY,
    }


def test_a_warning_is_a_notice_not_a_recording():
    # a warning is a quiet amber notice: it must not look like a live mic
    assert MODE_TO_STATE["warning"] == HudState.NOTICE
    assert MODE_TO_STATE["warning"] != HudState.LISTENING


def test_the_listening_state_is_the_only_live_one():
    live = {state for state, value in MODE_TO_STATE.items() if value is HudState.LISTENING}
    assert live == {"listening"}


def test_status_text_uses_the_state_labels():
    assert status_text_for("listening") == "LISTENING"
    assert status_text_for("processing") == "TRANSCRIBING"
    assert status_text_for("translating") == "TRANSLATING"
    assert status_text_for("success") == "TRANSLATED"
    assert status_text_for("error") == "ERROR"
    assert status_text_for("warning") == "NOTICE"
    assert status_text_for("hidden") == "READY"


def test_every_label_is_upper_case():
    for mode in MODE_TO_STATE:
        label = status_text_for(mode)
        assert label == label.upper()
        assert label == hud_label(MODE_TO_STATE[mode])


def test_the_labels_are_the_ones_the_design_names():
    assert hud_label(HudState.LISTENING) == "LISTENING"
    assert hud_label(HudState.TRANSCRIBING) == "TRANSCRIBING"
    assert hud_label(HudState.TRANSLATING) == "TRANSLATING"
    assert hud_label(HudState.TRANSLATED) == "TRANSLATED"


def test_the_content_keeps_the_case_it_was_written_in():
    """Only the labels are upper case: the speech keeps its own case."""
    source = inspect.getsource(GtkOverlay)
    assert ".lower()" not in source
    assert "display_text" not in source


# ---------------------------------------------------------------- font


def test_one_font_for_the_whole_card():
    """One font for the whole card: the label, the messages and the preview."""
    cfg = OverlayConfig()
    # the live preview shares the card's font
    assert TranscriptConfig.from_overlay_config(cfg).font_family == cfg.font
    # the renderer lays every text out with that one family
    source = inspect.getsource(GtkOverlay._layout)
    assert "self._font_description" in source


def test_the_font_follows_the_system_by_default():
    """With nothing configured the overlay uses the desktop's own font."""
    cfg = OverlayConfig()
    assert cfg.font == DEFAULT_FONT == ""
    assert cfg.font_size == DEFAULT_FONT_SIZE == 0.0


def test_the_font_is_configurable():
    cfg = OverlayConfig(font="Fira Code", font_size=20.0)
    assert cfg.font == "Fira Code"
    assert cfg.font_size == 20.0
    transcript = TranscriptConfig.from_overlay_config(cfg)
    assert transcript.font_family == "Fira Code"
    assert transcript.font_size == 20.0


def test_the_status_label_keeps_its_share_of_the_main_text():
    assert STATUS_FONT_SCALE < 1.0
    settings = resolve_settings(OverlayConfig(font_size=20.0))
    assert settings.font_size == 20.0


def test_the_configured_font_reaches_the_settings():
    settings = resolve_settings(OverlayConfig(font="Fira Code", font_size=20.0))
    assert settings.font_family == "Fira Code"
    assert settings.font_size == 20.0


def test_an_unset_font_is_left_to_the_system():
    settings = resolve_settings(OverlayConfig())
    assert settings.font_family == ""
    assert settings.font_size == 0.0


def test_the_font_sizes_land_in_the_band_the_card_was_designed_for():
    """The main text is 14–16 px and the status label 10–12 px."""
    primary, status, secondary = hud_font_sizes(0.0, 11.0)
    assert 13.0 <= primary * 96 / 72 <= 17.0  # points → pixels
    assert 9.0 <= status * 96 / 72 <= 13.0
    assert secondary < primary


def test_a_configured_size_wins_over_the_system_size():
    primary, status, _ = hud_font_sizes(18.0, 10.0)
    assert primary == 18.0
    assert status < primary


def test_the_doctor_reports_the_font_the_overlay_would_use():
    family, size = resolve_overlay_font(OverlayConfig(font="Fira Code", font_size=20.0))
    assert family == "Fira Code"
    assert size == 20.0


def test_the_texts_wrap_inside_the_card():
    """Every text is wrapped inside the card's own width.

    The card has a fixed width, so the texts are wrapped inside it instead
    of deciding how wide the card is.  That is what keeps a right-to-left
    text at the right edge of the card it is drawn in.
    """
    overlay = GtkOverlay.__new__(GtkOverlay)
    overlay._card_width = 360
    overlay._hud = HudMetrics()
    assert overlay._available_width() == 360 - 2 * HudMetrics().padding_x


def test_a_narrow_card_narrows_its_texts():
    overlay = GtkOverlay.__new__(GtkOverlay)
    overlay._card_width = 240
    overlay._hud = HudMetrics()
    assert overlay._available_width() == 240 - 2 * HudMetrics().padding_x


def test_a_right_to_left_text_is_aligned_with_the_start_of_its_paragraph():
    """Pango's LEFT means the *start* of the paragraph when the direction is
    automatic, and the direction follows the text — so one answer covers
    both directions.  With RIGHT a Persian text would hug the wrong edge.
    """
    gi = pytest.importorskip("gi", reason="PyGObject (GTK) is not installed")

    gi.require_version("Pango", "1.0")
    from gi.repository import Pango

    overlay = GtkOverlay.__new__(GtkOverlay)
    overlay._Pango = Pango
    assert overlay._alignment("سلام دنیا") == Pango.Alignment.LEFT
    assert overlay._alignment("hello world") == Pango.Alignment.LEFT


def test_the_wrapped_layout_follows_the_direction_of_its_own_text():
    """The layout's direction is automatic, so the text decides it."""
    gi = pytest.importorskip("gi", reason="PyGObject (GTK) is not installed")

    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    cairo = pytest.importorskip("cairo")
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 60)
    cr = cairo.Context(surface)

    overlay = GtkOverlay.__new__(GtkOverlay)
    overlay._Pango = Pango
    overlay._PangoCairo = PangoCairo
    overlay._hud = HudMetrics()
    overlay._font_family = ""
    overlay._font_size = 11.0
    overlay._card_width = 360

    layout = overlay._wrapped_layout(cr, "سلام دنیا", 11.0)
    assert layout.get_auto_dir() is True
    assert layout.get_width() == int(overlay._available_width() * Pango.SCALE)


def test_the_text_is_not_cut_short_by_the_layout():
    """The text rolls through the box, so the layout must not ellipsize it.

    A layout that stopped at the last line would swallow the text that the
    box is supposed to scroll to.
    """
    gi = pytest.importorskip("gi", reason="PyGObject (GTK) is not installed")

    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    cairo = pytest.importorskip("cairo")
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 60)
    cr = cairo.Context(surface)

    overlay = GtkOverlay.__new__(GtkOverlay)
    overlay._Pango = Pango
    overlay._PangoCairo = PangoCairo
    overlay._hud = HudMetrics()
    overlay._font_family = ""
    overlay._font_size = 11.0
    overlay._card_width = 360

    layout = overlay._wrapped_layout(cr, "word " * 60, 11.0)
    assert layout.get_ellipsize() == Pango.EllipsizeMode.NONE
    assert layout.get_line_count() > 3


# ---------------------------------------------------------------- the fixed card


def _paintable_overlay():
    """An overlay that can paint itself without a compositor.

    The real drawing code is used: only the parts the painting touches are
    filled in, the constructor needs a display the test machine has not.
    """
    gi = pytest.importorskip("gi", reason="PyGObject (GTK) is not installed")

    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Pango, PangoCairo

    overlay = GtkOverlay.__new__(GtkOverlay)
    overlay._Pango = Pango
    overlay._PangoCairo = PangoCairo
    overlay._Gdk = Gdk
    overlay._hud = HudMetrics()
    overlay._state = HudState.LISTENING
    overlay._label = Crossfade(0.2)
    overlay._ticker = TextTicker(HudMetrics())
    overlay._tint = ColorTween(0.2)
    overlay._wave = Waveform(HudMetrics())
    overlay._language = ""
    overlay._source = ""
    overlay._live_text = ""
    overlay._word_alphas = ()
    overlay._line_height = 0.0
    overlay._lock = threading.Lock()
    overlay._animator = TranscriptAnimator(TranscriptConfig(word_limit=8))
    overlay._transcript_enabled = True
    overlay._card_width = 360
    overlay._card_height = 128
    overlay._font_family = ""
    overlay._font_size = 11.0
    overlay._status_font_size = 8.0
    overlay._secondary_font_size = 9.5
    overlay.palette = resolve_palette("nocturne")
    return overlay


def _paint(overlay, text: str, secondary: str = "") -> tuple:
    """Paint the card with ``text`` and return the size it asked for."""
    cairo = pytest.importorskip("cairo")
    overlay._ticker.set(text, secondary)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 200)
    cr = cairo.Context(surface)
    overlay._draw_hud(cr, 400, 200)
    return overlay._target_size


def test_the_card_keeps_its_size_whatever_it_shows():
    """The card must not resize while the user talks.

    A card that follows the text it holds grows and shrinks with every
    word; the text belongs in a fixed box instead.
    """
    overlay = _paintable_overlay()
    short = _paint(overlay, "hi")
    long = _paint(overlay, "a rather long sentence that has to wrap over more than one line " * 3)
    assert short == long


def test_the_card_is_the_size_it_was_configured_with():
    overlay = _paintable_overlay()
    size = _paint(overlay, "hello")
    margin = HudMetrics().surface_margin
    assert size == (360 + 2 * round(margin), 128 + 2 * round(margin))


def test_the_text_stays_inside_the_card():
    """The text is clipped by the card's box: it never spills out of it."""
    overlay = _paintable_overlay()
    _paint(overlay, "word " * 80)
    # the text is wrapped inside the card, not across it
    assert overlay._available_width() < overlay._card_width
    assert overlay._hud.padding_x > 0


# ---------------------------------------------------------------- theme / accent


def test_resolve_palette_known_name():
    palette = resolve_palette("tokyo-night")
    assert palette is PALETTES["tokyo-night"]


def test_the_default_palette_is_the_one_the_card_was_designed_for():
    assert resolve_palette("") is PALETTES["nocturne"]
    assert resolve_palette("does-not-exist") is PALETTES["nocturne"]


def test_accent_override_uses_hex_to_rgba():
    palette = resolve_palette("tokyo-night")
    assert resolve_accent(palette, "") == palette.accent
    assert resolve_accent(palette, "  ") == palette.accent
    assert resolve_accent(palette, "#ff8800") == hex_to_rgba("#ff8800")
    assert resolve_accent(palette, "#f80") == hex_to_rgba("#f80")


def test_invalid_accent_override_keeps_the_palette_accent():
    palette = resolve_palette("tokyo-night")
    assert resolve_accent(palette, "not-a-colour") == palette.accent


def test_accent_override_produces_an_rgba_tuple():
    palette = resolve_palette("nord")
    accent = resolve_accent(palette, "#7dcfff")
    assert len(accent) == 4
    assert accent[3] == 1.0


# ---------------------------------------------------------------- placement


def test_center_position_is_anchored_instead_of_ignored():
    anchors = resolve_anchors("center", "center", 0, 12)
    assert isinstance(anchors, Anchors)
    assert anchors.edges == ("top",)
    assert anchors.margins == {"top": 12}


def test_top_and_bottom_positions_keep_their_edge():
    assert resolve_anchors("top", "center", 0, 12).edges == ("top",)
    assert resolve_anchors("bottom", "center", 0, 12).edges == ("bottom",)


def test_horizontal_anchors():
    assert resolve_anchors("top", "left", 5, 12).edges == ("top", "left")
    assert resolve_anchors("top", "right", 5, 12).edges == ("top", "right")
    # "center" anchors to neither side: layer-shell centres the surface
    assert resolve_anchors("top", "center", 5, 12).edges == ("top",)


def test_offsets_become_margins():
    anchors = resolve_anchors("bottom", "left", -7, -20)
    assert anchors.margins == {"bottom": 20, "left": 7}


# ---------------------------------------------------------------- fade envelope


def test_fade_envelope_fades_in_and_settles():
    now = [0.0]
    envelope = FadeEnvelope(100, clock=lambda: now[0])
    assert envelope.value == 0.0
    assert envelope.set_target(1.0) is True

    now[0] = 0.05
    assert 0.0 < envelope.value < 1.0
    assert not envelope.settled

    now[0] = 0.2
    assert envelope.value == pytest.approx(1.0)
    assert envelope.settled


def test_fade_envelope_set_target_is_a_no_op_when_unchanged():
    envelope = FadeEnvelope(140)
    assert envelope.set_target(1.0) is True
    assert envelope.set_target(1.0) is False
    assert envelope.set_target(0.0) is True


def test_fade_envelope_fades_out():
    now = [0.0]
    envelope = FadeEnvelope(100, clock=lambda: now[0])
    envelope.set_target(1.0)
    now[0] = 0.2
    assert envelope.value == pytest.approx(1.0)
    envelope.set_target(0.0)
    now[0] = 0.25
    assert 0.0 < envelope.value < 1.0
    now[0] = 0.4
    assert envelope.value == pytest.approx(0.0)
    assert envelope.settled


def test_default_fade_fits_the_hide_budget():
    from voicetype.overlay import HIDE_BUDGET_MS

    assert FadeEnvelope(140).duration * 1000 <= HIDE_BUDGET_MS


# ---------------------------------------------------------------- transcript


def test_transcript_animator_updates_its_words():
    animator = TranscriptAnimator(TranscriptConfig(word_limit=4))
    assert animator.set_text("سلام دنیا") is True
    assert animator.current_words == ("سلام", "دنیا")
    assert animator.set_text("سلام دنیا چطوری") is True
    assert animator.current_words == ("سلام", "دنیا", "چطوری")
    # only the last few words are kept
    assert animator.set_text("یک دو سه چهار پنج") is True
    assert animator.current_words == ("دو", "سه", "چهار", "پنج")


def test_transcript_animator_does_not_restart_on_a_character_correction():
    now = [0.0]
    animator = TranscriptAnimator(TranscriptConfig(), clock=lambda: now[0])
    animator.set_text("trans")
    started = animator.transition_started_at
    now[0] = 0.05
    assert animator.set_text("transcript") is True
    assert animator.current_words == ("transcript",)
    # the transition is preserved: only the last token changed
    assert animator.transition_started_at == started


def test_transcript_animator_frame_carries_the_render_words():
    now = [0.0]
    animator = TranscriptAnimator(TranscriptConfig(), clock=lambda: now[0])
    animator.set_text("hello world")
    frame = animator.frame(now[0])
    assert [word.text for word in frame.words] == ["hello", "world"]
    # the words fade in
    assert any(word.alpha <= 0.01 for word in frame.words)
    now[0] = 0.3
    frame = animator.frame(now[0])
    assert all(word.alpha > 0.01 for word in frame.words)


def test_transcript_animator_config_comes_from_the_overlay_config():
    config = TranscriptConfig.from_overlay_config(OverlayConfig())
    assert config.enabled is True
    assert config.word_limit >= 1


def test_the_transcript_knows_the_direction_it_runs_in():
    now = [0.0]
    animator = TranscriptAnimator(TranscriptConfig(), clock=lambda: now[0])
    animator.set_text("سلام دنیا")
    assert animator.frame(now[0]).direction == "rtl"
    animator.set_text("hello world")
    assert animator.frame(now[0]).direction == "ltr"


def test_the_direction_belongs_to_the_whole_paragraph_not_to_the_word_window():
    now = [0.0]
    animator = TranscriptAnimator(TranscriptConfig(word_limit=2), clock=lambda: now[0])
    # only the last two words are kept, but the paragraph is still Persian
    animator.set_text("من با Python code کار می کنم")
    frame = animator.frame(now[0])
    assert frame.current_words == ("می", "کنم")
    assert frame.direction == "rtl"


# ---------------------------------------------------------------- window surface


def test_transparency_css_keeps_the_window_surface_clear():
    css = TRANSPARENCY_CSS
    assert ".mic-osd-window" in css
    assert "background-color: transparent" in css
    assert "background-image: none" in css
    assert "box-shadow: none" in css


def test_the_transparency_css_is_a_module_level_constant():
    # installed once per process, so it must not be rebuilt per window
    assert isinstance(TRANSPARENCY_CSS, str)
    assert TRANSPARENCY_CSS is TRANSPARENCY_CSS


# ---------------------------------------------------------------- overlay.opacity


def test_opacity_scales_the_background_alpha():
    palette = resolve_palette("tokyo-night")
    styled = style_palette(palette, "", 0.5)
    assert styled.background[3] == pytest.approx(palette.background[3] * 0.5)
    # the colours themselves are untouched
    assert styled.background[:3] == palette.background[:3]


def test_opacity_zero_makes_the_card_fully_transparent():
    styled = style_palette(resolve_palette("tokyo-night"), "", 0.0)
    assert styled.background[3] == 0.0


def test_opacity_one_keeps_the_palette_alpha():
    palette = resolve_palette("tokyo-night")
    assert style_palette(palette, "", 1.0).background[3] == palette.background[3]


def test_the_accent_override_survives_the_opacity_scaling():
    styled = style_palette(resolve_palette("tokyo-night"), "#ff8800", 0.5)
    assert styled.accent == hex_to_rgba("#ff8800")
    assert styled.background[3] == pytest.approx(0.48)


def test_the_opacity_setting_is_read_from_the_config():
    settings = resolve_settings(OverlayConfig(opacity=0.4))
    assert settings.opacity == pytest.approx(0.4)


def test_the_settings_reach_the_palette():
    """The chain from the config to the pixels, without a compositor."""
    cfg = OverlayConfig(opacity=0.5, animations=False, accent="#ff8800")
    settings = resolve_settings(cfg)

    palette = style_palette(resolve_palette(cfg.theme), settings.accent, settings.opacity)

    assert palette.accent == hex_to_rgba("#ff8800")
    assert palette.background[3] == pytest.approx(palette.background[3])
    assert settings.animated is False


# ---------------------------------------------------------------- overlay.show_audio_meter


def test_the_audio_meter_switch_silences_the_visualization():
    assert meter_audio(False, 0.9, [0.5] * 8) == (0.0, [])
    assert meter_audio(True, 0.9, [0.5] * 8) == (0.9, [0.5] * 8)


def test_the_audio_meter_switch_is_read_from_the_config():
    assert resolve_settings(OverlayConfig(show_audio_meter=False)).audio_meter is False
    assert resolve_settings(OverlayConfig(show_audio_meter=True)).audio_meter is True


# ---------------------------------------------------------------- overlay.animations


def test_the_animation_switch_makes_the_fade_instant():
    settings = resolve_settings(OverlayConfig(animations=False))
    assert settings.fade_ms == 0
    envelope = FadeEnvelope(settings.fade_ms)
    assert envelope.duration == 0.0
    envelope.set_target(1.0)
    assert envelope.value == 1.0
    assert envelope.settled


def test_the_fade_keeps_its_duration_with_the_animations_on():
    settings = resolve_settings(OverlayConfig(animations=True, fade_ms=200))
    assert settings.fade_ms == 200


def test_the_animation_switch_stops_the_transcript_words_animating():
    config = resolve_settings(OverlayConfig(animations=False)).transcript
    assert config.enter_seconds == 0.0
    assert config.exit_seconds == 0.0
    assert config.stagger_seconds == 0.0


def test_the_transcript_keeps_its_timings_with_the_animations_on():
    config = resolve_settings(OverlayConfig(animations=True)).transcript
    assert config.enter_seconds > 0.0
    assert config.exit_seconds > 0.0
    assert config.stagger_seconds > 0.0


def test_a_transition_without_animations_is_immediate():
    now = [0.0]
    animator = TranscriptAnimator(
        TranscriptConfig(enter_seconds=0.0, exit_seconds=0.0, stagger_seconds=0.0),
        clock=lambda: now[0],
    )
    animator.set_text("hello world")
    frame = animator.frame(now[0])
    assert [word.text for word in frame.words] == ["hello", "world"]
    assert all(word.alpha == 1.0 for word in frame.words)


# ---------------------------------------------------------------- overlay.sounds


def test_the_mode_changes_pick_the_right_cue():
    assert mode_sound("hidden", "listening") == "up"
    assert mode_sound("listening", "processing") == "down"
    assert mode_sound("listening", "success") == "down"
    assert mode_sound("listening", "hidden") == "down"
    assert mode_sound("hidden", "error") == "error"


def test_a_mode_change_without_a_cue_is_silent():
    assert mode_sound("hidden", "processing") is None
    assert mode_sound("hidden", "hidden") is None
    assert mode_sound("listening", "listening") is None


class FakePlayer:
    """A sound player that only records the cues it was asked for."""

    def __init__(self) -> None:
        self.played: list[str] = []
        self.closed = False

    def play(self, name: str) -> None:
        self.played.append(name)

    def close(self) -> None:
        self.closed = True


def _bare_overlay():
    """A ``GtkOverlay`` with just the attributes the cue code touches.

    The real constructor needs a compositor, so the object is built without
    ``__init__`` and only the parts under test are filled in.
    """
    overlay = GtkOverlay.__new__(GtkOverlay)
    overlay._sounds_enabled = False
    overlay._sounds = None
    overlay._sounds_lock = threading.Lock()
    return overlay


def test_the_cues_are_only_played_when_they_are_enabled():
    overlay = _bare_overlay()
    assert overlay._sound_player() is None


def test_the_sound_player_is_created_on_the_first_cue():
    overlay = _bare_overlay()
    overlay._sounds_enabled = True
    player = overlay._sound_player()
    assert player is not None
    assert player.enabled is True
    # the player is reused, not rebuilt for every cue
    assert overlay._sound_player() is player
    overlay._sounds.close()


def test_the_cues_follow_the_mode_changes():
    overlay = _bare_overlay()
    player = FakePlayer()
    overlay._sounds = player
    overlay._sounds_enabled = True

    overlay._play_mode_sound("hidden", "listening")
    overlay._play_mode_sound("listening", "processing")
    overlay._play_mode_sound("hidden", "error")

    assert player.played == ["up", "down", "error"]


def test_the_sound_setting_is_read_from_the_config():
    assert resolve_settings(OverlayConfig(sounds=True)).sounds is True
    assert resolve_settings(OverlayConfig(sounds=False)).sounds is False


# ---------------------------------------------------------------- theme reloading


def test_a_palette_name_has_nothing_to_watch():
    assert theme_file("tokyo-night") is None
    assert theme_file("nocturne") is None
    assert theme_file("dark") is None
    assert theme_file("") is None


def test_a_theme_file_is_watched(tmp_path):
    css = tmp_path / "theme.css"
    css.write_text("@define-color accent-color #7aa2f7;\n", encoding="utf-8")
    assert theme_file(str(css)) == css


def test_a_missing_theme_file_is_not_watched(tmp_path):
    assert theme_file(str(tmp_path / "nope.css")) is None


def test_the_watcher_only_fires_when_the_file_changes(tmp_path):
    css = tmp_path / "theme.css"
    css.write_text("@define-color accent-color #7aa2f7;\n", encoding="utf-8")
    seen = []
    watcher = ThemeWatcher(css, on_change=lambda: seen.append(1))

    assert watcher.check_now() is False
    assert seen == []

    css.write_text("@define-color accent-color #ff8800;\n", encoding="utf-8")
    os.utime(css, (0, 0))
    assert watcher.check_now() is True
    assert seen == [1]


def test_a_failing_reload_does_not_stop_the_watcher(tmp_path):
    css = tmp_path / "theme.css"
    css.write_text("@define-color accent-color #7aa2f7;\n", encoding="utf-8")

    def boom():
        raise RuntimeError("broken theme")

    watcher = ThemeWatcher(css, on_change=boom)
    os.utime(css, (0, 0))
    assert watcher.check_now() is True
    # the poll survived the broken callback
    os.utime(css, (1, 1))
    assert watcher.check_now() is True


def _bare_overlay_with_theme(css_path, opacity=1.0):
    """A ``GtkOverlay`` with a theme, a palette and no drawing area."""
    from voicetype.ui.hud import ColorTween

    overlay = GtkOverlay.__new__(GtkOverlay)
    overlay._theme = Theme(str(css_path))
    overlay._accent = ""
    overlay._opacity = opacity
    overlay._state = HudState.LISTENING
    overlay._tint = ColorTween(0.2)
    overlay._area = None
    overlay.palette = style_palette(overlay._theme.palette, overlay._accent, overlay._opacity)
    return overlay


def test_a_theme_change_reloads_the_palette(tmp_path):
    css = tmp_path / "theme.css"
    css.write_text("@define-color accent-color #7aa2f7;\n", encoding="utf-8")
    overlay = _bare_overlay_with_theme(css)

    # a CSS theme starts from the default palette
    assert overlay.palette.background[:3] == PALETTES["nocturne"].background[:3]

    css.write_text("@define-color background-color #ff0000;\n", encoding="utf-8")
    os.utime(css, (0, 0))
    overlay._on_theme_changed()

    assert overlay.palette.background[0] == pytest.approx(1.0)


def test_a_theme_change_keeps_the_accent_and_the_opacity(tmp_path):
    css = tmp_path / "theme.css"
    css.write_text("@define-color accent-color #7aa2f7;\n", encoding="utf-8")
    overlay = _bare_overlay_with_theme(css, opacity=0.5)
    overlay._accent = "#ff8800"

    css.write_text("@define-color background-color #00ff00;\n", encoding="utf-8")
    os.utime(css, (0, 0))
    overlay._on_theme_changed()

    assert overlay.palette.accent == hex_to_rgba("#ff8800")
    # the CSS background replaces the palette's alpha, which is then scaled
    assert overlay.palette.background[3] == pytest.approx(0.5)


def test_a_broken_theme_change_keeps_the_old_palette(tmp_path):
    css = tmp_path / "theme.css"
    css.write_text("@define-color accent-color #7aa2f7;\n", encoding="utf-8")
    overlay = _bare_overlay_with_theme(css)
    before = overlay.palette

    def boom():
        raise RuntimeError("broken theme")

    overlay._theme.reload = boom
    overlay._on_theme_changed()

    assert overlay.palette is before


# ---------------------------------------------------------------- import safety


def _module_level_imports(path: Path) -> list[str]:
    """The modules a file imports at its top level."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def _package_files() -> list[Path]:
    import voicetype

    root = Path(voicetype.__file__).parent
    return sorted((root / "ui").rglob("*.py")) + [root / "overlay.py"]


@pytest.mark.parametrize("path", _package_files(), ids=lambda p: p.name)
def test_the_ui_layer_does_not_import_gtk_cairo_or_numpy_at_module_level(path):
    forbidden = ("gi", "gi.repository", "cairo", "numpy")
    for name in _module_level_imports(path):
        assert name.split(".")[0] not in {f.split(".")[0] for f in forbidden}, name
