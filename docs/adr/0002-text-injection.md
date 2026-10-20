# ADR-0002: تزریق متن روی KDE Plasma Wayland

**وضعیت:** پذیرفته‌شده بر اساس تحلیل مرجع؛ **نیازمند تأیید روی ماشین هدف** با `spikes/inject_matrix.sh` (T-0.3)

## تصمیم
ترتیب پیش‌فرض (`backend = "auto"`) روی Wayland:

1. **`paste`**: `wl-copy` + شبیه‌سازی `Ctrl+V` با `ydotool key` (`Ctrl+Shift+V` برای ترمینال‌ها مثل Konsole)، سپس بازیابی کلیپ‌بورد قبلی.
2. `wtype` (اگر کامپوزیتور `virtual-keyboard` را پشتیبانی کند).
3. `ydotool type`.

روی X11: `xdotool type --clearmodifiers` (یا paste با `xclip`).

## دلیل
- KWin پروتکل `zwp_virtual_keyboard_v1` را پیاده نکرده؛ `wtype` روی Plasma احتمالاً کار نمی‌کند.
- تایپ مستقیم متن فارسی/اختلاط RTL+LTR با `ydotool type` به چیدمان کیبورد وابسته و شکننده است؛ paste متن را بی‌کم‌وکاست منتقل می‌کند.
- hyprwhspr هم همین مسیر را پیش‌فرض گرفته.

## ریسک
`ydotool` نیاز به `ydotoold` و دسترسی `/dev/uinput` دارد. `voicetype doctor` این را بررسی می‌کند.
