# ADR-0003: ابزار OSD

**وضعیت:** پذیرفته‌شده؛ نمونه‌ی اجرایی: `spikes/osd_gtk4.py` (T-0.4) — نیازمند تأیید بصری روی Plasma

## تصمیم
**GTK4 + gtk4-layer-shell** (`gi`, `Gtk4LayerShell`).

| معیار | PyQt6 + LayerShellQt | GTK4 + gtk4-layer-shell |
|---|---|---|
| binding پایتون layer-shell | **وجود ندارد** (فقط C++/QML) | موجود (`gir1.2`/`gtk4-layer-shell` در Arch) |
| الگوی اثبات‌شده | — | hyprwhspr `mic_osd/window.py` |
| فوکوس‌نگرفتن | نامطمئن | `keyboard_mode=NONE` |
| click-through | `WA_TransparentForMouseEvents` | `set_input_region` خالی |

پکیج‌های Arch: `gtk4`, `gtk4-layer-shell`, `python-gobject`, `python-cairo`.

OSD در **فرایند جدا** (یا thread GLib جدا) از دیمون اجرا می‌شود؛ دیمون از طریق pipe/سوکت وضعیت و level می‌فرستد. در این نسخه GLib mainloop در ترد جدا با `GLib.idle_add` اجرا می‌شود.

## پیوست (2026-10)

تصمیم ابزار **بدون تغییر پابرجا می‌ماند**: همچنان GTK4 + gtk4-layer-shell (`gi`, `Gtk4LayerShell`)، همان layer و همان `keyboard_mode=NONE`.

گزینه‌ی دومِ متن بالا همان چیزی است که پیاده شده: OSD در **تردِ اختصاصیِ خودِ دیمون** اجرا می‌شود — یک `GLib.MainLoop` روی ترد `overlay-gtk` — و متدهای عمومی فقط با `GLib.idle_add` به آن صف می‌شوند. پس «فرایند جدا» پیاده نشده و pipe/سوکتِ جداگانه میان دیمون و OSD هم لازم نشده است.

آنچه در این پیوست تغییر کرده فقط **زبان بصری** است — پالت‌های نام‌دار، سه سبک تصویرسازی، ماشین‌حالت انیمیشن و پیش‌نمایش زنده‌ی متن، همه در `voicetype/ui/` و `voicetype/overlay.py`. مرجع کامل: [ADR-0007](0007-overlay-visual-language.md).
