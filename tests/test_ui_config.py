"""Tests for the overlay's configuration surface and its success feedback.

The overlay was ported to hyprwhspr's design language: a configurable
visualization, a palette, an accent colour, a live transcript and a fade
duration.  These tests pin down the *configuration* side of that port — the
defaults, the accepted values and the validation — plus the feedback plumbing
(the ``show_success`` call and the notifier's urgency vocabulary).
"""

from __future__ import annotations

import dataclasses
import subprocess
import time
import tomllib

import pytest

from tests.test_dispatcher import Backend, FakeAudio, Injector, Overlay, speak
from voicetype.config import (
    DEFAULT_FONT,
    DEFAULT_FONT_SIZE,
    LEGACY_THEME_NAMES,
    SAMPLE_CONFIG,
    THEME_NAMES,
    Config,
    ConfigError,
    OverlayConfig,
    load_config,
    parse_config,
)
from voicetype.dispatcher import Components, Dispatcher, NullOverlay
from voicetype.dispatcher import Overlay as OverlayProtocol
from voicetype.notify import Notifier

# ------------------------------------------------------------------ defaults


def test_new_overlay_defaults():
    cfg = OverlayConfig()
    assert cfg.style == "pill"
    assert cfg.animations is True
    assert cfg.show_transcript is True
    assert cfg.transcript_words == 4
    assert cfg.accent == ""
    assert cfg.fade_ms == 140
    assert cfg.sounds is False


def test_existing_overlay_defaults_are_unchanged():
    """The new keys must not have moved any of the old ones."""
    cfg = OverlayConfig()
    assert cfg.enabled is True
    assert cfg.position == "bottom"
    assert cfg.anchor_horizontal == "center"
    assert (cfg.offset_x, cfg.offset_y) == (0, 50)
    assert cfg.theme == "dark"
    assert cfg.opacity == 0.86
    assert (cfg.width, cfg.height) == (360, 96)
    assert cfg.show_partial_text is True and cfg.show_audio_meter is True


def test_the_overlay_font_follows_the_system_by_default():
    """With nothing configured the overlay uses the desktop's own font."""
    cfg = OverlayConfig()
    assert cfg.font == DEFAULT_FONT == ""
    assert cfg.font_size == DEFAULT_FONT_SIZE == 0.0


# ------------------------------------------------------------------ accepted values


@pytest.mark.parametrize(
    "key,value",
    [
        ("style", "pill"),
        ("style", "waveform"),
        ("style", "vu_meter"),
        ("animations", False),
        ("show_transcript", False),
        ("transcript_words", 7),
        ("transcript_words", 1),
        ("transcript_words", 12),
        ("accent", ""),
        ("accent", "#7aa2f7"),
        ("accent", "#7aa"),
        ("accent", "#7aa8"),
        ("accent", "#7aa2f780"),
        ("fade_ms", 0),
        ("fade_ms", 1000),
        ("sounds", True),
        ("font", "Fira Code"),
        ("font", ""),
        ("font_size", 0),
        ("font_size", 12),
        ("font_size", 72),
    ],
)
def test_new_overlay_keys_are_accepted(key, value):
    cfg = parse_config({"overlay": {key: value}})
    assert getattr(cfg.overlay, key) == value


def test_unknown_style_is_rejected():
    with pytest.raises(ConfigError) as ei:
        parse_config({"overlay": {"style": "hologram"}})
    assert "overlay.style" in str(ei.value)


@pytest.mark.parametrize("value", [-1, 1001])
def test_fade_ms_out_of_range_is_rejected(value):
    with pytest.raises(ConfigError) as ei:
        parse_config({"overlay": {"fade_ms": value}})
    assert "overlay.fade_ms" in str(ei.value)


@pytest.mark.parametrize("value", [0, 13])
def test_transcript_words_out_of_range_is_rejected(value):
    with pytest.raises(ConfigError) as ei:
        parse_config({"overlay": {"transcript_words": value}})
    assert "overlay.transcript_words" in str(ei.value)


@pytest.mark.parametrize("value", [-1, 73])
def test_font_size_out_of_range_is_rejected(value):
    with pytest.raises(ConfigError) as ei:
        parse_config({"overlay": {"font_size": value}})
    assert "overlay.font_size" in str(ei.value)


@pytest.mark.parametrize("value", ["nope", "#gggggg", "#12345", "7aa2f7"])
def test_invalid_accent_is_rejected(value):
    with pytest.raises(ConfigError) as ei:
        parse_config({"overlay": {"accent": value}})
    assert "overlay.accent" in str(ei.value)


def test_accent_must_be_a_string():
    with pytest.raises(ConfigError) as ei:
        parse_config({"overlay": {"accent": 123}})
    assert "overlay.accent" in str(ei.value)


# ------------------------------------------------------------------ theme


@pytest.mark.parametrize(
    "name",
    ["tokyo-night", "tokyo-night-light", "catppuccin-mocha", "rose-pine", "nord", "gruvbox-dark", "dracula"],
)
def test_palette_names_are_accepted(name):
    assert parse_config({"overlay": {"theme": name}}).overlay.theme == name


@pytest.mark.parametrize("name", ["dark", "light"])
def test_legacy_theme_names_are_accepted(name):
    assert parse_config({"overlay": {"theme": name}}).overlay.theme == name


def test_theme_names_are_case_insensitive():
    assert parse_config({"overlay": {"theme": "Tokyo-Night"}}).overlay.theme == "Tokyo-Night"


def test_unknown_theme_name_is_rejected():
    with pytest.raises(ConfigError) as ei:
        parse_config({"overlay": {"theme": "neon-disco"}})
    assert "overlay.theme" in str(ei.value)


def test_theme_css_path_is_accepted(tmp_path):
    css = tmp_path / "my-theme.css"
    css.write_text("@define-color accent-color #7aa2f7;\n", encoding="utf-8")
    cfg = parse_config({"overlay": {"theme": str(css)}})
    assert cfg.overlay.theme == str(css)


def test_missing_theme_css_path_is_rejected(tmp_path):
    missing = tmp_path / "nope.css"
    with pytest.raises(ConfigError) as ei:
        parse_config({"overlay": {"theme": str(missing)}})
    assert "overlay.theme" in str(ei.value)
    assert "not found" in str(ei.value)


def test_theme_path_must_be_a_css_file(tmp_path):
    other = tmp_path / "theme.txt"
    other.write_text("nothing", encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        parse_config({"overlay": {"theme": str(other)}})
    assert "overlay.theme" in str(ei.value)


def test_config_theme_names_match_the_ui_palettes():
    """The core must not import the UI package, so the names are repeated."""
    from voicetype.ui.theme import PALETTE_NAMES

    assert set(THEME_NAMES) == set(PALETTE_NAMES)
    assert set(LEGACY_THEME_NAMES) == {"dark", "light"}


def test_every_accepted_theme_resolves_to_a_palette():
    from voicetype.ui.theme import resolve_palette

    for name in THEME_NAMES + LEGACY_THEME_NAMES:
        assert resolve_palette(name) is not None


# ------------------------------------------------------------------ sample config


def _sample_overlay_table() -> dict:
    return tomllib.loads(SAMPLE_CONFIG).get("overlay", {})


def test_sample_config_still_parses(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE_CONFIG, encoding="utf-8")
    assert load_config(path) == Config()


def test_sample_config_covers_every_overlay_field():
    table = _sample_overlay_table()
    fields = {f.name for f in dataclasses.fields(OverlayConfig)}
    assert fields <= set(table), fields - set(table)


def test_sample_config_uses_the_defaults():
    table = _sample_overlay_table()
    defaults = {f.name: f.default for f in dataclasses.fields(OverlayConfig)}
    for name, value in defaults.items():
        assert table[name] == value, name


# ------------------------------------------------------------------ overlay protocol


def test_null_overlay_has_show_success():
    assert hasattr(NullOverlay(), "show_success")


def test_null_overlay_satisfies_the_overlay_protocol():
    assert isinstance(NullOverlay(), OverlayProtocol)


def test_the_protocol_declares_show_success():
    assert hasattr(OverlayProtocol, "show_success")
    attrs = getattr(OverlayProtocol, "__protocol_attrs__", None)  # Python 3.12+
    if attrs is not None:
        assert "show_success" in attrs


# ------------------------------------------------------------------ dispatcher


def _dispatcher(**kw):
    overlay = Overlay()
    dispatcher = Dispatcher(
        Components(Config(), Backend(), Injector(), lambda t: t),
        lambda on_chunk, cfg, on_err: FakeAudio(on_chunk),
        overlay,
        error_display_seconds=0.05,
        success_display_seconds=0.05,
        **kw,
    )
    return dispatcher, overlay


def test_dispatcher_shows_success_after_the_injection():
    d, ov = _dispatcher()
    d.start()
    speak()
    d.stop()
    assert d.wait_idle()
    names = ov.names()
    assert "show_success" in names
    # the overlay is hidden before the paste keystroke, then confirms the result
    assert names.index("hide") < names.index("show_success")
    success = [e for e in ov.events if e[0] == "show_success"]
    assert success[0][1] == "سلام دنیا"


def test_dispatcher_hides_the_overlay_again_after_the_success():
    d, ov = _dispatcher()
    d.start()
    speak()
    d.stop()
    assert d.wait_idle()
    deadline = time.time() + 2
    while ov.names()[-1] != "hide" and time.time() < deadline:
        time.sleep(0.01)
    assert ov.names()[-1] == "hide"
    assert ov.names().index("show_success") < len(ov.names()) - 1


def test_dispatcher_does_not_show_success_when_the_injection_fails():
    d, ov = _dispatcher()
    d._c.injector = Injector(exc=RuntimeError("no paste"))
    d.start()
    speak()
    d.stop()
    assert d.wait_idle()
    assert "show_success" not in ov.names()
    assert any(e[0] == "show_error" for e in ov.events)


# ------------------------------------------------------------------ notifier


class Run:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")


def _notifier():
    run = Run()
    return Notifier(run, which=lambda name: "/usr/bin/notify-send"), run


def test_notifier_error_keeps_its_behaviour():
    n, run = _notifier()
    n.error("Network error")
    cmd = run.calls[0]
    assert cmd[0] == "notify-send"
    assert cmd[-1] == "Network error" and "--" in cmd
    assert cmd[cmd.index("-a") + 1] == "VoiceType"
    assert cmd[cmd.index("-i") + 1] == "audio-input-microphone"
    assert cmd[cmd.index("-t") + 1] == "4000"
    assert cmd[cmd.index("-u") + 1] == "critical"


def test_notifier_urgency_vocabulary():
    n, run = _notifier()
    n.error("boom")
    n.warning("careful")
    n.info("fyi")
    n.success("done")
    urgencies = [cmd[cmd.index("-u") + 1] for cmd in run.calls]
    assert urgencies == ["critical", "normal", "low", "low"]
    timeouts = [cmd[cmd.index("-t") + 1] for cmd in run.calls]
    assert timeouts == ["4000", "3000", "2000", "2000"]


def test_notifier_truncates_long_messages():
    n, run = _notifier()
    n.info("x" * 500)
    assert run.calls[0][-1] == "x" * 300


def test_notifier_without_notify_send_does_nothing():
    run = Run()
    Notifier(run, which=lambda name: None).warning("nothing")
    assert run.calls == []
