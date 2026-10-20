"""GTK4 + gtk4-layer-shell overlay (docs/adr/0003). Imports gi lazily; falls back to NullOverlay.

All GTK work happens on one dedicated thread running a GLib main loop; public methods are
thread-safe because they only enqueue work with GLib.idle_add.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .config import OverlayConfig
from .dispatcher import NullOverlay, Overlay
from .log import get_logger

log = get_logger("overlay")

THEMES = {
    "dark": {"bg": (0.059, 0.090, 0.165), "fg": (1, 1, 1), "bar": (0.145, 0.388, 0.922), "dim": (0.75, 0.78, 0.85)},
    "light": {"bg": (0.97, 0.97, 0.98), "fg": (0.1, 0.12, 0.18), "bar": (0.145, 0.388, 0.922), "dim": (0.35, 0.38, 0.45)},
}
ERROR_COLOR = (0.86, 0.21, 0.21)
WARN_COLOR = (0.96, 0.62, 0.04)
FRAME_MS = 16
LEVEL_SMOOTHING = 0.35


@dataclass
class _View:
    mode: str = "hidden"  # hidden | listening | processing | error | warning
    text: str = ""
    level: float = 0.0
    shown_level: float = 0.0


def smooth(current: float, target: float, factor: float = LEVEL_SMOOTHING) -> float:
    """Exponential smoothing for the level meter (pure; unit-tested)."""
    return current + (target - current) * factor


STATUS_TEXT = {"listening": "Listening…", "processing": "Processing…"}


def create_overlay(cfg: OverlayConfig) -> Overlay:
    if not cfg.enabled:
        return NullOverlay()
    try:
        return GtkOverlay(cfg)
    except Exception as e:  # noqa: BLE001 - missing gi / no layer-shell support
        log.warning("GTK overlay unavailable (%s); running without overlay. Run `voicetype doctor`.", e)
        return NullOverlay()


class GtkOverlay:
    def __init__(self, cfg: OverlayConfig) -> None:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell

        if hasattr(Gtk4LayerShell, "is_supported") and not Gtk4LayerShell.is_supported():
            raise RuntimeError("compositor does not support wlr-layer-shell")
        self.cfg = cfg
        self.view = _View()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True, name="overlay-gtk")
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("GTK overlay thread did not start")

    # ------------------------------------------------------------------ GTK thread
    def _run(self) -> None:
        from gi.repository import GLib, Gtk
        from gi.repository import Gtk4LayerShell as LS

        self._GLib = GLib
        Gtk.init()
        win = Gtk.Window()
        win.set_decorated(False)
        win.set_default_size(self.cfg.width, self.cfg.height)
        area = Gtk.DrawingArea()
        area.set_content_width(self.cfg.width)
        area.set_content_height(self.cfg.height)
        area.set_draw_func(self._draw)
        win.set_child(area)

        LS.init_for_window(win)
        LS.set_namespace(win, "voicetype")
        LS.set_layer(win, LS.Layer.OVERLAY)
        LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)  # never takes focus
        LS.set_exclusive_zone(win, -1)
        c = self.cfg
        vert = {"top": LS.Edge.TOP, "bottom": LS.Edge.BOTTOM}.get(c.position)
        horiz = {"left": LS.Edge.LEFT, "right": LS.Edge.RIGHT}.get(c.anchor_horizontal)
        for edge in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
            LS.set_anchor(win, edge, edge in (vert, horiz))
        if vert is not None:
            LS.set_margin(win, vert, abs(c.offset_y))
        if horiz is not None:
            LS.set_margin(win, horiz, abs(c.offset_x))

        self._win, self._area, self._Gtk = win, area, Gtk
        self._timer = 0
        self._loop = GLib.MainLoop()
        self._ready.set()
        self._loop.run()

    def _make_click_through(self) -> None:
        try:
            import cairo

            surface = self._win.get_surface()
            if surface is not None:
                surface.set_input_region(cairo.Region())
        except Exception:  # noqa: BLE001
            log.debug("click-through not applied", exc_info=True)

    def _tick(self) -> bool:
        with self._lock:
            v = self.view
            v.shown_level = smooth(v.shown_level, v.level)
            alive = v.mode != "hidden"
        self._area.queue_draw()
        if not alive:
            self._timer = 0
        return alive

    def _apply(self, mode: str, text: str | None = None) -> bool:
        with self._lock:
            self.view.mode = mode
            if text is not None:
                self.view.text = text
            if mode in ("listening",):
                self.view.level = self.view.shown_level = 0.0
        if mode == "hidden":
            self._win.set_visible(False)
        else:
            if not self._win.get_visible():
                self._win.present()
                self._make_click_through()
            if not self._timer:
                self._timer = self._GLib.timeout_add(FRAME_MS, self._tick)
        self._area.queue_draw()
        return False

    def _draw(self, area, cr, width, height) -> None:
        import gi

        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Pango, PangoCairo

        c = self.cfg
        th = THEMES[c.theme]
        with self._lock:
            mode, text, shown = self.view.mode, self.view.text, self.view.shown_level
        # card
        r = 12
        cr.new_sub_path()
        cr.arc(width - r, r, r, -1.5708, 0)
        cr.arc(width - r, height - r, r, 0, 1.5708)
        cr.arc(r, height - r, r, 1.5708, 3.1416)
        cr.arc(r, r, r, 3.1416, 4.7124)
        cr.close_path()
        accent = ERROR_COLOR if mode == "error" else WARN_COLOR if mode == "warning" else None
        cr.set_source_rgba(*th["bg"], c.opacity)
        cr.fill_preserve()
        if accent:
            cr.set_source_rgba(*accent, 1)
            cr.set_line_width(2)
            cr.stroke()
        else:
            cr.new_path()

        # text (Pango does bidi, so Persian renders RTL automatically)
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription(f"{c.font} 12"))
        layout.set_width((width - 24) * Pango.SCALE)
        layout.set_ellipsize(Pango.EllipsizeMode.START)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        label = STATUS_TEXT.get(mode, "") if mode in STATUS_TEXT else text
        body = text if (mode == "listening" and text and c.show_partial_text) else label
        layout.set_text(body or "", -1)
        cr.set_source_rgba(*(accent or th["fg"]), 1)
        cr.move_to(12, 10)
        PangoCairo.show_layout(cr, layout)

        # level meter
        if c.show_audio_meter and mode == "listening":
            bar_h, margin = 8, 12
            y = height - margin - bar_h
            cr.set_source_rgba(*th["dim"], 0.25)
            cr.rectangle(margin, y, width - 2 * margin, bar_h)
            cr.fill()
            cr.set_source_rgba(*th["bar"], 0.9)
            cr.rectangle(margin, y, (width - 2 * margin) * max(0.0, min(1.0, shown)), bar_h)
            cr.fill()

    # ------------------------------------------------------------------ thread-safe API
    def _post(self, fn, *args) -> None:
        self._GLib.idle_add(fn, *args)

    def show_listening(self) -> None:
        self._post(self._apply, "listening", "")

    def set_partial(self, text: str) -> None:
        with self._lock:
            self.view.text = text
        self._post(self._area.queue_draw)

    def set_audio_level(self, level: float) -> None:
        with self._lock:
            self.view.level = max(0.0, min(1.0, level))

    def show_processing(self) -> None:
        self._post(self._apply, "processing", "")

    def show_error(self, message: str) -> None:
        self._post(self._apply, "error", message)

    def show_warning(self, message: str) -> None:
        self._post(self._apply, "warning", message)

    def hide(self) -> None:
        self._post(self._apply, "hidden", None)
