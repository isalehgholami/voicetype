"""GTK4 + gtk4-layer-shell overlay (docs/adr/0003) with hyprwhspr's visual language.

The rendering layer was ported from hyprwhspr's ``lib/mic_osd``: a transparent
layer-shell window, a state-driven visualization (pill, waveform or VU meter)
and a word-by-word live transcript drawn with Pango so Persian and Arabic
shape and reorder correctly.

``gi`` and ``cairo`` are imported lazily — the module must stay importable on
a machine without GTK (ADR-0001: "the core is free of heavy dependencies and
without Qt/GTK").  All GTK work happens on one dedicated thread running a
GLib main loop; the public methods are thread-safe because they only enqueue
work with ``GLib.idle_add``.
"""

from __future__ import annotations

import atexit
import math
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import DEFAULT_FONT_SIZE, OverlayConfig, resolve_font_family, resolve_font_size
from .dispatcher import NullOverlay, Overlay
from .log import get_logger
from .text import LTR, RTL, word_directions
from .ui.animations import FadeEnvelope, VisualizerState
from .ui.easing import clamp
from .ui.sounds import SoundPlayer
from .ui.theme import Palette, Theme, ThemeWatcher, hex_to_rgba
from .ui.transcript import TranscriptAnimator, TranscriptConfig

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from .ui.visualizations.base import BaseVisualization

log = get_logger("overlay")

FRAME_MS = 16
LEVEL_SMOOTHING = 0.35

#: How long the success state stays on screen before the overlay hides itself.
SUCCESS_HIDE_MS = 1200

#: The G5 budget for the overlay to disappear after the user stops talking
#: (docs/VoiceType-Architecture.md).  The default ``fade_ms`` (140) fits.
HIDE_BUDGET_MS = 300

#: How many recent level values are kept to synthesise a waveform.
LEVEL_BUFFER_SIZE = 64

#: The number of buckets in the synthesised pseudo-waveform.
WAVEFORM_BUCKETS = 32

#: The status label is drawn at this share of the main text: the transcript
#: carries the words, the label only the state.
STATUS_FONT_SCALE = 0.7

#: The overlay's text is bigger than the system's own font: the OSD is read
#: at a glance, and a system size of nine or eleven points is too small for
#: it.  A configured ``overlay.font_size`` wins over this.
SYSTEM_FONT_SCALE = 1.6

#: The font size used when the desktop names no size of its own.
FALLBACK_FONT_SIZE = 11.0

#: The ascent and the descent of a typical face, per point of font size.  They
#: are only used when the real font cannot be measured.
FALLBACK_ASCENT = 1.2
FALLBACK_DESCENT = 0.4

#: Maps an overlay mode onto the visualizer state it drives.
MODE_TO_STATE: dict[str, str] = {
    "listening": "recording",
    "processing": "processing",
    "error": "error",
    # A warning is a static amber notice, not a recording: it gets the paused
    # state, which is flat and does not pretend the microphone is live.
    "warning": "paused",
    "success": "success",
}

#: The cue played for a mode change (see :func:`mode_sound`).
SOUND_UP = "up"
SOUND_DOWN = "down"
SOUND_ERROR = "error"

#: The status labels shown when there is no live transcript to draw.
STATUS_TEXT: dict[str, str] = {"listening": "listening", "processing": "processing"}

#: ``overlay.position`` → the vertical layer-shell edge the surface is anchored to.
POSITION_TO_EDGE: dict[str, str] = {
    "top": "top",
    "center": "top",  # "center" used to be ignored entirely — see resolve_anchors()
    "bottom": "bottom",
}

#: ``overlay.anchor_horizontal`` → the horizontal layer-shell edge.
HORIZONTAL_TO_EDGE: dict[str, str] = {"left": "left", "right": "right"}

#: Which palette colour the status label uses in each mode.
MODE_TO_COLOR: dict[str, str] = {
    "listening": "text",
    "processing": "processing",
    "error": "error",
    "warning": "paused",
    "success": "success",
}

#: The transparency rule hyprwhspr installs on its OSD window.
TRANSPARENCY_CSS = """
window.mic-osd-window,
.mic-osd-window {
    background-color: transparent;
    background-image: none;
    box-shadow: none;
}
"""

#: Installed once per process; see :func:`install_transparency_css`.
_transparency_provider = None


@dataclass(frozen=True)
class Anchors:
    """The layer-shell placement of the overlay window."""

    #: The edges the surface is anchored to ("top", "bottom", "left", "right").
    edges: tuple[str, ...]
    #: The margin (in pixels) for each anchored edge.
    margins: dict[str, int]


def smooth(current: float, target: float, factor: float = LEVEL_SMOOTHING) -> float:
    """Exponential smoothing for the level meter (pure; unit-tested)."""
    return current + (target - current) * factor


def resolve_anchors(
    position: str,
    anchor_horizontal: str,
    offset_x: int = 0,
    offset_y: int = 12,
) -> Anchors:
    """Work out the layer-shell edges and margins for a placement.

    ``position`` selects the vertical edge.  ``"center"`` used to be ignored
    completely — it matched neither "top" nor "bottom", so the surface got no
    vertical anchor at all and ended up wherever the compositor felt like
    putting it.  It now anchors to the top edge with the configured offset,
    like every other position.

    ``anchor_horizontal`` selects the horizontal edge; ``"center"`` anchors to
    neither side, which is how layer-shell centres a surface.
    """
    edges: list[str] = []
    margins: dict[str, int] = {}

    vertical = POSITION_TO_EDGE.get(str(position or "").strip().lower())
    if vertical is not None:
        edges.append(vertical)
        margins[vertical] = abs(int(offset_y))

    horizontal = HORIZONTAL_TO_EDGE.get(str(anchor_horizontal or "").strip().lower())
    if horizontal is not None:
        edges.append(horizontal)
        margins[horizontal] = abs(int(offset_x))

    return Anchors(tuple(edges), margins)


def resolve_accent(palette: Palette, accent: str) -> tuple:
    """Return the accent colour, honouring the ``overlay.accent`` override.

    An empty value keeps the palette's accent; an unparsable one is ignored
    (with a warning) rather than taking the overlay down.
    """
    value = str(accent or "").strip()
    if not value:
        return palette.accent
    try:
        return hex_to_rgba(value)
    except ValueError:
        log.warning("invalid overlay.accent %r; using the palette accent", accent)
        return palette.accent


def status_text_for(mode: str, message: str = "") -> str:
    """The label shown for ``mode`` when there is no live transcript.

    Every label is lower case: the overlay speaks in one voice.
    """
    if mode in ("error", "warning", "success"):
        return display_text(message)
    return STATUS_TEXT.get(mode, "")


def display_text(text: str) -> str:
    """The overlay's text style: everything the user reads is lower case."""
    return str(text or "").lower()


def transcript_words(words: Sequence[str]) -> list[str]:
    """The live transcript's words as they are drawn (lower case)."""
    return [display_text(word) for word in words]


def mode_sound(previous: str, mode: str) -> str | None:
    """The cue that belongs to a mode change (``None`` when nothing happened).

    * entering ``listening`` — the recording started
    * leaving ``listening`` — the recording stopped
    * entering ``error`` — something failed

    The error cue wins over the stop cue: the recording did stop with the
    error, but the failure is the more important information.
    """
    if mode == "error":
        return SOUND_ERROR
    if previous == "listening" and mode != "listening":
        return SOUND_DOWN
    if mode == "listening" and previous != "listening":
        return SOUND_UP
    return None


def theme_file(name: str) -> Path | None:
    """The theme file to watch, or ``None`` when the theme is a palette name.

    Only a file can change under the user's hands, so a palette name has
    nothing to watch.
    """
    key = (name or "").strip()
    if not key:
        return None
    candidate = Path(key).expanduser()
    # The suffix test matches the theme resolver, so the watcher watches
    # exactly the file the palette is read from.
    if candidate.suffix == ".css" and candidate.is_file():
        return candidate
    return None


def status_color(palette: Palette, mode: str) -> tuple:
    """The palette colour the status label uses in ``mode``."""
    return getattr(palette, MODE_TO_COLOR.get(mode, "text"), palette.text)


def synthesize_samples(history: Sequence[float], count: int = WAVEFORM_BUCKETS) -> list[float]:
    """Turn a history of level values into a pseudo-waveform.

    The ``Overlay`` protocol only carries a scalar level, so the waveform
    visualization — which wants a *history* of amplitudes — is fed the recent
    levels instead of real PCM.  It is an approximation of a waveform, not a
    faithful one; the bar heights are what carry the meaning anyway.
    """
    if count <= 0:
        return []
    values = [float(v) for v in history][-count:]
    if not values:
        return [0.0] * count
    if len(values) < count:
        values = [0.0] * (count - len(values)) + values
    return values


def word_positions(
    widths: Sequence[float],
    space: float,
    area_width: float,
    directions: Sequence[str] | None = None,
    base: str = LTR,
) -> list[float]:
    """The left edge of every word, laid out centred in ``area_width``.

    ``directions`` gives the direction of every word ("ltr" or "rtl"); when it
    is omitted every word counts as left-to-right.  Neighbouring words of the
    same direction form a run: a right-to-left run is laid out from the right,
    a left-to-right run from the left, and the runs follow the direction of
    the paragraph (``base``) — from the right for a Persian or Arabic
    transcript.

    That is what the bidirectional algorithm does with "من با Python code
    کردم": the two English words stay in their own order, and the Persian
    words keep theirs.
    """
    if not widths:
        return []
    count = len(widths)
    if directions is None:
        directions = [LTR] * count

    # Group the words into runs of the same direction.
    runs: list[tuple[str, list[int]]] = []
    for index, direction in enumerate(directions):
        if runs and runs[-1][0] == direction:
            runs[-1][1].append(index)
        else:
            runs.append((direction, [index]))

    # The runs are ordered like the paragraph: in a right-to-left paragraph
    # the first run of the text is the rightmost one.
    if base == RTL:
        runs = list(reversed(runs))

    total = sum(widths) + space * max(0, count - 1)
    cursor = (area_width - total) / 2.0
    positions = [0.0] * count
    for direction, indices in runs:
        run_width = sum(widths[index] for index in indices) + space * (len(indices) - 1)
        if direction == RTL:
            # the first word of the run is flush with its right edge
            x = cursor + run_width
            for index in indices:
                x -= widths[index]
                positions[index] = x
                x -= space
        else:
            x = cursor
            for index in indices:
                positions[index] = x
                x += widths[index] + space
        cursor += run_width + space
    return positions


def total_width(entries: Sequence[tuple[object, float]], space: float) -> float:
    """The width of a laid-out word list, including the spaces between words."""
    if not entries:
        return 0.0
    return sum(width for _, width in entries) + space * max(0, len(entries) - 1)


def preview_height(
    pill_height: float,
    offset_y: float,
    size: float,
    metrics: tuple[float, float],
) -> float:
    """The surface height a text of ``size`` points needs above the pill.

    The live transcript is painted above the pill, so a large font needs a
    taller surface: the words would otherwise be clipped by the top edge.
    """
    ascent, descent = metrics
    return pill_height + 4.0 + offset_y + (ascent + descent) * size


def fitting_size(size: float, ascent: float, baseline: float) -> float:
    """The biggest size whose ascent still fits above ``baseline``.

    The text is drawn just above the pill, so a font that is too tall for
    the space above it would be clipped by the surface.
    """
    if ascent <= 0.0 or baseline <= 0.0:
        return size
    return min(size, baseline / ascent)


def scaled_font_size(size: float, system_size: float) -> float:
    """The point size the main text is drawn with.

    A configured size wins.  Without one the system's own size is scaled up:
    the OSD is read at a glance, and a system size of nine or eleven points
    is too small for it.
    """
    return size if size > 0.0 else system_size * SYSTEM_FONT_SCALE


def system_font() -> tuple[str, float]:
    """The desktop's own font: the family and the size, in points.

    The family and the size come from the desktop settings, so the overlay
    looks like the rest of the system.  Without a display they cannot be
    read: the family stays empty (Pango's default) and the size falls back.
    """
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Pango", "1.0")
        from gi.repository import Gtk, Pango

        settings = Gtk.Settings.get_default()
        name = settings.get_property("gtk-font-name") if settings is not None else ""
        description = Pango.FontDescription(str(name or ""))
        family = str(description.get_family() or "")
        size = float(description.get_size()) / float(Pango.SCALE)
        return (family, size if size > 0.0 else FALLBACK_FONT_SIZE)
    except Exception:  # noqa: BLE001 - the desktop font is a nicety
        log.debug("the system font could not be read", exc_info=True)
        return ("", FALLBACK_FONT_SIZE)


def resolve_overlay_font(cfg: OverlayConfig) -> tuple[str, float]:
    """The font family and the point size the overlay draws with.

    This is what ``voicetype doctor`` reports: the configured family and
    size, or the system's own font.
    """
    settings = resolve_settings(cfg)
    family, size = settings.font_family, settings.font_size
    if not family or size <= 0.0:
        system_family, system_size = system_font()
        family = family or system_family
        size = scaled_font_size(size, system_size)
    return (family, size)


def install_transparency_css(Gtk, Gdk) -> None:
    """Make the overlay window's surface transparent (once per process).

    A themed desktop's ``~/.config/gtk-4.0/gtk.css`` paints an opaque
    window colour at ``STYLE_PROVIDER_PRIORITY_USER``.  The overlay's
    transparency rule has to outrank it, or the theme's colour is painted as
    an opaque slab behind the pill (hyprwhspr issue #248).
    """
    global _transparency_provider

    if _transparency_provider is not None:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(TRANSPARENCY_CSS)
    Gtk.StyleContext.add_provider_for_display(
        display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_USER + 1,
    )
    _transparency_provider = provider


def preload_layer_shell() -> str | None:
    """Load libgtk4-layer-shell *before* libwayland so it can bind zwlr_layer_shell_v1.

    gtk4-layer-shell interposes libwayland symbols to grab the layer-shell global when the
    display is opened. If GDK (and with it libwayland) is loaded first, the global is never
    bound and every window fails with "Failed to initialize layer surface ... may have been
    linked after libwayland". See gtk4-layer-shell's linking.md.
    """
    import ctypes
    import ctypes.util

    candidates = [
        ctypes.util.find_library("gtk4-layer-shell"),
        "libgtk4-layer-shell.so",
        "/usr/lib/libgtk4-layer-shell.so",
        "/usr/lib64/libgtk4-layer-shell.so",
        "/usr/local/lib/libgtk4-layer-shell.so",
    ]
    for cand in candidates:
        if not cand:
            continue
        try:
            ctypes.CDLL(cand, mode=ctypes.RTLD_GLOBAL)
            return cand
        except OSError:
            continue
    return None


def create_overlay(cfg: OverlayConfig) -> Overlay:
    if not cfg.enabled:
        return NullOverlay()
    try:
        preload_layer_shell()
        overlay = GtkOverlay(cfg)
    except Exception as e:  # noqa: BLE001 - missing gi / no layer-shell support
        log.warning("GTK overlay unavailable (%s); running without overlay. Run `voicetype doctor`.", e)
        return NullOverlay()
    # The overlay owns a theme watcher and a GLib loop; both are released when
    # the process goes away.
    atexit.register(overlay.close)
    return overlay


def _create_visualization(style: str, palette: Palette) -> BaseVisualization:
    """Build the visualization named by ``style``.

    The import is deferred on purpose: the overlay module must import cleanly
    even when the visualization package is unavailable.
    """
    from .ui.visualizations import create_visualization

    return create_visualization(style, palette)


@dataclass(frozen=True)
class OverlaySettings:
    """The configuration values the overlay paints with, resolved once."""

    #: The ``overlay.theme`` value: a palette name or the path to a CSS file.
    theme: str
    #: The ``overlay.accent`` override ("" = the palette decides).
    accent: str
    #: The ``overlay.opacity`` factor applied to the card's alpha.
    opacity: float
    #: Whether the overlay animates at all (``overlay.animations``).
    animated: bool
    #: Whether the visualization reacts to the microphone.
    audio_meter: bool
    #: Whether the audible cues are played.
    sounds: bool
    #: The show/hide fade duration in milliseconds (0 = instant).
    fade_ms: int
    #: The font family the user configured (``overlay.font``); empty means
    #: "the system's own font".
    font_family: str
    #: The point size of the main text the user configured
    #: (``overlay.font_size``); zero means "the system's own font size".
    font_size: float
    #: The live transcript's settings.
    transcript: TranscriptConfig


def resolve_settings(cfg: OverlayConfig) -> OverlaySettings:
    """Resolve the overlay's configuration into the values it paints with.

    The master animation switch reaches three places: the show/hide fade
    becomes instant, the transcript words stop animating and the state
    envelopes stop pulsing.
    """
    animated = bool(getattr(cfg, "animations", True))
    transcript = TranscriptConfig.from_overlay_config(cfg)
    if not animated:
        transcript = replace(
            transcript,
            enter_seconds=0.0,
            exit_seconds=0.0,
            stagger_seconds=0.0,
        )
    fade_ms = 0 if not animated else int(getattr(cfg, "fade_ms", 140))
    font_size = resolve_font_size(getattr(cfg, "font_size", DEFAULT_FONT_SIZE))
    return OverlaySettings(
        theme=str(getattr(cfg, "theme", "dark") or "dark"),
        accent=str(getattr(cfg, "accent", "") or ""),
        opacity=clamp(float(getattr(cfg, "opacity", 0.86)), 0.0, 1.0),
        animated=animated,
        audio_meter=bool(getattr(cfg, "show_audio_meter", True)),
        sounds=bool(getattr(cfg, "sounds", False)),
        fade_ms=fade_ms,
        font_family=resolve_font_family(getattr(cfg, "font", "")),
        font_size=font_size,
        transcript=transcript,
    )


def style_palette(palette: Palette, accent: str = "", opacity: float = 1.0) -> Palette:
    """Apply the accent override and the surface opacity to ``palette``.

    The opacity scales the alpha of the background colour, which is the card
    the waveform and the VU meter fill; the palette's own alpha is kept, so
    a translucent palette stays translucent.
    """
    styled = replace(palette, accent=resolve_accent(palette, accent))
    background = styled.background
    factor = clamp(float(opacity), 0.0, 1.0)
    return replace(
        styled,
        background=(background[0], background[1], background[2], background[3] * factor),
    )


def meter_audio(enabled: bool, level: float, samples: Sequence[float]) -> tuple[float, list[float]]:
    """The audio a frame feeds into the visualization.

    With ``overlay.show_audio_meter`` off the microphone is ignored
    altogether: the level is silence and no samples are handed over, so the
    bars stay flat on the floor.
    """
    if not enabled:
        return 0.0, []
    return level, list(samples)


class GtkOverlay:
    """The GTK4 layer-shell overlay.

    The window is a transparent surface on the OVERLAY layer: it never takes
    focus, never reserves screen space and lets clicks through.  Its content
    is painted by a :mod:`voicetype.ui.visualizations` implementation, with the
    live transcript and the status label drawn on top with Pango.

    The configuration keys that shape the look are honoured here:

    * ``overlay.opacity`` scales the alpha of the card the visualization is
      drawn on.  The palette's ``background`` colour carries its own alpha,
      so the setting multiplies it; the pill paints its own near-black body
      and gets the same factor through the visualization's
      ``surface_opacity``.
    * ``overlay.show_audio_meter`` decides whether the visualization reacts to
      the microphone at all.  With it off the level is treated as silence.
    * ``overlay.animations`` is the master switch for motion: the show/hide
      fade becomes instant, the state envelopes stop pulsing and the
      transcript words appear without animating.
    * ``overlay.sounds`` plays a short cue when the recording starts, stops
      or fails.
    """

    def __init__(self, cfg: OverlayConfig) -> None:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")

        self.cfg = cfg
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._error: Exception | None = None

        self._GLib: Any = None
        self._Pango: Any = None
        self._PangoCairo: Any = None
        self._win = None
        self._area = None
        self._loop = None
        self._viz: BaseVisualization | None = None
        self._timer = 0
        self._success_timer = 0
        self._last_tick = time.monotonic()

        self._mode = "hidden"
        self._text = ""
        self._level = 0.0
        self._levels: deque[float] = deque(maxlen=LEVEL_BUFFER_SIZE)
        self._samples: list[float] = []

        self._settings = resolve_settings(cfg)
        # The font is resolved once the desktop settings are known; until
        # then these are the values the user configured.
        self._font_family = self._settings.font_family
        self._font_size = self._settings.font_size
        self._status_font_size = 0.0
        #: The ascent and the descent of the font, per point; measured once.
        self._metrics: tuple[float, float] | None = None
        # The theme is kept as a live object, not just a resolved palette, so
        # an edited theme file can be picked up without a daemon restart.
        self._theme_name = self._settings.theme
        self._theme = Theme(self._theme_name)
        self._accent = self._settings.accent
        self._opacity = self._settings.opacity
        self._animated = self._settings.animated
        self._audio_meter = self._settings.audio_meter
        self._sounds_enabled = self._settings.sounds
        self._sounds: SoundPlayer | None = None
        self._sounds_lock = threading.Lock()
        self.palette = style_palette(self._theme.palette, self._accent, self._opacity)

        # With the animations off the fade is instant: the overlay appears
        # and disappears without a transition.
        self._fade = FadeEnvelope(self._settings.fade_ms)
        self._animator = TranscriptAnimator(self._settings.transcript)
        self._transcript_enabled = self._animator.config.enabled and bool(
            getattr(cfg, "show_partial_text", True)
        )

        self._watcher: ThemeWatcher | None = None
        watched = theme_file(self._theme_name)
        if watched is not None:
            self._watcher = ThemeWatcher(watched, on_change=self._on_theme_changed)

        self._thread = threading.Thread(target=self._run, daemon=True, name="overlay-gtk")
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("GTK overlay thread did not start")
        if self._error is not None:
            raise self._error

    # ------------------------------------------------------------------ theme
    def _on_theme_changed(self) -> None:
        """Re-read the theme file and repaint (runs on the GTK thread).

        A broken theme file must never take the overlay down, so every step
        of the reload is guarded.
        """
        try:
            palette = style_palette(self._theme.reload(), self._accent, self._opacity)
            self.palette = palette
            viz = self._viz
            if viz is not None:
                viz.set_palette(palette)
                viz.set_surface_opacity(self._opacity)
            if self._area is not None:
                self._area.queue_draw()
        except Exception:  # noqa: BLE001 - a failed reload keeps the old palette
            log.debug("theme reload failed", exc_info=True)

    def _start_theme_watcher(self) -> None:
        """Start polling the theme file (runs on the GTK thread)."""
        watcher = self._watcher
        if watcher is None:
            return
        try:
            if watcher.start():
                log.info("watching the theme file %s", watcher.path)
        except Exception:  # noqa: BLE001 - the watcher is a nicety
            log.debug("theme watcher unavailable", exc_info=True)

    # ------------------------------------------------------------------ font
    def _resolve_font(self) -> None:
        """Resolve the font every text of the overlay is drawn with.

        With nothing configured the overlay follows the desktop's own font:
        the family the settings ask for, and their size multiplied — the
        OSD is read at a glance, and a system size of nine or eleven points
        is too small for it.  A configured family or size wins, and the
        status label keeps its share of the main text either way.
        """
        system_family, system_size = system_font()
        self._font_family = self._settings.font_family or system_family
        self._font_size = scaled_font_size(self._settings.font_size, system_size)
        self._status_font_size = self._font_size * STATUS_FONT_SCALE
        # The live transcript draws with the very same font.
        self._animator.config = replace(
            self._animator.config,
            font_family=self._font_family,
            font_size=self._font_size,
        )

    def _font_description(self, size: float):
        """The Pango font description of the configured font.

        A part that is not known is left out of the description, so Pango
        falls back to the system's own font for it.
        """
        parts = []
        if self._font_family:
            parts.append(self._font_family)
        if size > 0.0:
            parts.append(f"{size:g}")
        return self._Pango.FontDescription(" ".join(parts))

    def _font_metrics(self) -> tuple[float, float]:
        """The ascent and the descent of the font, per point of font size.

        They decide how much room the text takes above the pill, so they are
        measured from the font itself instead of being guessed.  The result
        is cached: the font does not change while the overlay is alive.
        """
        if self._metrics is not None:
            return self._metrics
        try:
            import cairo

            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            layout = self._PangoCairo.create_layout(cairo.Context(surface))
            layout.set_font_description(self._font_description(self._font_size))
            layout.set_text("Ag", -1)
            line = layout.get_line_readonly(0)
            if line is not None and self._font_size > 0.0:
                _, logical = line.get_extents()
                scale = float(self._Pango.SCALE)
                ascent = max(0.0, -logical.y / scale) / self._font_size
                descent = max(0.0, (logical.height + logical.y) / scale) / self._font_size
                if ascent > 0.0:
                    self._metrics = (ascent, descent)
                    return self._metrics
        except Exception:  # noqa: BLE001 - the metrics are a nicety
            log.debug("the font metrics could not be measured", exc_info=True)
        self._metrics = (FALLBACK_ASCENT, FALLBACK_DESCENT)
        return self._metrics

    # ------------------------------------------------------------------ GTK thread
    def _run(self) -> None:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo
        from gi.repository import Gtk4LayerShell as LS

        self._GLib = GLib
        self._Pango = Pango
        self._PangoCairo = PangoCairo
        try:
            # gtk_init() aborts the process when no display can be opened, so
            # the check variant is used: a headless daemon must degrade to
            # "no overlay", not die.
            if hasattr(Gtk, "init_check"):
                if not Gtk.init_check():
                    raise RuntimeError("no display available")
            else:
                Gtk.init()
            # the display must be open before the compositor's layer-shell global can be bound
            if hasattr(LS, "is_supported") and not LS.is_supported():
                raise RuntimeError("compositor does not support wlr-layer-shell")

            install_transparency_css(Gtk, Gdk)

            # The font is resolved before anything is measured: the surface
            # grows with it and every text is laid out with it.
            self._resolve_font()

            viz = _create_visualization(str(getattr(self.cfg, "style", "pill")), self.palette)
            # the accent override (and any future palette change) reaches the
            # visualization through its palette
            viz.set_palette(self.palette)
            # the card's opacity and the master animation switch are the
            # visualization's own settings
            viz.set_surface_opacity(self._opacity)
            viz.set_animated(self._animated)
            self._viz = viz

            width, height = self._surface_size(viz)

            win = Gtk.Window()
            win.set_decorated(False)
            win.set_resizable(False)
            win.set_default_size(width, height)
            # GTK gives every window the "background" style class, which paints
            # the theme's opaque window colour across the whole surface.
            win.remove_css_class("background")
            win.add_css_class("mic-osd-window")

            area = Gtk.DrawingArea()
            area.set_content_width(width)
            area.set_content_height(height)
            area.set_draw_func(self._draw)
            win.set_child(area)

            LS.init_for_window(win)
            LS.set_namespace(win, "voicetype")
            LS.set_layer(win, LS.Layer.OVERLAY)
            LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)  # never takes focus
            LS.set_exclusive_zone(win, -1)

            anchors = resolve_anchors(
                getattr(self.cfg, "position", "top"),
                getattr(self.cfg, "anchor_horizontal", "center"),
                getattr(self.cfg, "offset_x", 0),
                getattr(self.cfg, "offset_y", 12),
            )
            edges = {
                "top": LS.Edge.TOP,
                "bottom": LS.Edge.BOTTOM,
                "left": LS.Edge.LEFT,
                "right": LS.Edge.RIGHT,
            }
            for name, edge in edges.items():
                LS.set_anchor(win, edge, name in anchors.edges)
            for name, margin in anchors.margins.items():
                LS.set_margin(win, edges[name], margin)
        except Exception as e:  # noqa: BLE001 - reported to the caller
            self._error = e
            self._ready.set()
            return

        self._win, self._area = win, area
        self._loop = GLib.MainLoop()
        self._start_theme_watcher()
        self._ready.set()
        self._loop.run()

    def _surface_size(self, viz) -> tuple[int, int]:
        """The size of the drawing surface.

        The live transcript is painted above the pill, so a surface that is
        too short for the pill's preview threshold is grown — otherwise the
        text would land on top of the pill (hyprwhspr does the same).  A
        large font grows the surface as well: the words need the room.
        """
        width = int(self.cfg.width)
        height = int(self.cfg.height)
        if self._transcript_enabled:
            threshold = int(getattr(viz, "PREVIEW_HEIGHT_THRESHOLD", 0) or 0)
            if threshold:
                height = max(height, threshold + 1)
            height = max(height, int(math.ceil(self._preview_height(viz))))
        return width, height

    def _preview_height(self, viz) -> float:
        """The surface height the configured font needs above the pill."""
        pill_h = float(getattr(viz, "PILL_HEIGHT", 0) or 0)
        if pill_h <= 0.0:
            return 0.0
        offset = float(self._animator.config.offset_y)
        return preview_height(pill_h, offset, self._font_size, self._font_metrics())

    def _make_click_through(self) -> None:
        try:
            import cairo

            surface = self._win.get_surface()
            if surface is not None:
                surface.set_input_region(cairo.Region())
        except Exception:  # noqa: BLE001
            log.debug("click-through not applied", exc_info=True)

    # ------------------------------------------------------------------ frame loop
    def _tick(self) -> bool:
        """Advance the animations and repaint. Returns ``False`` when done."""
        now = time.monotonic()
        dt = min(0.25, max(0.0, now - self._last_tick))
        self._last_tick = now

        self._feed_visualization(dt)

        # The fade envelope is clock-driven: reading its value is what
        # advances it towards the target.
        self._area.queue_draw()

        with self._lock:
            hidden = self._mode == "hidden"
        if hidden and self._fade.settled:
            win = self._win
            if win is not None:
                win.set_visible(False)
            self._timer = 0
            return False
        return True

    def _feed_visualization(self, dt: float) -> None:
        """Advance the visualization with the current audio frame.

        The state manager's phase is the animation clock.  The base
        implementation advances it with a fixed 1/60 s step — which is exactly
        this loop's frame interval — so that step is traded for the measured
        delta.  Visualizations that drive the phase themselves are left
        untouched.
        """
        viz = self._viz
        if viz is None:
            return
        manager = viz.state_manager
        level, samples = self._current_audio()
        before = manager.animation_phase
        viz.update(level, samples)
        fixed_step = manager.PHASE_RATE / 60.0
        advanced = (manager.animation_phase - before) % (2 * math.pi)
        if abs(advanced - fixed_step) < 1e-6:
            manager.update(dt)
            manager.animation_phase = (manager.animation_phase - fixed_step) % (2 * math.pi)

    def _current_audio(self) -> tuple[float, list[float]]:
        """The audio this frame feeds into the visualization.

        With ``overlay.show_audio_meter`` off the microphone is ignored
        altogether: the level is silence and no samples are handed over, so
        the bars stay flat on the floor.
        """
        if not self._audio_meter:
            return meter_audio(False, 0.0, [])
        with self._lock:
            level = self._level
            if self._samples:
                return meter_audio(True, level, self._samples)
            history = list(self._levels)
        return meter_audio(True, level, synthesize_samples(history, WAVEFORM_BUCKETS))

    def _start_timer(self) -> None:
        if not self._timer:
            self._last_tick = time.monotonic()
            self._timer = self._GLib.timeout_add(FRAME_MS, self._tick)

    # ------------------------------------------------------------------ state
    def _apply(self, mode: str, text: str | None = None) -> bool:
        """Switch the overlay to ``mode`` (runs on the GTK thread)."""
        win = self._win
        if win is None:
            return False  # the overlay was closed
        with self._lock:
            previous_mode = self._mode
            self._mode = mode
            if text is not None:
                self._text = text
            elif mode in ("listening", "hidden"):
                self._text = ""

        viz = self._viz
        was_recording = viz is not None and viz.state is VisualizerState.RECORDING
        state = MODE_TO_STATE.get(mode)
        if viz is not None and state is not None:
            viz.set_state(state)
        if mode == "hidden":
            self._animator.clear()
        elif was_recording and viz is not None and viz.state is not VisualizerState.RECORDING:
            # the live transcript belongs to the recording state only
            self._animator.clear()

        if mode == "hidden":
            # the window is only taken down once the fade has settled
            self._fade.set_target(0.0)
        else:
            self._fade.set_target(1.0)
            if not win.get_visible():
                win.present()
                self._make_click_through()
            if mode == "success":
                self._schedule_success_hide()

        self._play_mode_sound(previous_mode, mode)
        self._start_timer()
        self._area.queue_draw()
        return False

    # ------------------------------------------------------------------ sounds
    def _sound_player(self) -> SoundPlayer | None:
        """The sound player, created on the first cue.

        It only exists when ``overlay.sounds`` is on, and it never imports
        GTK: the cues are synthesised and played on a background thread.
        """
        if not self._sounds_enabled:
            return None
        with self._sounds_lock:
            if self._sounds is None:
                self._sounds = SoundPlayer(enabled=True)
            return self._sounds

    def _play_mode_sound(self, previous: str, mode: str) -> None:
        """Play the cue that belongs to a mode change."""
        name = mode_sound(previous, mode)
        if name is None:
            return
        player = self._sound_player()
        if player is None:
            return
        player.play(name)

    def _schedule_success_hide(self) -> None:
        if self._success_timer:
            self._GLib.source_remove(self._success_timer)
        self._success_timer = self._GLib.timeout_add(SUCCESS_HIDE_MS, self._success_hide)

    def _success_hide(self) -> bool:
        self._success_timer = 0
        self._apply("hidden")
        return False

    # ------------------------------------------------------------------ painting
    def _draw(self, area, cr, width, height) -> None:
        viz = self._viz
        if viz is None:
            return
        alpha = self._fade.value
        # Everything is painted into a group so the show/hide fade applies to
        # the visualization and the text alike.
        cr.push_group()
        try:
            viz.draw_background(cr, width, height)
            viz.draw(cr, width, height)
            self._draw_text(cr, width, height)
        finally:
            cr.pop_group_to_source()
        cr.paint_with_alpha(alpha)

    def _draw_text(self, cr, width: int, height: int) -> None:
        """Draw the live transcript, or the status label when there is none."""
        if self._mode == "listening" and self._draw_transcript(cr, width, height):
            return
        label = status_text_for(self._mode, self._text)
        if not label:
            return
        self._draw_label(cr, width, height, label, status_color(self.palette, self._mode))

    def _draw_label(self, cr, width: int, height: int, text: str, color) -> None:
        """Draw a single-line status label with Pango (bidi-correct)."""
        available = max(0.0, float(width) - 20.0)
        if available <= 0.0:
            return
        baseline = self._text_baseline(width, height)
        size = fitting_size(self._status_font_size, self._font_metrics()[0], baseline)
        layout = self._layout(cr, text, size)
        layout.set_width(int(available * self._Pango.SCALE))
        layout.set_ellipsize(self._Pango.EllipsizeMode.END)
        layout.set_alignment(self._Pango.Alignment.CENTER)
        x = (float(width) - available) / 2.0
        y = baseline + self._baseline_offset(layout)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.55)
        cr.move_to(x, y + 2.0)
        self._PangoCairo.show_layout(cr, layout)
        cr.set_source_rgba(color[0], color[1], color[2], 0.97)
        cr.move_to(x, y)
        self._PangoCairo.show_layout(cr, layout)

    def _draw_transcript(self, cr, width: int, height: int) -> bool:
        """Draw the live transcript above the pill. Returns ``True`` when drawn."""
        viz = self._viz
        if viz is None or not self._transcript_enabled:
            return False
        if viz.state is not VisualizerState.RECORDING:
            return False
        frame = self._animator.frame(time.monotonic())
        if not frame.words:
            return False

        config = self._animator.config
        size = float(config.font_size)
        max_width = min(config.max_width, max(0.0, float(width) - 20.0))
        # A Persian or Arabic transcript is laid out from the right; the
        # direction of the paragraph comes from the transcript itself.
        base = frame.direction
        previous_directions = word_directions(frame.previous_words, base)
        current_directions = word_directions(frame.current_words, base)

        # The words sit just above the pill: a font that does not fit the
        # space above it is shrunk, so a large ``overlay.font_size`` never
        # clips the text.
        baseline = self._text_baseline(width, height)
        size = fitting_size(size, self._font_metrics()[0], baseline)

        space = self._measure(cr, " ", size)
        previous = self._layout_words(cr, frame.previous_words, size)
        current = self._layout_words(cr, frame.current_words, size)
        widest = max(total_width(previous, space), total_width(current, space))
        if max_width > 0.0 and widest > max_width:
            size = max(8.0, size * max_width / widest)
            space = self._measure(cr, " ", size)
            previous = self._layout_words(cr, frame.previous_words, size)
            current = self._layout_words(cr, frame.current_words, size)

        previous_positions = word_positions(
            [w for _, w in previous], space, width, previous_directions, base
        )
        current_positions = word_positions(
            [w for _, w in current], space, width, current_directions, base
        )

        for word in frame.words:
            if word.alpha <= 0.01:
                continue
            if word.source == "previous":
                if word.index >= len(previous_positions):
                    continue
                x = previous_positions[word.index]
                layout = previous[word.index][0]
            else:
                if word.index >= len(current_positions):
                    continue
                x = current_positions[word.index]
                layout = current[word.index][0]
                if word.matched_from is not None and word.matched_from < len(previous_positions):
                    # a word that survived the diff slides to its new position
                    old_x = previous_positions[word.matched_from]
                    x = old_x + (x - old_x) * word.layout_progress
            y = baseline + word.y_offset + self._baseline_offset(layout)

            # A restrained shadow keeps the text readable over bright windows
            # without introducing a visible badge or background rectangle.
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.55 * word.alpha)
            cr.move_to(x, y + 2.0)
            self._PangoCairo.show_layout(cr, layout)

            cr.set_source_rgba(1.0, 1.0, 1.0, 0.97 * word.alpha)
            cr.move_to(x, y)
            self._PangoCairo.show_layout(cr, layout)
        return True

    def _layout(self, cr, text: str, size: float):
        """A Pango layout for ``text`` at ``size`` points.

        The whole overlay is drawn with one font — the configured
        ``overlay.font`` — so the labels, the messages and the live transcript
        all share the same face.
        """
        layout = self._PangoCairo.create_layout(cr)
        layout.set_font_description(self._font_description(size))
        layout.set_text(text, -1)
        return layout

    def _baseline_offset(self, layout) -> float:
        """How far the layout's origin sits *above* its first baseline.

        ``pango_cairo_show_layout`` places the layout's top-left corner at the
        current point, not its baseline, so the offset has to be added to put
        the text on the intended baseline.
        """
        line = layout.get_line_readonly(0)
        if line is None:
            return 0.0
        _, logical = line.get_extents()
        return logical.y / float(self._Pango.SCALE)

    def _layout_words(self, cr, words: Sequence[str], size: float) -> list[tuple[object, float]]:
        """Lay every word out and measure it: ``[(layout, width), …]``."""
        out = []
        for word in transcript_words(words):
            layout = self._layout(cr, word, size)
            out.append((layout, self._layout_width(layout)))
        return out

    def _layout_width(self, layout) -> float:
        """The logical width of a Pango layout, in pixels."""
        return layout.get_extents()[1].width / float(self._Pango.SCALE)

    def _measure(self, cr, text: str, size: float) -> float:
        return self._layout_width(self._layout(cr, text, size))

    def _text_baseline(self, width: int, height: int) -> float:
        """The baseline the overlay's text sits on."""
        viz = self._viz
        geometry = getattr(viz, "pill_geometry", None)
        if callable(geometry):
            try:
                _, pill_y, _, _ = geometry(width, height)
            except Exception:  # noqa: BLE001 - a broken geometry must not kill the paint
                pill_y = float(height) - 46.0
            return float(pill_y) - float(self._animator.config.offset_y)
        # No pill: the label goes to the bottom of the surface.
        return max(12.0, float(height) - 10.0)

    # ------------------------------------------------------------------ thread-safe API
    def _post(self, fn, *args) -> None:
        if self._GLib is None:
            return
        self._GLib.idle_add(fn, *args)

    def show_listening(self) -> None:
        self._post(self._apply, "listening", "")

    def set_partial(self, text: str) -> None:
        self._post(self._set_partial, text)

    def _set_partial(self, text: str) -> bool:
        # ``TranscriptAnimator`` handles character-level corrections without
        # restarting the animation, so the partial is handed over as it is.
        self._animator.set_text(text)
        self._area.queue_draw()
        return False

    def set_audio_level(self, level: float, samples=None) -> None:
        """Feed a new audio level (and optionally a sample buffer).

        The ``Overlay`` protocol only carries the scalar level; the optional
        ``samples`` argument is an extension used by the waveform
        visualization.
        """
        try:
            value = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._level = value
            self._levels.append(value)
            if samples is not None:
                self._samples = [float(s) for s in samples]

    def show_processing(self) -> None:
        self._post(self._apply, "processing", "")

    def show_error(self, message: str) -> None:
        self._post(self._apply, "error", message)

    def show_warning(self, message: str) -> None:
        self._post(self._apply, "warning", message)

    def show_success(self, text: str = "") -> None:
        """Show the success state; the overlay hides itself after ~1.2 s."""
        self._post(self._apply, "success", text)

    def hide(self) -> None:
        self._post(self._apply, "hidden", None)

    # ------------------------------------------------------------------ teardown
    def close(self) -> None:
        """Tear the overlay down.

        The theme watcher and the sound player own resources outside the
        overlay, so they are released here; the window is destroyed and the GTK
        loop is asked to quit so the overlay's thread ends with it.  Everything
        is guarded: tearing down must never raise.
        """
        watcher = self._watcher
        self._watcher = None
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:  # noqa: BLE001 - shutting down anyway
                log.debug("theme watcher stop failed", exc_info=True)

        # the overlay may be replaced by a new one (a reload): its exit hook
        # must not pile up
        try:
            atexit.unregister(self.close)
        except Exception:  # noqa: BLE001 - never fatal
            pass

        with self._sounds_lock:
            sounds = self._sounds
            self._sounds = None
        if sounds is not None:
            try:
                sounds.close()
            except Exception:  # noqa: BLE001
                log.debug("sound player close failed", exc_info=True)

        try:
            self._post(self._destroy_window)
            self._post(self._quit_loop)
        except Exception:  # noqa: BLE001 - the loop may already be gone
            log.debug("could not quit the GTK loop", exc_info=True)

    def _destroy_window(self) -> bool:
        """Destroy the layer-shell surface (runs on the GTK thread).

        The surface belongs to the compositor: without an explicit destroy it
        stays on the screen when the overlay is replaced by a new one.
        """
        win, self._win = self._win, None
        if win is not None:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001 - tearing down anyway
                log.debug("could not destroy the overlay window", exc_info=True)
        return False

    def _quit_loop(self) -> bool:
        loop = self._loop
        if loop is not None:
            loop.quit()
        return False
