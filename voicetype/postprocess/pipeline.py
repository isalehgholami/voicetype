"""Composable post-processing pipeline: strip → filter → replace → terms → ZWNJ → digits →
punctuation → capitalize → auto-punctuate. Every stage is toggled by config."""

from __future__ import annotations

from collections.abc import Callable

from ..config import PostprocessConfig
from .commands import match_command
from .hallucination import is_hallucination
from .persian import convert_digits, normalize_zwnj, persian_punctuation
from .replacements import apply_replacements, compile_replacements
from .terms import apply_terms

SENTENCE_END = tuple(".!?؟…:؛;،,)\"'»”")


def capitalize_first(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1 :] if ch.isascii() else text
        if ch.isdigit():
            return text
    return text


def auto_punctuate(text: str) -> str:
    if text.endswith("\n"):
        return text
    t = text.rstrip()
    if not t or t.endswith(SENTENCE_END):
        return text
    return t + "."


class Pipeline:
    """Callable ``text -> str``; an empty result means "inject nothing"."""

    def __init__(self, cfg: PostprocessConfig) -> None:
        self.cfg = cfg
        self._rules = compile_replacements(cfg.custom_replacements)
        self.stages: list[tuple[str, Callable[[str], str]]] = self._build()

    def _build(self) -> list[tuple[str, Callable[[str], str]]]:
        c = self.cfg
        stages: list[tuple[str, Callable[[str], str]]] = [("strip", str.strip)]
        if c.filter_hallucinations:
            stages.append(("hallucination", lambda t: "" if is_hallucination(t) else t))
        if self._rules:
            stages.append(("replacements", lambda t: apply_replacements(t, self._rules)))
        if c.english_terms:
            stages.append(("terms", apply_terms))
        if c.normalize_zwnj:
            stages.append(("zwnj", normalize_zwnj))
        if c.digits != "keep":
            stages.append(("digits", lambda t: convert_digits(t, c.digits)))
        if c.persian_punctuation:
            stages.append(("fa_punct", persian_punctuation))
        if c.capitalize_first:
            stages.append(("capitalize", capitalize_first))
        if c.auto_punctuate:
            stages.append(("auto_punct", lambda t: auto_punctuate(t) if t.strip() else t))
        return stages

    def __call__(self, text: str) -> str:
        text = text.strip()
        if self.cfg.voice_commands:
            cmd = match_command(text)
            if cmd is not None:
                return cmd
        for _name, fn in self.stages:
            if not text:
                break
            text = fn(text)
        return text

    @property
    def stage_names(self) -> list[str]:
        return [n for n, _ in self.stages]
