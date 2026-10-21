"""Headless tests for the overlay's rendering plumbing.

Nothing here needs a compositor: the GTK class is only inspected, never
instantiated, and everything that is exercised is a pure function.
"""

import ast
import inspect
import math
import os
import threading
from pathlib import Path

import pytest

from voicetype.config import DEFAULT_FONT, DEFAULT_FONT_SIZE, OverlayConfig
from voicetype.dispatcher import NullOverlay
from voicetype.overlay import (
    MODE_TO_STATE,
    STATUS_FONT_SCALE,
    SYSTEM_FONT_SCALE,
    TRANSPARENCY_CSS,
    Anchors,
    GtkOverlay,
    create_overlay,
    display_text,
    fitting_size,
    meter_audio,
    mode_sound,
    preview_height,
    resolve_accent,
    resolve_anchors,
    resolve_settings,
    scaled_font_size,
    smooth,
    status_text_for,
    style_palette,
    synthesize_samples,
    theme_file,
    total_width,
    transcript_words,
    word_positions,
)
from voicetype.ui.animations import FadeEnvelope, StateManager, VisualizerState
from voicetype.ui.theme import PALETTES, Theme, ThemeWatcher, hex_to_rgba, resolve_palette
from voicetype.ui.transcript import TranscriptAnimator, TranscriptConfig
from voicetype.ui.visualizations import create_visualization

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


# ---------------------------------------------------------------- mode → state


def test_mode_to_state_mapping():
    assert MODE_TO_STATE == {
        "listening": "recording",
        "processing": "processing",
        "error": "error",
        "warning": "paused",
        "success": "success",
    }


def test_a_warning_is_a_paused_notice_not_a_recording():
    # a warning is a static amber notice: it must not look like a live mic
    assert MODE_TO_STATE["warning"] == "paused"
    assert MODE_TO_STATE["warning"] != "recording"


def test_every_mode_maps_to_a_known_visualizer_state():
    known = {state.value for state in VisualizerState}
    for mode, state in MODE_TO_STATE.items():
        assert state in known, mode


def test_status_text_uses_the_mode_labels_and_the_messages():
    assert status_text_for("listening") == "listening"
    assert status_text_for("processing") == "processing"
    assert status_text_for("error", "خطا رخ داد") == "خطا رخ داد"
    assert status_text_for("warning", "هشدار") == "هشدار"
    assert status_text_for("hidden") == ""


def test_every_label_is_lower_case():
    assert status_text_for("error", "Could not understand the audio") == "could not understand the audio"
    assert status_text_for("warning", "Provider Fallback") == "provider fallback"
    assert status_text_for("success", "Hello World") == "hello world"
    assert display_text("Hello World") == "hello world"
    assert display_text("") == ""


def test_the_live_transcript_is_lower_case_too():
    assert transcript_words(["Hello", "WORLD", "سلام"]) == ["hello", "world", "سلام"]


def test_one_font_for_the_whole_osd():
    """One font for the whole OSD: the labels, the messages and the transcript."""
    cfg = OverlayConfig()
    # the live transcript shares the overlay's font
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


def test_a_big_font_needs_a_taller_surface():
    metrics = (1.4, 0.4)
    assert preview_height(42.0, 7.0, 40.0, metrics) > preview_height(42.0, 7.0, 17.0, metrics)


def test_a_font_that_does_not_fit_is_shrunk():
    # a 20pt font with an ascent of 1.4 px per point needs 28 px above the
    # baseline
    assert fitting_size(20.0, 1.4, 40.0) == 20.0
    assert fitting_size(20.0, 1.4, 10.0) == pytest.approx(10.0 / 1.4)


def test_a_font_that_does_not_fit_the_surface_at_all_is_kept():
    # nothing sensible can be drawn in no space, so the size is left alone
    assert fitting_size(20.0, 1.4, 0.0) == 20.0


def test_the_system_size_is_scaled_up():
    """The OSD is read at a glance, so the system size is multiplied."""
    assert scaled_font_size(0.0, 10.0) == pytest.approx(10.0 * SYSTEM_FONT_SCALE)
    assert scaled_font_size(0.0, 10.0) > 10.0


def test_a_configured_size_wins_over_the_system_size():
    assert scaled_font_size(18.0, 10.0) == 18.0


# ---------------------------------------------------------------- theme / accent


def test_resolve_palette_known_name():
    palette = resolve_palette("tokyo-night")
    assert palette is PALETTES["tokyo-night"]


def test_resolve_palette_unknown_name_falls_back():
    assert resolve_palette("does-not-exist") is PALETTES["tokyo-night"]
    assert resolve_palette("") is PALETTES["tokyo-night"]


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


# ---------------------------------------------------------------- audio


def test_synthesize_samples_pads_and_truncates():
    assert synthesize_samples([], 4) == [0.0, 0.0, 0.0, 0.0]
    assert synthesize_samples([0.5], 4) == [0.0, 0.0, 0.0, 0.5]
    assert synthesize_samples([0.1, 0.2, 0.3, 0.4, 0.5], 4) == [0.2, 0.3, 0.4, 0.5]
    assert synthesize_samples([0.5], 0) == []


def test_word_positions_are_centred():
    positions = word_positions([10.0, 20.0], 5.0, 100.0)
    assert positions == [32.5, 47.5]
    assert word_positions([], 5.0, 100.0) == []


def test_an_rtl_transcript_is_laid_out_from_the_right():
    # "سلام دنیا" — the first word is the rightmost one
    positions = word_positions([10.0, 20.0], 5.0, 100.0, ["rtl", "rtl"], "rtl")
    assert positions == [57.5, 32.5]


def test_the_english_words_of_a_mixed_transcript_keep_their_order():
    # "من با Python code کردم" — the two English words stay side by side in
    # their own order, the Persian words keep theirs
    directions = ["rtl", "ltr", "ltr", "rtl"]
    positions = word_positions([10.0] * 4, 5.0, 100.0, directions, "rtl")
    # visual order, left to right: code-ish run, then the Persian words
    assert positions[1] < positions[2]  # Python before code
    assert positions[3] < positions[0]  # the last Persian word is leftmost
    assert positions[0] > positions[2]  # the first Persian word is rightmost


def test_the_words_stay_centred_whatever_the_direction_is():
    ltr = word_positions([10.0, 20.0, 30.0], 5.0, 100.0, ["ltr"] * 3, "ltr")
    rtl = word_positions([10.0, 20.0, 30.0], 5.0, 100.0, ["rtl"] * 3, "rtl")
    assert ltr == [15.0, 30.0, 55.0]
    assert rtl == [75.0, 50.0, 15.0]


def test_total_width_counts_the_spaces_between_the_words():
    assert total_width([], 5.0) == 0.0
    assert total_width([(None, 10.0), (None, 20.0)], 5.0) == 35.0


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


def test_the_settings_reach_the_visualization():
    """The chain from the config to the pixels, without a compositor."""
    cfg = OverlayConfig(opacity=0.5, animations=False, accent="#ff8800")
    settings = resolve_settings(cfg)

    palette = style_palette(resolve_palette(cfg.theme), settings.accent, settings.opacity)
    viz = create_visualization("pill", palette)
    viz.set_surface_opacity(settings.opacity)
    viz.set_animated(settings.animated)

    assert viz.palette.accent == hex_to_rgba("#ff8800")
    assert viz.palette.background[3] == pytest.approx(0.96 * 0.5)
    assert viz.surface_opacity == pytest.approx(0.5)
    assert viz.state_manager.animated is False


# ---------------------------------------------------------------- overlay.show_audio_meter


def test_the_audio_meter_switch_silences_the_visualization():
    assert meter_audio(False, 0.9, [0.5] * 8) == (0.0, [])
    assert meter_audio(True, 0.9, [0.5] * 8) == (0.9, [0.5] * 8)


def test_the_audio_meter_switch_is_read_from_the_config():
    assert resolve_settings(OverlayConfig(show_audio_meter=False)).audio_meter is False
    assert resolve_settings(OverlayConfig(show_audio_meter=True)).audio_meter is True


# ---------------------------------------------------------------- overlay.animations


def test_the_state_envelope_is_constant_without_animations():
    manager = StateManager(animated=False)
    manager.set_state_from_string("recording")
    assert manager.get_animation_value() == 1.0
    # the phase does not matter either
    manager.animation_phase = 1.234
    assert manager.get_animation_value() == 1.0


def test_the_phase_stops_advancing_without_animations():
    manager = StateManager(animated=False)
    manager.update(1.0)
    assert manager.animation_phase == 0.0


def test_the_animations_can_be_switched_at_runtime():
    manager = StateManager()
    manager.set_state_from_string("error")
    manager.animation_phase = 0.0
    strobing = manager.get_animation_value()
    manager.animation_phase = math.pi / 4
    assert manager.get_animation_value() != strobing

    manager.set_animated(False)
    assert manager.get_animation_value() == 1.0
    assert manager.animated is False


def test_the_visualization_passes_the_animation_switch_on():
    viz = create_visualization("pill")
    viz.set_state("recording")
    viz.set_animated(False)
    assert viz.state_manager.animated is False
    assert viz.state_manager.get_animation_value() == 1.0


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
    """A ``GtkOverlay`` with a theme, a visualization and no drawing area."""
    overlay = GtkOverlay.__new__(GtkOverlay)
    overlay._theme = Theme(str(css_path))
    overlay._accent = ""
    overlay._opacity = opacity
    overlay._viz = create_visualization("pill")
    overlay._area = None
    overlay.palette = style_palette(overlay._theme.palette, overlay._accent, overlay._opacity)
    return overlay


def test_a_theme_change_reloads_the_palette(tmp_path):
    css = tmp_path / "theme.css"
    css.write_text("@define-color accent-color #7aa2f7;\n", encoding="utf-8")
    overlay = _bare_overlay_with_theme(css)

    assert overlay.palette.background[:3] == PALETTES["tokyo-night"].background[:3]

    css.write_text("@define-color background-color #ff0000;\n", encoding="utf-8")
    os.utime(css, (0, 0))
    overlay._on_theme_changed()

    assert overlay.palette.background[0] == pytest.approx(1.0)
    # the visualization got the new palette too
    assert overlay._viz.palette.background[0] == pytest.approx(1.0)


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
    assert overlay._viz.surface_opacity == pytest.approx(0.5)


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

