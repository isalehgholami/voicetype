<div dir="rtl" align="right">

<div align="center">

# VoiceType

تایپ با صدا برای لینوکس، با درک فارسی.<br>
یک میان‌بر بزنید، حرف بزنید، و متن همان‌جا که مکان‌نما هست نوشته می‌شود.

[English README](README.md)

</div>

---

VoiceType یک دیمون کوچک است. کلید را می‌زنید، حرف می‌زنید، دوباره کلید را می‌زنید و متن در هر برنامه‌ای که فوکوس دارد (مرورگر، ترمینال، ویرایشگر، تلگرام، LibreOffice) چسبانده می‌شود. اول برای فارسی ساخته شد، انگلیسی هم جواب می‌دهد، و قاطی کردن این دو در یک جمله هم مشکلی ندارد.

## چرا ساختیمش

تایپ کردن در لینوکس در طول روز خسته‌کننده است، و از وقتی با ایجنت‌های کدنویسی کار می‌کنیم بدتر هم شد، چون نصف کار توضیح دادن با پرامپت‌های بلند است. می‌خواستیم فکرمان را بلند بگوییم، فارسی، و سیستم درست بفهمد.

وقتی درباره‌ی کد حرف می‌زنیم کلمه‌های انگلیسی وسط جمله‌ی فارسی می‌آید. برای همین VoiceType خودش «پایتون» را `Python` و «گیت‌هاب» را `GitHub` می‌نویسد. و اگر پرامپت قرار است انگلیسی باشد، یک خط در کانفیگ کافی است تا حرف شما را پیش از تایپ ترجمه کند.

این پروژه را دو نفری با هم نوشتیم.

## چه چیزی می‌گیرید

- فارسی که درست دیده می‌شود. نیم‌فاصله سر جایش است، نشانه‌گذاری فارسی است و ارقام را خودتان انتخاب می‌کنید.
- اصطلاحات انگلیسی همان‌طور که برنامه‌نویس‌ها می‌نویسند. فعلاً ۴۴ اصطلاح در فهرست هست و می‌توانید خودتان اضافه کنید.
- ترجمه‌ی اختیاری به انگلیسی یا زبان دیگر. پیش‌فرض خاموش است. بخش [ترجمه](#ترجمه) را ببینید.
- تایپ در هر برنامه‌ای. متن با paste وارد می‌شود و کلیپ‌بوردِ قبلی‌تان بعدش برمی‌گردد.
- یک کارت کوچک شناور که نشان می‌دهد چه می‌شنود. فوکوس نمی‌گیرد و کلیک از رویش رد می‌شود.
- چند موتور تشخیص گفتار، آنلاین یا کاملاً آفلاین، و جایگزینی خودکار وقتی یکی پشت هم خطا بدهد.
- وقتی ساکت می‌شوید خودش تمام می‌کند و با سروصدای اتاقتان تنظیم می‌شود.
- `voicetype doctor` که می‌گوید چه چیزی کم است و دستور دقیقِ رفعش را می‌دهد.

## نصب

```bash
git clone https://github.com/isalehgholami/voicetype.git
cd voicetype
chmod +x scripts/*.sh              # گاهی دانلود zip/tar مجوز اجرا را حذف می‌کند

./scripts/install-user.sh --system-deps
voicetype doctor                   # همه‌ی موارد الزامی باید [✓] باشند
```

اسکریپت توزیع شما را پیدا می‌کند (Arch، دبیان و اوبونتو، فدورا، openSUSE، آلپاین، Void)، چیزهای کم را نصب می‌کند، VoiceType را در یک محیط `pipx` می‌گذارد، دیمون را راه می‌اندازد و میان‌بر `Super+Alt+V` را روی KDE Plasma، GNOME، Hyprland، Sway یا i3 ثبت می‌کند. بدون `--system-deps` هیچ‌وقت `sudo` نمی‌زند و به‌جایش دستورِ لازم را چاپ می‌کند.

بعد از اولین اجرا یک بار خارج و دوباره وارد شوید تا گروه‌های `input` و `audio` اعمال شوند. برای به‌روزرسانی: `git pull && ./scripts/install-user.sh`.

ما روی Arch با KDE Plasma 6 و Wayland توسعه و تست می‌کنیم. بقیه‌ی محیط‌ها را اسکریپت پوشش می‌دهد، ولی زمان کمتری روی سیستم‌های واقعی امتحان شده‌اند. اگر چیزی خراب بود خبر بدهید.

نصب دستی، بسته‌ی Arch، گزینه‌های اسکریپت و حذف کامل در [docs/installation.md](docs/installation.md) هست (به انگلیسی).

## استفاده

داخل یک فیلد متن کلیک کنید، `Super+Alt+V` را بزنید، حرف بزنید و دوباره بزنید. یا فقط ساکت شوید: بعد از ۳ ثانیه سکوت خودش تمام می‌کند.

| کار | دستور |
|---|---|
| شروع یا پایان دیکته | `Super+Alt+V` یا `voicetype toggle` |
| دور ریختن ضبط | `voicetype cancel` |
| دیدن وضعیت دیمون | `voicetype status` |
| بررسی نصب | `voicetype doctor` |
| اعمال تغییر کانفیگ | `voicetype reload` |
| تست پروکسی | `voicetype net-test` |

با `voicetype --help` همه‌ی دستورها را می‌بینید.

برای کارهای مختلف می‌توانید تنظیمات جدا داشته باشید: یک کانفیگ ناقص در `~/.config/voicetype/profiles/<name>.toml` بگذارید و با `voicetype profile <name>` عوضش کنید. مثلاً پروفایلی برای کدنویسی که نقطه‌گذاری خودکار را خاموش می‌کند:

```toml
# ~/.config/voicetype/profiles/code.toml
[postprocess]
auto_punctuate = false
capitalize_first = false
```

## موتورهای تشخیص گفتار

| موتور | نیاز | آفلاین | توضیح |
|---|---|:-:|---|
| `google_free` (پیش‌فرض) | اینترنت | خیر | endpoint مستندنشده است و صدای شما به Google می‌رود. |
| `vosk` | `vosk` و یک مدل | بله | سبک و خصوصی. روی متن قاطیِ فارسی و انگلیسی دقتش کمتر است. |
| `whisper` | `faster-whisper` | بله | دقیق‌ترین گزینه‌ی آفلاین. روی CPU یا CUDA. |
| `google_cloud` | کلید service account | خیر | پولی. |

موتور را با `provider` زیر `[stt]` انتخاب کنید. برای کار آفلاین:

```bash
./scripts/install-user.sh vosk
voicetype models install fa-small          # حدود ۵۳ مگابایت
```

هنگام حرف زدن متن روی کارت دیده می‌شود. Vosk خودش زنده کار می‌کند. برای بقیه صدا را سر مکث‌ها می‌بریم و هر جمله را وسط حرف زدنِ شما می‌فرستیم، پس وقتی تمام می‌کنید فقط آخرین تکه‌ی کوتاه می‌ماند. بهایش درخواست‌های بیشتر است. اگر endpoint رایگان محدودتان کرد، `stream_interim_seconds = 0` بگذارید، یا `streaming = false` تا همه‌چیز آخر کار یک‌جا فرستاده شود.

برای مقایسه‌ی موتورها با صدای خودتان، مجموعه‌ی ۵۰ جمله‌ای را ضبط کنید و بنچمارک را اجرا کنید: [docs/benchmarks.md](docs/benchmarks.md).

## ترجمه

فارسی بگویید، انگلیسی تایپ شود. پیش‌فرض خاموش است.

```bash
./scripts/install-user.sh translate
```

```toml
[translate]
enabled = true
language = "en"      # "en"، "English"، "de"، "zh-CN" و ...
```

```bash
voicetype translate "سلام دنیا"    # Hello world.
```

اول متن فارسی اصلاح می‌شود، بعد ترجمه، و نتیجه سر مکان‌نما تایپ می‌شود. اگر هیچ سرویسی جواب ندهد، همان متن اصلی تایپ می‌شود و روی کارت هشدار می‌آید، پس ترجمه‌ی ناموفق هیچ‌وقت حرف‌هایتان را از بین نمی‌برد.

ترجمه از endpointهای رایگان Google، Bing، Yandex و Alibaba رد می‌شود. یکی‌یکی امتحان می‌شوند تا یکی جواب بدهد. کلید API و حساب لازم نیست. این endpointها رسمی نیستند و گاهی محدودتان می‌کنند یا بلاک می‌شوند، و دلیل چهارتا بودنشان همین است. جزئیات: [docs/translation.md](docs/translation.md).

## تنظیمات

همه‌چیز در `~/.config/voicetype/config.toml` است و همه‌ی کلیدها اختیاری‌اند. یک نمونه‌ی کوتاه:

```toml
[stt]
provider = "google_free"      # google_free | vosk | whisper | google_cloud
language = "fa-IR"
silence_threshold_seconds = 3.0

[overlay]
position = "bottom"
theme = "nocturne"            # یک پالت آماده، یا مسیر فایل .css خودتان

[postprocess]
digits = "keep"               # keep | latin | persian

[postprocess.custom_replacements]
"می شود" = "می‌شود"
```

```bash
voicetype config init     # فایل کامل و کامنت‌دار با همه‌ی پیش‌فرض‌ها را می‌نویسد
voicetype config check    # ویرایش‌هایتان را اعتبارسنجی می‌کند
voicetype reload          # بدون ری‌استارت اعمال می‌کند
```

فهرست کامل کلیدها با پیش‌فرض و بازه در [docs/configuration.md](docs/configuration.md) است. تم‌ها در [docs/theming.md](docs/theming.md) و قاعده‌های متن فارسی در [docs/postprocess.md](docs/postprocess.md) توضیح داده شده‌اند.

اینترنت فیلتر است؟ `proxy = "socks5h://127.0.0.1:1080"` را زیر `[network]` بگذارید (پروکسی HTTP هم جواب می‌دهد) و `voicetype net-test` را بزنید. اگر پروکسی کار نکند، VoiceType به‌جای اتصال مستقیم خطا می‌دهد تا صدایتان از مسیری غیر از انتخاب خودتان بیرون نرود. جزئیات: [docs/network.md](docs/network.md).

## حریم خصوصی

- موتور پیش‌فرض یعنی `google_free` صدای میکروفون را به Google می‌فرستد و دیمون هنگام شروع این را در لاگ می‌گوید. برای کار کاملاً آفلاین `vosk` یا `whisper` را بگذارید.
- اگر ترجمه روشن باشد، **متنِ** حرف‌هایتان به سرویسی که جواب بدهد فرستاده می‌شود.
- صدا ذخیره نمی‌شود مگر `debug_save_audio` را روشن کنید، و متن هیچ‌وقت در لاگ نمی‌آید.
- سوکت کنترل مجوز `0600` دارد، متن هیچ‌وقت به شل داده نمی‌شود و کاراکترهای کنترلی پیش از paste حذف می‌شوند.

جزئیات: [docs/privacy.md](docs/privacy.md).

## چیزهایی که باید بدانید

- استریم با Google شبیه‌سازی‌شده است. endpoint رایگان استریم واقعی ندارد و ما سر مکث‌ها می‌بریم، پس ممکن است کلمه‌ای در پیش‌نمایش زنده با متن نهایی فرق کند.
- قاعده‌های نیم‌فاصله ابتکاری (heuristic) هستند. اگر کلمه‌ای را اشتباه گذاشت، یک خط به `custom_replacements` اضافه کنید.
- Klipper و مدیرهای کلیپ‌بورد دیگر ممکن است متن دیکته‌شده را در تاریخچه نگه دارند.
- حالت hold (نگه داشتن کلید) گروه `input` و افزونه‌ی `evdev` می‌خواهد. حالت پیش‌فرض (فشار برای شروع و پایان) هیچ‌کدام را لازم ندارد.
- روی KDE اسکریپت میان‌بر را مستقیم در `kglobalshortcutsrc` می‌نویسد و این قالب بین نسخه‌های Plasma ممکن است فرق کند. اگر کار نکرد، دستور `voicetype-toggle` را دستی در System Settings، Shortcuts اضافه کنید.
- ضبط در دسکتاپ‌های PipeWire با `pw-cat` انجام می‌شود. PortAudio کارت صدای خام را اختصاصی قفل می‌کند و ممکن است میکروفون را از کل دسکتاپ غیب کند. اگر برایتان پیش آمد، راه‌حل در [docs/configuration.md](docs/configuration.md#how-the-microphone-is-captured) است.

فهرست کامل: [docs/known-issues.md](docs/known-issues.md).

## وقتی چیزی خراب شد

اول `voicetype doctor` را بزنید. هر خط ناموفق دستورِ رفعش را کنارش دارد.

| مشکل | امتحان کنید |
|---|---|
| `daemon is not running` | `systemctl --user start voicetype` و بعد `journalctl --user -u voicetype -f` |
| میان‌بر کاری نمی‌کند | ببینید `voicetype toggle` در ترمینال کار می‌کند یا نه. روی Plasma یک بار خارج و وارد شوید و `scripts/install-user.sh --hotkey-only` را اجرا کنید. |
| چیزی تایپ نمی‌شود | `systemctl --user enable --now ydotool` و خط text injection را در `doctor` ببینید |
| paste در ترمینال اشتباه است | `kdotool` را نصب کنید یا `[injector] paste_shortcut = "ctrl+shift+v"` بگذارید |
| پنجره دیده نمی‌شود | `gtk4-layer-shell` و `python-gobject` را نصب کنید. GNOME از layer-shell پشتیبانی نمی‌کند و آنجا بدون پنجره کار می‌کند. |
| زود یا دیر تمام می‌شود | `silence_threshold_seconds` را زیر `[stt]` تغییر دهید |

بیشتر: [docs/troubleshooting.md](docs/troubleshooting.md).

## دست بردن در کد

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check .
pytest              # میکروفون، شبکه و نمایشگر لازم نیست
```

هسته (کانفیگ، dispatcher، موتورها، پس‌پردازش، منطق injector) روی هر سیستم‌عاملی اجرا می‌شود و GTK و `evdev` و PortAudio تنبل import می‌شوند. تست‌هایی که سوکت Unix دارند روی ویندوز skip می‌شوند، پس برای مجموعه‌ی کامل روی لینوکس اجرا کنید.

اضافه کردن موتور جدید به dispatcher دست نمی‌زند:

```python
from voicetype.stt import registry

registry.register("mine", lambda opts: MyBackend(opts))   # بعد: provider = "mine"
```

برای فهمیدن طراحی از سند معماری شروع کنید، ولی اول بخش errata آن را بخوانید، چون چند فرض اولیه غلط درآمد. تصمیم‌هایی که در راه گرفتیم در [docs/adr](docs/adr/) است.

## سپاس

تشخیص سکوت تطبیقی، سوکت کنترل، تزریق با paste و پنجره‌ی layer-shell از طراحی [hyprwhspr](https://github.com/goodroot/hyprwhspr) الهام گرفته‌اند. تشخیص آفلاین با [Vosk](https://github.com/alphacep/vosk-api) و [faster-whisper](https://github.com/SYSTRAN/faster-whisper) انجام می‌شود.

## مجوز

[MIT](LICENSE)

</div>
