<div dir="rtl" align="right">

<div align="center">

# 🎙️ VoiceType

**تایپ با صدا برای لینوکس — فارسی (و اصطلاحات انگلیسی) بگویید، متن سر مکان‌نما تایپ می‌شود.**

[English README](README.md)

</div>

یک میان‌بر بزنید، حرف بزنید، و متن در هر برنامه‌ای که باز است (مرورگر، تلگرام، ترمینال، IDE، LibreOffice) نوشته می‌شود.

## ✨ امکانات

- دیکته‌ی **فارسی‌محور** با نیم‌فاصله‌ی درست، علائم نگارشی و ارقام فارسی
- **فارسی + انگلیسی مخلوط:** «پایتون» ← `Python`، «گیت‌هاب» ← `GitHub` (۴۰+ اصطلاح، قابل‌توسعه)
- کار در **هر برنامه‌ای** با paste در کلیپ‌بورد؛ کلیپ‌بورد قبلی‌تان برگردانده می‌شود
- **پنجره‌ی شناور** (GTK4): سطح صدا، وضعیت و متن لحظه‌ای؛ فوکوس نمی‌گیرد
- چند موتور تشخیص گفتار: Google (رایگان)، Vosk (آفلاین)، Whisper (آفلاین و دقیق‌تر)، Google Cloud؛ با جایگزینی خودکار هنگام خطا
- توقف هوشمند با تشخیص سکوت تطبیقی
- امن: سوکت کنترل با مجوز `0600`، متن هرگز اجرا نمی‌شود
- `voicetype doctor` مشکلات محیط را با دستور نصب نشان می‌دهد

> ⚠️ **نسخه‌ی ۰٫۱:** هسته با ۳۵۰ تست خودکار بررسی شده، اما بخش‌هایی که به دسکتاپ واقعی نیاز دارند (پنجره، میان‌بر، paste، میکروفون) باید روی سیستم خودتان تأیید شوند. اول `voicetype doctor` را اجرا کنید.

## 🚀 نصب (Arch Linux + KDE Plasma)

```bash
git clone https://github.com/<your-username>/voicetype.git
cd voicetype

./scripts/setup-arch.sh            # نصب بسته‌ها + افزودن به گروه‌های input و audio
#   ← یک بار خارج و دوباره وارد شوید

pip install --user -e .            # یا ساخت بسته: cd packaging && makepkg -si
voicetype config init              # ساخت ~/.config/voicetype/config.toml
./scripts/install-hotkey.sh Meta+Alt+V

systemctl --user enable --now ydotool voicetype
voicetype doctor                   # همه‌ی موارد الزامی باید [✓] باشند
```

داخل یک فیلد متن کلیک کنید، **Super + Alt + V** را بزنید، حرف بزنید و دوباره بزنید (یا ۳ ثانیه سکوت کنید).

### افزونه‌های اختیاری

```bash
pip install --user -e ".[vosk]"       # تشخیص آفلاین
pip install --user -e ".[whisper]"    # آفلاین و دقیق‌تر
voicetype models install fa-small     # مدل فارسی Vosk (حدود ۵۳ مگابایت)
```

## 🎮 استفاده

| کار | روش |
|---|---|
| شروع / پایان دیکته | **Super + Alt + V** یا `voicetype toggle` |
| لغو بدون تایپ | `voicetype cancel` |
| وضعیت دیمون | `voicetype status` |
| عیب‌یابی | `voicetype doctor` |
| اعمال تغییر تنظیمات | `voicetype reload` |
| پروفایل‌ها | `voicetype profile code` / `voicetype profile next` |

## ⚙️ تنظیمات

همه‌چیز در `~/.config/voicetype/config.toml` است و **همه‌ی کلیدها اختیاری‌اند**. با `voicetype config init` فایل کامنت‌دار پیش‌فرض را بسازید و با `voicetype config check` بررسی کنید.

```toml
[stt]
provider = "google_free"      # google_free | vosk | whisper | google_cloud
silence_threshold_seconds = 3.0

[postprocess.custom_replacements]
"می شود" = "می‌شود"
```

## 🔒 حریم خصوصی

`google_free` (پیش‌فرض) **صدا را به سرور Google می‌فرستد.** برای کار کاملاً آفلاین `provider = "vosk"` یا `"whisper"` را بگذارید. صدا به‌صورت پیش‌فرض ذخیره نمی‌شود و متن هرگز در لاگ نمی‌آید. جزئیات: [docs/privacy.md](docs/privacy.md)

## 🩺 رفع مشکل

| مشکل | راه‌حل |
|---|---|
| `daemon is not running` | `systemctl --user start voicetype` و لاگ: `journalctl --user -u voicetype -f` |
| میان‌بر کار نمی‌کند | `voicetype toggle` را در ترمینال امتحان کنید؛ یا از تنظیمات سیستم دستور `voicetype-toggle` را به کلید ببندید |
| متن تایپ نمی‌شود | `systemctl --user enable --now ydotool` و بخش *text injection backend* در `doctor` |
| پنجره دیده نمی‌شود | `gtk4-layer-shell` و `python-gobject` را نصب کنید (GNOME از layer-shell پشتیبانی نمی‌کند) |

بیشتر: [docs/troubleshooting.md](docs/troubleshooting.md)

## 🛠️ توسعه

```bash
pip install -e ".[dev]"
ruff check .
pytest        # ۳۵۰ تست؛ بدون میکروفون، شبکه یا GUI
```

## 📄 مجوز

[MIT](LICENSE)

</div>
