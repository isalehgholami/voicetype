"""GTK4 + gtk4-layer-shell overlay (docs/adr/0003) with the HUD of ADR-0008.

The overlay is one floating card: a status row, the text that matters and a
slim waveform.  It is a display, not an application — there is no button, no
menu and no gesture in it, and nothing in it drives the application.  The
states it shows come from the dispatcher: listening, transcribing,
translating, finished, idle and failed.

The card has a **fixed size**.  It never resizes while the user talks: the
text lives in a fixed box inside the card and rolls through it — what does
not fit scrolls, and a text that is replaced rolls out while its successor
rolls in.

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
from typing import Any

from .config import DEFAULT_FONT_SIZE, OverlayConfig, resolve_font_family, resolve_font_size
from .dispatcher import NullOverlay, Overlay
from .log import get_logger
from .ui.animations import FadeEnvelope
from .ui.easing import clamp
from .ui.hud import (
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
from .ui.sounds import SoundPlayer
from .ui.theme import Palette, Theme, ThemeWatcher, hex_to_rgba
from .ui.transcript import TranscriptAnimator, TranscriptConfig

log = get_logger("overlay")

FRAME_MS = 16
LEVEL_SMOOTHING = 0.35

#: How long the finished state stays on screen before the overlay hides itself.
SUCCESS_HIDE_MS = 1200

#: A finished translation gets longer: the sentence has to be readable.
TRANSLATION_HIDE_MS = 2200

#: The G5 budget for the overlay to disappear after the user stops talking
#: (docs/VoiceType-Architecture.md).  The default ``fade_ms`` (140) fits.
HIDE_BUDGET_MS = 300

#: How many recent level values are kept to drive the waveform.
LEVEL_BUFFER_SIZE = 64

#: The status labels are drawn at this share of the main text.
STATUS_FONT_SCALE = 0.72

#: The font size used when the desktop names no size of its own.
FALLBACK_FONT_SIZE = 11.0

#: The ascent and the descent of a typical face, per point of font size.  They
#: are only used when the real font cannot be measured.
FALLBACK_ASCENT = 1.2
FALLBACK_DESCENT = 0.4

#: How far the status label's letters are spread out, in em.
LETTER_SPACING = 0.09

#: The overlay's modes and the HUD state each of them shows.  The finished
#: state is TRANSLATED when the result carries a language and DONE when it
#: does not.
MODE_TO_STATE: dict[str, HudState] = {
    "listening": HudState.LISTENING,
    "processing": HudState.TRANSCRIBING,
    "translating": HudState.TRANSLATING,
    "success": HudState.TRANSLATED,
    "error": HudState.ERROR,
    "warning": HudState.NOTICE,
    "hidden": HudState.READY,
}

#: The cue played for a mode change (see :func:`mode_sound`).
SOUND_UP = "up"
SOUND_DOWN = "down"
SOUND_ERROR = "error"

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


#: ``overlay.position`` → the vertical layer-shell edge the surface is anchored to.
POSITION_TO_EDGE: dict[str, str] = {
    "top": "top",
    "center": "top",  # "center" used to be ignored entirely — see resolve_anchors()
    "bottom": "bottom",
}

#: ``overlay.anchor_horizontal`` → the horizontal layer-shell edge.
HORIZONTAL_TO_EDGE: dict[str, str] = {"left": "left", "right": "right"}


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


def status_text_for(mode: str) -> str:
    """The status label the card shows for ``mode``.

    The labels are upper case: they are a whisper, not a headline.  The text
    the card carries — the speech, the translation, a message — keeps the
    case it was written in.
    """
    state = MODE_TO_STATE.get(mode)
    if state is None:
        return ""
    return hud_label(state)


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


def style_palette(palette: Palette, accent: str = "", opacity: float = 1.0) -> Palette:
    """Apply the accent override and the surface opacity to ``palette``.

    The opacity scales the alpha of the background colour, which is the card
    the whole HUD is drawn on; the palette's own alpha is kept, so a
    translucent palette stays translucent.
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
    is the floating card of :mod:`voicetype.ui.hud` — a status row, the text
    that matters and the waveform — painted with Cairo and drawn with Pango.

    The configuration keys that shape the look are honoured here:

    * ``overlay.opacity`` scales the alpha of the card.
    * ``overlay.show_audio_meter`` decides whether the waveform reacts to the
      microphone at all.  With it off the level is treated as silence.
    * ``overlay.animations`` is the master switch for motion: the show/hide
      fade becomes instant, the state envelopes stop pulsing and the words
      stop animating.  A desktop that asks for reduced motion is honoured
      the same way.
    * ``overlay.sounds`` plays a short cue when the recording starts, stops
      or fails.
    * ``overlay.width`` and ``overlay.height`` are the **fixed size** of the
      card.  It does not grow with the text it holds: the text lives in a
      fixed box inside the card and rolls through it.
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
        self._Gdk: Any = None
        self._win = None
        self._area = None
        self._loop = None
        self._timer = 0
        self._success_timer = 0
        self._last_tick = time.monotonic()

        self._mode = "hidden"
        self._level = 0.0
        self._levels: deque[float] = deque(maxlen=LEVEL_BUFFER_SIZE)
        self._samples: list[float] = []

        self._settings = resolve_settings(cfg)
        # The font is resolved once the desktop settings are known; until
        # then these are the values the user configured.
        self._font_family = self._settings.font_family
        self._font_size = self._settings.font_size
        self._status_font_size = self._settings.font_size * STATUS_FONT_SCALE
        self._secondary_font_size = self._settings.font_size * 0.86
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

        # The card's own model: what it shows, what it is in the middle of
        # showing, and how its bars move.
        self._hud = HudMetrics()
        self._state = HudState.READY
        self._label = Crossfade(self._hud.transition)
        #: The text box: a fixed window the text rolls through.
        self._ticker = TextTicker(self._hud)
        self._tint = ColorTween(self._hud.transition)
        self._wave = Waveform(self._hud)
        self._language = ""
        #: The source of the translation, kept for the finished state.
        self._source = ""
        #: The live text the card currently shows.
        self._live_text = ""
        #: The opacity of every word of the live preview.
        self._word_alphas: tuple[float, ...] = ()
        #: The height of one line of the main text; measured once.
        self._line_height = 0.0
        #: The card's fixed size: it does not follow the text it holds.
        self._card_width = float(cfg.width)
        self._card_height = float(cfg.height)

        # The surface follows the card: the size is measured while painting
        # and eased into place by the frame loop.
        self._size = (0, 0)
        self._target_size = (0, 0)
        self._size_ready = False

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
            # The state's colour follows the palette, so it is re-tinted.
            self._tint.set(hud_color(palette, self._state))
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
        """Resolve the font every text of the card is drawn with.

        With nothing configured the overlay follows the desktop's own font:
        the family the settings ask for, and their size — the HUD is read
        from a few centimetres away, but it is a compact card, so the size
        the desktop asks for is the right one.  A configured family or size
        wins, and the status label keeps its share of the main text either
        way.
        """
        system_family, system_size = system_font()
        self._font_family = self._settings.font_family or system_family
        primary, status, secondary = hud_font_sizes(self._settings.font_size, system_size)
        self._font_size = primary
        self._status_font_size = status
        self._secondary_font_size = secondary

    def _font_description(self, size: float, medium: bool = False):
        """The Pango font description of the configured font.

        A part that is not known is left out of the description, so Pango
        falls back to the system's own font for it.
        """
        parts = []
        if self._font_family:
            parts.append(self._font_family)
        if size > 0.0:
            parts.append(f"{size:g}")
        description = self._Pango.FontDescription(" ".join(parts))
        if medium:
            weight = getattr(self._Pango.Weight, "MEDIUM", None)
            if weight is not None:
                description.set_weight(weight)
        return description

    def _font_metrics(self) -> tuple[float, float]:
        """The ascent and the descent of the font, per point of font size.

        The result is cached: the font does not change while the overlay is
        alive.
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
        self._Gdk = Gdk
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

            # The font is resolved before anything is measured: the card grows
            # with it and every text is laid out with it.
            self._resolve_font()
            self._apply_motion_settings()

            win = Gtk.Window()
            win.set_decorated(False)
            win.set_resizable(False)
            # GTK gives every window the "background" style class, which paints
            # the theme's opaque window colour across the whole surface.
            win.remove_css_class("background")
            win.add_css_class("mic-osd-window")

            width = int(self._card_width + self._hud.surface_margin * 2)
            height = int(self._card_height + self._hud.surface_margin * 2)
            win.set_default_size(width, height)
            self._size = (width, height)
            self._target_size = (width, height)

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

    def _apply_motion_settings(self) -> None:
        """Honour ``overlay.animations`` and the desktop's reduced motion.

        A desktop that asks for reduced motion gets the same treatment as a
        user who switched the animations off: nothing moves, nothing blinks.
        """
        animated = self._animated and self._desktop_animations()
        duration = self._hud.transition if animated else 0.0
        for fade in (self._label,):
            fade.duration = duration
        self._ticker.duration = duration
        self._tint.duration = duration
        self._wave.animated = animated

    def _desktop_animations(self) -> bool:
        """Whether the desktop wants animations (GTK's own switch)."""
        try:
            from gi.repository import Gtk

            settings = Gtk.Settings.get_default()
            if settings is None:
                return True
            return bool(settings.get_property("gtk-enable-animations"))
        except Exception:  # noqa: BLE001 - the switch is a nicety
            return True

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

        self._advance_waveform(dt)
        self._sync_live_text()
        self._ticker.advance(dt)
        self._advance_size(dt)

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

    def _advance_waveform(self, dt: float) -> None:
        """Move the waveform on: with the microphone, or with the work."""
        state = self._state
        if state == HudState.LISTENING and self._audio_meter:
            with self._lock:
                level = self._level
                history = list(self._levels)
            self._wave.update(state, level, history, dt)
            return
        self._wave.update(state, 0.0, None, dt)

    def _sync_live_text(self) -> None:
        """Keep the card's live text in step with the animator.

        The animator owns the word window and the idle timeout: it knows when
        a word was corrected and when the speaker stopped.  The card only
        shows what the animator says — and a text that grows replaces the old
        one in place, while a text that goes away rolls out of the box.
        """
        if self._state not in (HudState.LISTENING, HudState.TRANSCRIBING):
            self._word_alphas = ()
            return
        if not self._transcript_enabled:
            self._word_alphas = ()
            return
        frame = self._animator.frame(time.monotonic())
        words = " ".join(frame.current_words)
        # The opacity of every word of the live preview: the words are drawn
        # as one paragraph by Pango, so each of them gets its own opacity
        # through a foreground attribute.
        self._word_alphas = tuple(word.alpha for word in frame.words if word.source == "current")
        if words == self._live_text:
            return
        self._live_text = words
        if words:
            # The live preview grows in place: it rolls with the words, and
            # rolling it away would make it unreadable.
            self._ticker.set(words, "", instant=True, live=True)
        else:
            self._ticker.set("", "")

    def _advance_size(self, dt: float) -> None:
        """Ease the surface towards the size the card asked for.

        The card's size is measured while it is painted; the frame loop
        carries the surface there, so a new word never snaps the card.
        """
        target = self._target_size
        if not target[0] or not target[1]:
            return
        current = self._size
        if current == target:
            return
        if not self._size_ready:
            # The first size is the real one: there is nothing to ease from.
            self._size_ready = True
            self._size = target
        else:
            factor = 1.0 - math.exp(-dt / 0.06)
            self._size = (
                int(round(current[0] + (target[0] - current[0]) * factor)),
                int(round(current[1] + (target[1] - current[1]) * factor)),
            )
        width, height = self._size
        area = self._area
        if area is not None:
            area.set_content_width(width)
            area.set_content_height(height)
        win = self._win
        if win is not None:
            win.set_default_size(width, height)

    def _start_timer(self) -> None:
        if not self._timer:
            self._last_tick = time.monotonic()
            self._timer = self._GLib.timeout_add(FRAME_MS, self._tick)

    # ------------------------------------------------------------------ state
    def _set_state(self, state: HudState, label: str = "", instant: bool = False) -> None:
        """Switch the card to ``state`` (runs on the GTK thread).

        An ``instant`` switch does not crossfade: a card that comes back
        from the hidden state is fading in anyway, and a crossfade would
        show the idle label next to the new one for a moment.
        """
        if state is not self._state:
            self._state = state
        self._label.set(label or hud_label(state), instant=instant)
        self._tint.set(hud_color(self.palette, state))

    def _apply(self, mode: str, text: str | None = None, language: str = "") -> bool:
        """Switch the overlay to ``mode`` (runs on the GTK thread)."""
        win = self._win
        if win is None:
            return False  # the overlay was closed
        with self._lock:
            previous_mode = self._mode
            self._mode = mode

        # A card that was hidden comes back with its new label right away:
        # the fade-in is enough of a transition.
        instant = previous_mode == "hidden"

        if mode == "listening":
            self._set_state(HudState.LISTENING, instant=instant)
            self._language = ""
            self._source = ""
            self._wave.reset()
            # A new recording starts from an empty card.
            self._animator.clear()
            self._live_text = ""
            self._ticker.reset()
        elif mode == "processing":
            # The partial stays on the card: it is what was heard so far.
            self._set_state(HudState.TRANSCRIBING, instant=instant)
        elif mode == "translating":
            # The source rolls into the box; the translation rolls in over it.
            self._set_state(HudState.TRANSLATING, instant=instant)
            self._source = text or ""
            self._ticker.set("", self._source, instant=instant)
        elif mode == "success":
            self._set_state(HudState.TRANSLATED if language else HudState.DONE, instant=instant)
            self._language = language
            self._ticker.set(text or "", self._source, instant=instant)
        elif mode == "error":
            self._set_state(HudState.ERROR, instant=instant)
            self._ticker.set(text or "", "", instant=instant)
        elif mode == "warning":
            self._set_state(HudState.NOTICE, instant=instant)
            self._ticker.set(text or "", "", instant=instant)
        else:  # hidden
            self._set_state(HudState.READY)
            self._animator.clear()
            self._live_text = ""
            self._ticker.reset()

        if mode == "hidden":
            # the window is only taken down once the fade has settled
            self._fade.set_target(0.0)
        else:
            self._fade.set_target(1.0)
            if not win.get_visible():
                win.present()
                self._make_click_through()
            if mode == "success":
                self._schedule_success_hide(bool(language))

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

    def _schedule_success_hide(self, translated: bool = False) -> None:
        if self._success_timer:
            self._GLib.source_remove(self._success_timer)
        delay = TRANSLATION_HIDE_MS if translated else SUCCESS_HIDE_MS
        self._success_timer = self._GLib.timeout_add(delay, self._success_hide)

    def _success_hide(self) -> bool:
        self._success_timer = 0
        self._apply("hidden")
        return False

    # ------------------------------------------------------------------ painting
    def _draw(self, area, cr, width, height) -> None:
        alpha = self._fade.value
        # Everything is painted into a group so the show/hide fade applies to
        # the card and the text alike.
        cr.push_group()
        try:
            self._draw_hud(cr, width, height)
        finally:
            cr.pop_group_to_source()
        cr.paint_with_alpha(alpha)

    def _draw_hud(self, cr, width: int, height: int) -> None:
        """Paint the card: its frame, its status row, its text box and its bars."""
        content = self._hud_content()

        # The card has a fixed size: only the status row is measured, because
        # only the status row decides where the language chip sits.  The
        # texts are wrapped inside the card instead of deciding how wide it
        # is — that is what keeps the card from resizing while the user
        # talks.
        measures = self._measure_hud(cr, content)
        frame = hud_layout(
            content,
            measures,
            self._hud,
            width=self._card_width,
            height=self._card_height,
            line_height=self._measure_line_height(cr),
        )

        margin = self._hud.surface_margin
        draw_card(cr, margin, margin, frame.width, frame.height, self._hud.radius, self.palette, 1.0)

        self._draw_status(cr, margin, frame)
        self._draw_texts(cr, margin, frame)
        self._draw_wave(cr, margin, frame)

        # The surface follows the card: the size the card needs is handed to
        # the frame loop, which eases the window towards it.
        self._target_size = (
            int(round(frame.width + margin * 2)),
            int(round(frame.height + margin * 2)),
        )

    def _hud_content(self) -> HudContent:
        """What the card shows right now."""
        primary, secondary = self._ticker.current
        return HudContent(
            state=self._state,
            primary=primary,
            secondary=secondary,
            language=self._language,
            label=str(self._label.current or ""),
        )

    def _live_word_alphas(self) -> tuple[float, ...]:
        """The opacity of every word of the live preview.

        The words are painted by Pango as one paragraph — so they wrap and
        reorder the way the language wants — and each of them gets its own
        opacity through a foreground attribute.
        """
        return self._word_alphas

    def _measure_hud(self, cr, content: HudContent) -> HudMeasures:
        """Measure the status row (Pango is the only one who knows the font).

        Only the status row is measured: the card has a fixed width, so the
        texts are wrapped inside it instead of deciding how wide it is.
        """
        measures = HudMeasures()
        if content.label:
            layout = self._status_layout(cr, content.label)
            measures.status_width = self._layout_width(layout)
            measures.status_height = self._layout_height(layout)
        if content.language:
            layout = self._status_layout(cr, content.language)
            measures.language_width = self._layout_width(layout)
        return measures

    def _measure_line_height(self, cr) -> float:
        """The height of one line of the card's main text.

        It is measured once: the font does not change while the overlay is
        alive, and the card only needs it to know how much room its text box
        has to keep.
        """
        cached = getattr(self, "_line_height", 0.0)
        if cached:
            return cached
        try:
            height = self._layout_height(self._layout(cr, "Ag", self._font_size))
        except Exception:  # noqa: BLE001 - the measurement is a nicety
            height = 0.0
        self._line_height = height
        return height

    def _draw_status(self, cr, margin: float, frame) -> None:
        """Draw the status dot, its label and the language chip."""
        color = self._tint.value()
        old_alpha, new_alpha = self._label.alphas()
        if new_alpha > 0.01:
            cr.set_source_rgba(color[0], color[1], color[2], 0.95 * new_alpha)
            cr.arc(
                margin + frame.dot_center[0],
                margin + frame.dot_center[1],
                self._hud.dot_radius,
                0,
                2 * math.pi,
            )
            cr.fill()

        previous = self._label.previous
        current = self._label.current
        if previous and old_alpha > 0.01:
            layout = self._status_layout(cr, str(previous))
            self._show(cr, layout, margin + frame.label_x, margin + frame.label_y, color, 0.8 * old_alpha)
        if current and new_alpha > 0.01:
            layout = self._status_layout(cr, str(current))
            self._show(cr, layout, margin + frame.label_x, margin + frame.label_y, color, 0.9 * new_alpha)

        self._draw_language(cr, margin, frame)

    def _draw_language(self, cr, margin: float, frame) -> None:
        """Draw the chip that names the language of the translation.

        The chip is a recess in the card — the card's own colour, pushed
        towards black — with the name of the language in the quiet text
        colour.  It is the smallest thing on the card on purpose: the
        translation is the news, the language is only a label.
        """
        language = self._language
        if not language:
            return
        chip_x, chip_y, chip_width, chip_height = frame.language
        if chip_width <= 0.0:
            return
        rounded_rect(
            cr,
            margin + chip_x,
            margin + chip_y,
            chip_width,
            chip_height,
            chip_height / 2.0,
        )
        cr.set_source_rgba(*inner_surface(self.palette))
        cr.fill()

        layout = self._status_layout(cr, language)
        text_x = margin + chip_x + self._hud.language_padding
        text_y = margin + chip_y + self._hud.language_padding_y
        self._show(cr, layout, text_x, text_y, self.palette.text_dim, 0.95)

    def _draw_texts(self, cr, margin: float, frame) -> None:
        """Draw the card's text box: the text that rolls through it.

        The box is a window of a fixed size.  What does not fit in it is
        clipped away, and the box scrolls to the newest line — so a sentence
        that grows rolls instead of pushing the card around.  A text that is
        replaced does not cross-fade: the old one rolls out of the box and
        the new one rolls in.
        """
        box_x, box_y, box_w, box_h = frame.text_box
        if box_w <= 0.0 or box_h <= 0.0:
            return
        ticker = self._ticker
        roll = ticker.progress
        # One roll carries the text a whole box: the old text leaves the
        # window upwards while the new one comes in from below it.
        ticker.set_roll(box_h)

        cr.save()
        cr.rectangle(margin + box_x, margin + box_y, box_w, box_h)
        cr.clip()

        previous = ticker.previous
        if previous is not None and roll < 1.0:
            y = margin + box_y - ticker.roll * roll - ticker.previous_scroll
            self._draw_document(cr, margin, box_x, y, previous, 1.0 - roll)

        y = margin + box_y - ticker.scroll + ticker.roll * (1.0 - roll)
        height = self._draw_document(cr, margin, box_x, y, ticker.current, roll)
        cr.restore()

        # How much of the document does not fit into the box: the box scrolls
        # by that much, so the newest line is the one that stays in sight.
        ticker.set_overflow(height - box_h)

    def _draw_document(
        self,
        cr,
        margin: float,
        box_x: float,
        y: float,
        document: tuple[str, str],
        alpha: float,
    ) -> float:
        """Draw one document: the main text and the one below it.

        Returns the height the document takes.
        """
        primary, secondary = document
        height = 0.0
        if primary:
            layout = self._wrapped_layout(cr, primary, self._font_size)
            self._apply_word_alphas(layout, primary, self._live_word_alphas(), alpha)
            self._show(cr, layout, margin + box_x, y, self.palette.text, alpha)
            height = self._layout_height(layout)
        if secondary:
            # The source sits below the main text — but when it is the only
            # text on the card it starts at the top of the box, the way the
            # main text always does.
            gap = self._hud.text_gap if primary else 0.0
            layout = self._wrapped_layout(cr, secondary, self._secondary_font_size)
            self._show(
                cr,
                layout,
                margin + box_x,
                y + height + gap,
                self.palette.text_dim,
                alpha,
            )
            height += gap + self._layout_height(layout)
        return height

    def _draw_wave(self, cr, margin: float, frame) -> None:
        """Draw the waveform."""
        if not frame.has_wave:
            return
        color = self.palette.accent
        draw_waveform(
            cr,
            (margin + frame.wave[0], margin + frame.wave[1], frame.wave[2], frame.wave[3]),
            self._wave.heights,
            color,
            self._hud,
        )

    # ------------------------------------------------------------------ text
    def _layout(self, cr, text: str, size: float):
        """A Pango layout for ``text`` at ``size`` points.

        The whole card is drawn with one font — the configured
        ``overlay.font`` — so the label, the messages and the live preview
        all share the same face.
        """
        layout = self._PangoCairo.create_layout(cr)
        layout.set_font_description(self._font_description(size))
        layout.set_text(text, -1)
        return layout

    def _status_layout(self, cr, text: str):
        """A layout for the status label: a little bolder, a little wider.

        The label is set in small caps with open letter spacing, which is
        what makes it read as a label and not as a headline.
        """
        layout = self._layout(cr, text, self._status_font_size)
        layout.set_font_description(self._font_description(self._status_font_size, medium=True))
        make = getattr(self._Pango, "attr_letter_spacing_new", None)
        if make is not None:
            try:
                attributes = self._Pango.AttrList.new()
                attributes.insert(make(int(LETTER_SPACING * 1024)))
                layout.set_attributes(attributes)
            except Exception:  # noqa: BLE001 - the spacing is a nicety
                log.debug("the letter spacing could not be set", exc_info=True)
        return layout

    def _wrapped_layout(self, cr, text: str, size: float):
        """A layout that wraps inside the card.

        The layout is *not* limited to a number of lines and it does not
        ellipsize: the card's text box is a window that the text rolls
        through, so the text is free to be longer than the box.
        """
        layout = self._layout(cr, text, size)
        layout.set_width(int(self._available_width() * self._Pango.SCALE))
        layout.set_wrap(self._Pango.WrapMode.WORD_CHAR)
        if hasattr(layout, "set_auto_dir"):
            # The paragraph's direction follows its own text.
            layout.set_auto_dir(True)
        layout.set_alignment(self._alignment(text))
        return layout

    def _available_width(self) -> float:
        """The width the card's texts may use.

        The card's own width decides it — not the maximum — so a right-to-left
        text ends at the right edge of the card it is actually drawn in, and
        every text is wrapped inside the same fixed box.
        """
        width = getattr(self, "_card_width", 0.0)
        if width <= 0.0:
            width = 360.0
        return max(40.0, width - self._hud.padding_x * 2.0)

    def _alignment(self, text: str):
        """The alignment of a text.

        Pango's LEFT means the *start* of the paragraph when the layout's
        direction is automatic, and the direction follows the text: a Persian
        paragraph starts at its right edge, an English one at its left.  One
        answer therefore covers both directions — with RIGHT the text would
        hug the wrong edge of the card.
        """
        return self._Pango.Alignment.LEFT

    def _apply_word_alphas(self, layout, text: str, alphas: tuple[float, ...], alpha: float = 1.0) -> None:
        """Fade the words of the live preview in one by one.

        The words are laid out by Pango as one paragraph — so they wrap and
        reorder the way the language wants — and each of them gets its own
        opacity through a foreground attribute.  The opacity of the document
        (the roll that carries the text in or out) multiplies into it, so a
        word that is rolling away fades with the rest of the text.
        """
        if len(alphas) < 2:
            return
        words = text.split()
        if len(words) != len(alphas):
            return
        try:
            attributes = self._Pango.AttrList.new()
            offset = 0
            for word, word_alpha in zip(words, alphas, strict=True):
                start = offset
                offset += len(word.encode("utf-8"))
                end = offset
                offset += 1  # the space between the words
                faded = clamp(word_alpha, 0.0, 1.0) * clamp(alpha, 0.0, 1.0)
                if faded >= 0.995:
                    continue
                rgba = self._Gdk.RGBA()
                color = self.palette.text
                rgba.red = color[0]
                rgba.green = color[1]
                rgba.blue = color[2]
                rgba.alpha = faded
                attribute = self._Pango.attr_foreground_new(rgba)
                attribute.set_start_index(start)
                attribute.set_end_index(end)
                attributes.insert(attribute)
            layout.set_attributes(attributes)
        except Exception:  # noqa: BLE001 - the fade is a nicety
            log.debug("the word fade could not be applied", exc_info=True)

    def _show(self, cr, layout, x: float, y: float, color: tuple, alpha: float) -> None:
        """Draw one Pango layout with its own opacity."""
        if alpha <= 0.01:
            return
        cr.set_source_rgba(color[0], color[1], color[2], alpha)
        cr.move_to(x, y)
        self._PangoCairo.show_layout(cr, layout)

    def _layout_width(self, layout) -> float:
        """The logical width of a Pango layout, in pixels."""
        return layout.get_extents()[1].width / float(self._Pango.SCALE)

    def _layout_height(self, layout) -> float:
        """The logical height of a Pango layout, in pixels."""
        return layout.get_extents()[1].height / float(self._Pango.SCALE)

    # ------------------------------------------------------------------ thread-safe API
    def _post(self, fn, *args) -> None:
        if self._GLib is None:
            return
        self._GLib.idle_add(fn, *args)

    def show_listening(self) -> None:
        self._post(self._apply, "listening")

    def set_partial(self, text: str) -> None:
        self._post(self._set_partial, text)

    def _set_partial(self, text: str) -> bool:
        # ``TranscriptAnimator`` handles character-level corrections without
        # restarting the animation, so the partial is handed over as it is.
        # The animator owns the word window and the idle timeout; the frame
        # loop copies what it says onto the card.
        self._animator.set_text(text)
        self._area.queue_draw()
        return False

    def set_audio_level(self, level: float, samples=None) -> None:
        """Feed a new audio level (and optionally a sample buffer).

        The ``Overlay`` protocol only carries the scalar level; the optional
        ``samples`` argument is an extension kept for compatibility.
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
        self._post(self._apply, "processing")

    def show_translating(self, text: str) -> None:
        """Show the source text while the translation is being made."""
        self._post(self._apply, "translating", text)

    def show_error(self, message: str) -> None:
        self._post(self._apply, "error", message)

    def show_warning(self, message: str) -> None:
        self._post(self._apply, "warning", message)

    def show_success(self, text: str = "", language: str = "") -> None:
        """Show the finished state; the overlay hides itself after a while."""
        self._post(self._apply, "success", text, language)

    def hide(self) -> None:
        self._post(self._apply, "hidden")

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
    #: The live preview's settings.
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
        theme=str(getattr(cfg, "theme", "nocturne") or "nocturne"),
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


def install_transparency_css(Gtk, Gdk) -> None:
    """Make the overlay window's surface transparent (once per process).

    A themed desktop's ``~/.config/gtk-4.0/gtk.css`` paints an opaque
    window colour at ``STYLE_PROVIDER_PRIORITY_USER``.  The overlay's
    transparency rule has to outrank it, or the theme's colour is painted as
    an opaque slab behind the card (hyprwhspr issue #248).
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
    size, or the system's own font — scaled into the band the card was
    designed for.
    """
    settings = resolve_settings(cfg)
    family, size = settings.font_family, settings.font_size
    if not family or size <= 0.0:
        system_family, system_size = system_font()
        family = family or system_family
        size = hud_font_sizes(size, system_size)[0]
    return (family, size)
