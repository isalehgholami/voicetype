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

> ⚠️ **نسخه‌ی ۰٫۱:** هسته با ۳۸۵ تست خودکار بررسی شده، اما بخش‌هایی که به دسکتاپ واقعی نیاز دارند (پنجره، میان‌بر، paste، میکروفون) باید روی سیستم خودتان تأیید شوند. اول `voicetype doctor` را اجرا کنید.

## 🚀 نصب (Arch Linux + KDE Plasma)

```bash
git clone https://github.com/isalehgholami/voicetype.git
cd voicetype
chmod +x scripts/*.sh             # احتیاط: دانلود به‌صورت zip/tar ممکن است مجوز اجرا را حذف کند

./scripts/setup-arch.sh            # نصب بسته‌ها + افزودن به گروه‌های input و audio
#   ← یک بار خارج و دوباره وارد شوید

./scripts/install-user.sh          # نصب با pipx + سرویس systemd + تنظیمات + میان‌بر Super+Alt+V
systemctl --user enable --now ydotool   # سرویس ydotoold باید اجرا باشد؛ اگر یونیت کاربری نبود: sudo ydotoold &
voicetype doctor                   # همه‌ی موارد الزامی باید [✓] باشند
```

> روی Arch دستور `pip install --user` به‌خاطر PEP 668 خطا می‌دهد، برای همین از **pipx** استفاده شده. بسته‌ی سیستمی می‌خواهید؟ `cd packaging && makepkg -si` (پکیج‌های `python-sounddevice` و `python-speechrecognition` در AUR هستند؛ با `yay`/`paru` نصب کنید).

داخل یک فیلد متن کلیک کنید، **Super + Alt + V** را بزنید، حرف بزنید و دوباره بزنید (یا ۳ ثانیه سکوت کنید).

اسکریپت نصب idempotent است — **بعد از `git pull` دوباره اجرایش کنید**:

```bash
git pull && ./scripts/install-user.sh     # به‌روزرسانی کد و وابستگی‌ها + ترمیم نصب خراب
```

کد نصب‌شده و وابستگی‌هایش را به‌روز می‌کند، اگر venv خراب باشد از نو می‌سازد، یونیت systemd را دوباره می‌نویسد، دیمون را ری‌استارت می‌کند و مطمئن می‌شود `~/.config/voicetype/config.toml` وجود دارد (فایل خراب با نام `config.toml.bak-<timestamp>` پشتیبان می‌شود و تنظیمات پیش‌فرض جایگزین آن می‌شود — تنظیمات شما پاک نمی‌شود).

### افزونه‌های اختیاری

```bash
./scripts/install-user.sh socks,vosk,whisper,hotkey   # هرکدام را بخواهید: socks · vosk · whisper · hotkey
./scripts/install-user.sh --reinstall                 # ساخت دوباره‌ی venv پیکس
./scripts/install-user.sh --reset-extras socks        # نصب دقیقاً همین افزونه‌ها
./scripts/install-user.sh --no-hotkey                 # دست‌نزدن به میان‌بر KDE
voicetype models install fa-small     # مدل فارسی Vosk (حدود ۵۳ مگابایت)
```

افزونه‌های نصب‌شده در اجرای قبلی حفظ می‌شوند؛ پس اجرای دوباره‌ی اسکریپت هیچ قابلیتی را بی‌سروصدا حذف نمی‌کند.

## 🎮 استفاده

| کار | روش |
|---|---|
| شروع / پایان دیکته | **Super + Alt + V** یا `voicetype toggle` |
| لغو بدون تایپ | `voicetype cancel` |
| وضعیت دیمون | `voicetype status` |
| عیب‌یابی | `voicetype doctor` |
| تست اینترنت / پروکسی | `voicetype net-test` |
| اعمال تغییر تنظیمات | `voicetype reload` |
| پروفایل‌ها | `voicetype profile code` / `voicetype profile next` |

## ⚙️ تنظیمات

همه‌چیز در `~/.config/voicetype/config.toml` است و **همه‌ی کلیدها اختیاری‌اند**. با `voicetype config init` فایل کامنت‌دار پیش‌فرض را بسازید و با `voicetype config check` بررسی کنید. اگر فایل خراب شد، `voicetype config init --repair` (و اسکریپت نصب) آن را با نام `config.toml.bak-<timestamp>` پشتیبان می‌گیرد و پیش‌فرض‌ها را دوباره می‌نویسد.

```toml
[stt]
provider = "google_free"      # google_free | vosk | whisper | google_cloud
silence_threshold_seconds = 3.0

[postprocess.custom_replacements]
"می شود" = "می‌شود"
```

## 🌐 اینترنت قطع یا فیلتر است؟ پروکسی بزنید

همه‌ی ترافیک VoiceType (تشخیص گفتار Google و دانلود مدل‌ها) را از پروکسی HTTP یا SOCKS رد کنید:

```toml
[network]
proxy = "socks5h://127.0.0.1:1080"     # یا  http://127.0.0.1:8080  ·  user:pass@ هم پشتیبانی می‌شود
```

```bash
voicetype reload && voicetype net-test      # OK یا FAIL به‌همراه علت
```

برای SOCKS باید PySocks نصب باشد (`./scripts/install-user.sh socks`). اگر پروکسی کار نکند VoiceType **به‌جای اتصال مستقیم خطا می‌دهد** تا صدای شما ناخواسته مستقیم ارسال نشود. جزئیات: [docs/network.md](docs/network.md)

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
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest        # ۳۸۵ تست؛ بدون میکروفون، شبکه یا GUI
```

## 📄 مجوز

[MIT](LICENSE)

</div>
