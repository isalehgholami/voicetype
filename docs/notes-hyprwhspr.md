# یادداشت‌های hyprwhspr (T-0.2)

مرجع: `ref-src/hyprwhspr` (پایتون، سرویس systemd کاربری). قصد کپی‌کردن کد را نداریم؛ فقط الگوها.

## چه چیزی را الگو می‌گیریم

| ماژول مرجع | الگو | در VoiceType |
|---|---|---|
| `lib/src/app/silence.py` | کالیبراسیون noise floor در ~۰٫۶ ثانیه اول (۶ نمونه × ۱۰۰ms)، آستانه = `max(min(samples)*2, 2e-4)`؛ شمارش سکوت فقط **بعد از شنیده‌شدن گفتار** («armed»)؛ poll هر ۱۰۰ms | `voicetype/silence.py` |
| `lib/src/recording_control_server.py` | کنترل بیرونی با FIFO/سوکت در `$XDG_RUNTIME_DIR`، دستورات متنی | `voicetype/control.py` (Unix socket، مجوز 0600) |
| `lib/src/text_injector.py` | پیش‌فرض: کلیپ‌بورد + شبیه‌سازی کلید paste؛ ذخیره/بازیابی کلیپ‌بورد؛ `ctrl+shift+v` برای ترمینال‌ها (konsole و ...)؛ fallback به `wtype` / `ydotool` / `xdotool`؛ keycode قابل‌تنظیم برای چیدمان غیر QWERTY | `voicetype/injector.py` |
| `lib/mic_osd/window.py` | GTK4 + `gtk4-layer-shell`: layer=OVERLAY، anchor، `keyboard_mode=NONE`، `exclusive_zone=-1`، namespace | `voicetype/overlay.py` |
| `config/systemd/hyprwhspr.service` | `PartOf/After=graphical-session.target` (نه `Requires`)، `ExecStartPre` منتظر سوکت Wayland، `Restart=on-failure` | `packaging/voicetype.service` |
| `lib/src/hallucination.py` | فیلتر خروجی‌های مزاحم Whisper | `voicetype/postprocess/hallucination.py` |
| `lib/src/global_shortcuts.py` | هات‌کی با `evdev` (نیاز به گروه `input`) + `UInput` برای re-emit | فقط اختیاری (hold-mode) |

## چه چیزی را نه

- **evdev grab + UInput re-emit** پیش‌فرض نمی‌شود: نیاز به دسترسی `input` دارد و پیچیده است (~۱۴۰۰ خط). پیش‌فرض ما KDE Custom Shortcut است.
- ماژول‌های realtime (ElevenLabs/Gemini/OpenAI)، meeting-recorder، nix، نصب‌کننده‌ی backendها: خارج از محدوده.
- `ydotoold` خصوصی: فقط اگر کاربر `ydotool` را انتخاب کند.

## یافته‌های مهم

1. hyprwhspr روی KDE هم `wtype` را اگر موجود باشد امتحان می‌کند ولی مسیر اصلی «paste با کلید» است؛ تایپ مستقیم متن فارسی/RTL شکننده است.
2. OSD در KDE با GTK4 + layer-shell کار می‌کند (KWin پروتکل `wlr-layer-shell` را پشتیبانی می‌کند).
3. کنترل از بیرون با `pkill -SIGUSR1` (سند معماری) شکننده است؛ سوکت جایگزین شد.
