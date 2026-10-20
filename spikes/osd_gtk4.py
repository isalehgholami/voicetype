#!/usr/bin/env python3
"""T-0.4 — نمونه‌ی OSD: ۳۶۰×۹۶ بالا-وسط، layer-shell، بدون فوکوس، click-through.

اجرا (Arch): python spikes/osd_gtk4.py   — ۴ ثانیه نمایش و خروج.
"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402


def on_activate(app):
    win = Gtk.Window(application=app)
    win.set_default_size(360, 96)
    win.set_decorated(False)
    win.set_child(Gtk.Label(label="Listening…"))

    LS.init_for_window(win)
    LS.set_namespace(win, "voicetype-spike")
    LS.set_layer(win, LS.Layer.OVERLAY)
    LS.set_anchor(win, LS.Edge.TOP, True)
    LS.set_margin(win, LS.Edge.TOP, 12)
    LS.set_exclusive_zone(win, -1)
    LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)  # فوکوس نمی‌گیرد

    win.present()
    # click-through: ناحیه‌ی ورودی خالی
    surface = win.get_surface()
    if surface is not None:
        import cairo
        surface.set_input_region(cairo.Region())
    GLib.timeout_add_seconds(4, app.quit)


app = Gtk.Application(application_id="ir.voicetype.spike")
app.connect("activate", on_activate)
app.run(None)
