"""Optional spoken commands. Only applied when the *whole* utterance is a command,
so ordinary sentences containing e.g. «نقطه» are untouched."""

from __future__ import annotations

# phrase → text to inject ("" means: inject nothing)
COMMANDS: dict[str, str] = {
    "خط جدید": "\n",
    "سطر جدید": "\n",
    "پاراگراف جدید": "\n\n",
    "نقطه": ".",
    "ویرگول": "،",
    "علامت سوال": "؟",
    "علامت سؤال": "؟",
    "علامت تعجب": "!",
    "دو نقطه": ":",
    "پاک کن": "",
    "new line": "\n",
    "new paragraph": "\n\n",
    "period": ".",
    "comma": ",",
    "question mark": "?",
}


def match_command(text: str) -> str | None:
    key = " ".join(text.strip().strip(".،!؟?").lower().split())
    return COMMANDS.get(key)
