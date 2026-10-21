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
- **ترجمه‌ی اختیاری:** هر متن دیکته‌شده را می‌توانید به انگلیسی (یا هر زبان دیگری) ترجمه کنید؛ به‌صورت پیش‌فرض خاموش است
- کار در **هر برنامه‌ای** با paste در کلیپ‌بورد؛ کلیپ‌بورد قبلی‌تان برگردانده می‌شود
- **پنجره‌ی شناور** (GTK4 + layer-shell): فوکوس نمی‌گیرد و کلیک از آن عبور می‌کند. سه سبک (`pill`، `waveform`، `vu_meter`)، حالت‌های انیمیشنی (ضبط، مکث، پردازش، خطا، موفقیت) و پیش‌نمایش زنده‌ی متن کلمه‌به‌کلمه؛ با پالت آماده یا فایل CSS خودتان ([تم‌ها](docs/theming.md))
- چند موتور تشخیص گفتار: Google (رایگان)، Vosk (آفلاین)، Whisper (آفلاین و دقیق‌تر)، Google Cloud؛ با جایگزینی خودکار هنگام خطا
- توقف هوشمند با تشخیص سکوت تطبیقی
- امن: سوکت کنترل با مجوز `0600`، متن هرگز اجرا نمی‌شود
- `voicetype doctor` مشکلات محیط را با دستور نصب نشان می‌دهد

> ⚠️ **نسخه‌ی ۰٫۱:** هسته با ۶۷۸ تست خودکار بررسی شده، اما بخش‌هایی که به دسکتاپ واقعی نیاز دارند (پنجره، میان‌بر، paste، میکروفون) باید روی سیستم خودتان تأیید شوند. اول `voicetype doctor` را اجرا کنید.

## 🚀 نصب (هر توزیع لینوکسی)

```bash
git clone https://github.com/isalehgholami/voicetype.git
cd voicetype
chmod +x scripts/*.sh             # احتیاط: دانلود به‌صورت zip/tar ممکن است مجوز اجرا را حذف کند

./scripts/install-user.sh --system-deps   # بسته‌های سیستم + نصب با pipx + سرویس systemd + تنظیمات + میان‌بر Super+Alt+V
#   ← یک بار خارج و دوباره وارد شوید تا گروه‌های input و audio اعمال شوند

voicetype doctor                   # همه‌ی موارد الزامی باید [✓] باشند
```

اسکریپت **توزیع را تشخیص می‌دهد** (Arch، دبیان/اوبونتو، فدورا، openSUSE، آلپاین و Void): هرچه کم است را
پیدا می‌کند و — با `--system-deps` — فقط همان را با `sudo` نصب می‌کند. بدون این گزینه اصلاً root لازم نیست
و به‌جای نصب، دستور دقیق نصب را چاپ می‌کند.

بعد از آن، اسکریپت VoiceType را در یک venv پیکس نصب می‌کند (بدون root و بدون خطای PEP 668)، یونیت
systemd کاربری را می‌نویسد و اجرا می‌کند، `~/.config/voicetype/config.toml` را همراه با یک راهنمای کوتاه
می‌سازد، میان‌برِ دسکتاپِ در حال اجرا را ثبت می‌کند و در پایان همه‌چیز را بررسی می‌کند — هر مرحله گزارش خودش را چاپ می‌کند.

> روی Arch دستور `pip install --user` به‌خاطر PEP 668 خطا می‌دهد، برای همین از **pipx** استفاده شده. بسته‌ی سیستمی می‌خواهید؟ `cd packaging && makepkg -si` (پکیج‌های `python-sounddevice` و `python-speechrecognition` در AUR هستند؛ با `yay`/`paru` نصب کنید).

داخل یک فیلد متن کلیک کنید، **Super + Alt + V** را بزنید، حرف بزنید و دوباره بزنید (یا ۳ ثانیه سکوت کنید).

اسکریپت نصب idempotent است — **بعد از `git pull` دوباره اجرایش کنید**:

```bash
git pull && ./scripts/install-user.sh     # به‌روزرسانی کد و وابستگی‌ها + ترمیم نصب خراب
```

کد نصب‌شده و وابستگی‌هایش را به‌روز می‌کند، اگر venv خراب باشد از نو می‌سازد، یونیت systemd را دوباره می‌نویسد، دیمون را ری‌استارت می‌کند و مطمئن می‌شود `~/.config/voicetype/config.toml` وجود دارد (فایل خراب با نام `config.toml.bak-<timestamp>` پشتیبان می‌شود و تنظیمات پیش‌فرض جایگزین آن می‌شود — تنظیمات شما پاک نمی‌شود).

### گزینه‌ها

```bash
./scripts/install-user.sh socks,vosk,whisper,hotkey,translate   # هرکدام را بخواهید: socks · vosk · whisper · hotkey · translate
./scripts/install-user.sh --system-deps         # نصب بسته‌های سیستمیِ جاافتاده (sudo)
./scripts/install-user.sh --reinstall                 # ساخت دوباره‌ی venv پیکس
./scripts/install-user.sh --reset-extras socks        # نصب دقیقاً همین افزونه‌ها
./scripts/install-user.sh --no-hotkey                 # دست‌نزدن به میان‌بر
./scripts/install-user.sh --no-service                # دست‌نزدن به یونیت systemd
./scripts/install-user.sh --no-mic-check              # رد کردن تست میکروفون
./scripts/install-user.sh --hotkey Ctrl+Alt+Space # میان‌بر دلخواه
./scripts/install-user.sh --hotkey-only          # فقط ثبت میان‌بر، بدون نصب
./scripts/install-user.sh --desktop hyprland     # ثبت میان‌بر برای یک دسکتاپِ مشخص
./scripts/install-user.sh -v                          # نمایش کامل خروجی دستورها
./scripts/install-user.sh --help
voicetype models install fa-small     # مدل فارسی Vosk (حدود ۵۳ مگابایت)
```

افزونه‌های نصب‌شده در اجرای قبلی حفظ می‌شوند؛ پس اجرای دوباره‌ی اسکریپت هیچ قابلیتی را بی‌سروصدا حذف نمی‌کند.

### دسکتاپ‌های دیگر

ثبت خودکار میان‌بر روی **KDE Plasma**، **GNOME**، **Hyprland**، **Sway** و **i3** انجام می‌شود: به‌ترتیب در
`kglobalshortcutsrc`، به‌صورت custom keybinding، با یک خط `bind` و با یک خط `bindsym` نوشته می‌شود.
روی دسکتاپی که اسکریپت نشناسد، دقیقاً می‌گوید چه خطی را کجا bind کنید — یا خودتان `voicetype-toggle` را bind کنید.
اگر ترجیح می‌دهید دستی نصب کنید: `pip install -e .` و سپس `voicetype config init`.

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

[overlay]
position = "bottom"           # top | center | bottom
style = "pill"                # pill | waveform | vu_meter
theme = "tokyo-night"         # نام پالت، "dark"/"light" یا مسیر یک فایل .css
accent = ""                   # مثلاً "#ff8800" — جایگزینی رنگ accent پالت
opacity = 0.86                # کدریِ کارتی که پنجره روی آن کشیده می‌شود (۰٫۰ تا ۱٫۰)
animations = true             # کلید اصلی حرکت: false یعنی بی‌درنگ و بدون تپش
show_audio_meter = true       # false یعنی تصویرسازی میکروفون را نادیده می‌گیرد
show_transcript = true        # پیش‌نمایش زنده‌ی متن کلمه‌به‌کلمه
transcript_words = 4          # چند کلمه روی صفحه بماند (۱ تا ۱۲)
font = ""                          # خالی = قلمِ خودِ سیستم؛ مثلاً "JetBrains Mono"
font_size = 0.0                    # صفر = اندازه‌ی خودِ سیستم (ضرب در ۱٫۶)؛ در غیر این صورت بر حسب پوینت
fade_ms = 140                 # مدت انیمیشن ظاهر/محو شدن
sounds = false                # بوق صوتی هنگام شروع، پایان یا خطای ضبط

[postprocess.custom_replacements]
"می شود" = "می‌شود"

[translate]                   # اختیاری — به‌صورت پیش‌فرض خاموش
enabled = false
language = "en"               # "en" یا "English" یا "de" یا "zh-CN"
```

برای پالت‌ها و تم‌های سفارشی: [docs/theming.md](docs/theming.md). سبکِ پیش‌فرضِ `pill` رنگ‌های ثابتی می‌کشد، پس پالت فقط در سبک‌های `waveform` و `vu_meter` دیده می‌شود.

همه‌ی تنظیمات با `voicetype reload` اعمال می‌شود: پنجره‌ی overlay دوباره ساخته می‌شود (فقط اگر تنظیماتِ خودش عوض شده باشد)، میان‌برِ حالت hold دوباره راه‌اندازی می‌شود و بقیه‌ی تنظیمات ( STT ، تزریق متن، پس‌پردازش، ترجمه، سطحِ لاگ ) بلافاصله به‌کار می‌روند. اگر ضبطی در جریان باشد، تنظیماتِ تازه در پایانِ همان چرخه اعمال می‌شود. تنها استثنا فایلِ تم است: اگر `overlay.theme` به یک فایل `.css` اشاره کند، ویرایشِ همان فایل بی‌درنگ روی پنجره اعمال می‌شود.

## 🌍 ترجمه‌ی گفته‌ها (اختیاری)

فارسی حرف بزنید، متن انگلیسی (یا هر زبان دیگری) تایپ شود. این قابلیت **به‌صورت پیش‌فرض خاموش** است:

```bash
./scripts/install-user.sh translate        # نصب py-googletrans
# (بسته‌ی googletrans همیشه نصب می‌شود — دیمون برای بررسیِ تنظیمات به آن نیاز دارد)
```

```toml
[translate]
enabled = true
language = "en"      # "en"، "English"، "de"، "zh-CN" و …
source = "auto"      # "auto" یعنی زبان گفته‌شده را خودش تشخیص دهد
```

```bash
voicetype translate "سلام دنیا"    # → Hello world.
```

ابتدا متنِ فارسی اصلاح می‌شود، سپس ترجمه می‌شود و در پایان حرف بزرگ و نقطه‌ی پایانی دوباره اضافه می‌شود. اگر ترجمه ممکن نباشد، متن اصلی بدون تغییر تایپ می‌شود (و یک هشدار در پنجره نشان داده می‌شود) — ترجمه هرگز حرف‌های شما را از بین نمی‌برد. جزئیات: [docs/translation.md](docs/translation.md)

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

`google_free` (پیش‌فرض) **صدا را به سرور Google می‌فرستد.** برای کار کاملاً آفلاین `provider = "vosk"` یا `"whisper"` را بگذارید. صدا به‌صورت پیش‌فرض ذخیره نمی‌شود و متن هرگز در لاگ نمی‌آید. اگر ترجمه را روشن کنید، **متن** دیکته‌شده هم به Google فرستاده می‌شود. جزئیات: [docs/privacy.md](docs/privacy.md)

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
pytest        # ۶۷۸ تست؛ بدون میکروفون، شبکه یا GUI
```

## 📄 مجوز

[MIT](LICENSE)

</div>
