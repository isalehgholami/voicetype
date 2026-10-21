# ADR-0004: هات‌کی سراسری

**وضعیت:** پذیرفته‌شده؛ نمونه‌ها: `spikes/hotkey_evdev.py`, بخش میان‌بر در `scripts/install-user.sh` (T-0.5)

## تصمیم
1. **پیش‌فرض:** KDE Custom Shortcut → دستور `voicetype toggle` (سوکت کنترل). بدون دسترسی ویژه، روی هر نسخه‌ی Plasma.
2. **اختیاری:** listener `evdev` برای حالت `hold` (push-to-talk) و کلید `Esc`؛ نیاز به عضویت در گروه `input`.
3. `kglobalaccel` روی D-Bus: **کنار گذاشته شد** — API خصوصی/ناپایدار است و `kwriteconfig` در سند معماری (کلید `_launch`) برای رجیستر شدن کافی نیست.

## دلیل
Wayland گرب سراسری استاندارد ندارد. Custom Shortcut امن‌ترین مسیر است؛ evdev برای رویداد «رها شدن کلید» (hold) و Esc لازم است.
