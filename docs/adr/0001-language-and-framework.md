# ADR-0001: زبان و فریم‌ورک

**وضعیت:** پذیرفته‌شده (T-0.8)

## تصمیم
- **Python ≥ 3.11** (`tomllib` استاندارد).
- هسته (config، dispatcher، STT، postprocess، سوکت کنترل) **بدون وابستگی سنگین** و بدون Qt/GTK؛ تا روی هر سیستمی تست‌پذیر باشد.
- UI: **GTK4 + gtk4-layer-shell** (ADR-0003). نه PyQt6.
- ضبط صدا: `sounddevice` (PortAudio → PipeWire/Pulse). import تنبل تا تست‌ها بدون سخت‌افزار اجرا شوند.
- STT: provider پیش‌فرض `google_free` از طریق کتابخانه‌ی `SpeechRecognition` (endpoint فقط FLAC می‌پذیرد و این کتابخانه encoder آن را همراه دارد)؛ Vosk و faster-whisper اختیاری و تنبل-import. timeout/retry/chunking/لغو را خودمان بالای آن پیاده می‌کنیم.
- بسته‌بندی: `pyproject.toml` + PKGBUILD.

## دلیل
سند معماری PyQt6 + یک shim به نام `_layershell` فرض کرده بود که وجود ندارد (بررسی ref-src). GTK4 مسیر اثبات‌شده‌ی hyprwhspr است.
