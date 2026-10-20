"""User replacements: literal text by default, regex when the key starts with ``re:``."""

from __future__ import annotations

import re

from ..config import ConfigError

MAX_PATTERN_LEN = 200
_NESTED_QUANT = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]")  # (a+)+ style → catastrophic


def compile_replacements(pairs: tuple[tuple[str, str], ...]) -> list[tuple[re.Pattern, str, bool]]:
    out: list[tuple[re.Pattern, str, bool]] = []
    for key, value in pairs:
        if key.startswith("re:"):
            pattern = key[3:]
            if len(pattern) > MAX_PATTERN_LEN or _NESTED_QUANT.search(pattern):
                raise ConfigError(f"config: regex replacement '{key}' is too long or has nested quantifiers")
            try:
                out.append((re.compile(pattern), value, True))
            except re.error as e:
                raise ConfigError(f"config: invalid regex replacement '{key}': {e}") from e
        else:
            out.append((re.compile(re.escape(key)), value, False))
    return out


def apply_replacements(text: str, rules: list[tuple[re.Pattern, str, bool]]) -> str:
    for pat, value, is_regex in rules:
        text = pat.sub(value if is_regex else value.replace("\\", "\\\\"), text)
    return text
