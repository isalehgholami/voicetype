"""English technical terms: Persian transliteration ↔ canonical Latin spelling.

The architecture doc's single Latin pattern could never match «پایتون»; here every entry maps
*both* the Persian transliterations and any-case Latin spelling to one canonical form.
"""

from __future__ import annotations

import re
from functools import lru_cache

# canonical → Persian transliterations (Latin spellings are matched case-insensitively)
TERMS: dict[str, tuple[str, ...]] = {
    "Python": ("پایتون", "پایسون"),
    "Linux": ("لینوکس", "لینکس"),
    "Qt": ("کیوتی", "کیو تی"),
    "Wayland": ("ویلند", "وی‌لند"),
    "KDE": ("کی دی ای",),
    "GitHub": ("گیت‌هاب", "گیت هاب", "گیتهاب"),
    "Git": ("گیت",),
    "GitLab": ("گیت‌لب", "گیت لب"),
    "Docker": ("داکر", "دوکر"),
    "JavaScript": ("جاوااسکریپت", "جاوا اسکریپت"),
    "TypeScript": ("تایپ‌اسکریپت", "تایپ اسکریپت"),
    "Java": ("جاوا",),
    "React": ("ریکت",),
    "Django": ("جنگو",),
    "Flask": ("فلسک",),
    "API": ("ای پی آی", "ایپیآی"),
    "JSON": ("جیسون",),
    "HTML": ("اچ تی ام ال",),
    "CSS": ("سی اس اس",),
    "SQL": ("اس کیو ال", "سیکوال"),
    "PostgreSQL": ("پستگرس", "پوستگرس"),
    "Redis": ("ردیس",),
    "Nginx": ("انجین‌اکس", "انجینیکس"),
    "Windows": ("ویندوز",),
    "Android": ("اندروید",),
    "Chrome": ("کروم",),
    "Firefox": ("فایرفاکس",),
    "Telegram": ("تلگرام",),
    "WhatsApp": ("واتساپ", "واتس‌اپ"),
    "Google": ("گوگل",),
    "OpenAI": ("اوپن ای آی",),
    "ChatGPT": ("چت جی پی تی", "چت‌جی‌پی‌تی"),
    "Claude": ("کلود",),
    "Bluetooth": ("بلوتوث",),
    "Wi-Fi": ("وای‌فای", "وای فای"),
    "USB": ("یو اس بی",),
    "GPU": ("جی پی یو",),
    "CPU": ("سی پی یو",),
    "Arch": ("آرچ",),
    "Ubuntu": ("اوبونتو",),
    "VS Code": ("وی اس کد",),
    "PyCharm": ("پای‌چارم", "پایچارم"),
    "Vosk": ("ووسک",),
    "Whisper": ("ویسپر",),
}

_LEFT = r"(?<![\w‌])"
_RIGHT = r"(?![\w‌])"


@lru_cache(maxsize=1)
def _compiled() -> list[tuple[re.Pattern, str]]:
    rules: list[tuple[re.Pattern, str]] = []
    for canonical, persian in TERMS.items():
        alts = [re.escape(p) for p in persian]
        # Persian forms are exact; Latin form is case-insensitive.  Longest alternatives first.
        pat_fa = re.compile(_LEFT + "(?:" + "|".join(sorted(alts, key=len, reverse=True)) + ")" + _RIGHT)
        pat_en = re.compile(_LEFT + re.escape(canonical) + _RIGHT, re.IGNORECASE)
        rules.append((pat_fa, canonical))
        rules.append((pat_en, canonical))
    # longer canonicals first so "GitHub" wins over "Git"
    rules.sort(key=lambda r: -len(r[1]))
    return rules


def apply_terms(text: str) -> str:
    for pat, canonical in _compiled():
        text = pat.sub(canonical, text)
    return text
