"""Word-by-word animation for the live transcript preview.

Adapted from hyprwhspr's ``lib/mic_osd/transcript_preview.py``.

The hardest part of showing a live partial transcript is that the provider
revises it constantly: a word that was ``trans`` becomes ``transcript``, and
a word that was ``hello`` is often ``hello,`` a moment later. Restarting the
whole animation on every keystroke makes the text unreadable at speech
speed.

The animator therefore diffs the previous and current word windows with a
longest-common-subsequence match, so only genuinely new words animate in.
Character-level corrections to the last token update in place without
restarting anything.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..config import DEFAULT_FONT, DEFAULT_FONT_SIZE, resolve_font_family, resolve_font_size
from ..text import LTR, base_direction
from .easing import clamp, ease_in_out, ease_out_cubic, progress

__all__ = [
    "TranscriptConfig",
    "RenderWord",
    "TranscriptFrame",
    "TranscriptAnimator",
]


@dataclass(frozen=True)
class TranscriptConfig:
    """Validated settings for the live transcript shown above the overlay."""

    enabled: bool = True
    word_limit: int = 4
    idle_timeout_seconds: float = 1.40

    #: The font the user configured: the family (``overlay.font``) and the
    #: point size of the main text (``overlay.font_size``).
    font_family: str = DEFAULT_FONT
    font_size: float = DEFAULT_FONT_SIZE

    # Visual tuning is part of the overlay style, not user configuration.
    enter_seconds: float = 0.20
    exit_seconds: float = 0.20
    stagger_seconds: float = 0.028
    offset_y: float = 7.0
    rise_px: float = 9.0
    max_width: float = 320.0

    @classmethod
    def from_overlay_config(cls, cfg) -> TranscriptConfig:
        """Build from a :class:`voicetype.config.OverlayConfig`."""
        return cls(
            enabled=bool(getattr(cfg, "show_transcript", True)),
            word_limit=int(clamp(int(getattr(cfg, "transcript_words", 4)), 1, 12)),
            font_family=resolve_font_family(getattr(cfg, "font", "")),
            font_size=resolve_font_size(getattr(cfg, "font_size", DEFAULT_FONT_SIZE)),
        )


@dataclass(frozen=True)
class RenderWord:
    """One word to paint for the current animation frame."""

    text: str
    source: str  # "previous" | "current"
    index: int
    alpha: float
    y_offset: float = 0.0
    matched_from: int | None = None
    layout_progress: float = 1.0


@dataclass(frozen=True)
class TranscriptFrame:
    """Everything the renderer needs for one frame."""

    previous_words: tuple[str, ...]
    current_words: tuple[str, ...]
    words: tuple[RenderWord, ...]
    #: The direction the words run in ("ltr" or "rtl").  A Persian or Arabic
    #: transcript is laid out from the right, an English one from the left.
    direction: str = LTR


def _lcs_matches(previous: Sequence[str], current: Sequence[str]) -> tuple[tuple[int, int], ...]:
    """Duplicate-safe longest-common-subsequence matches for tiny word windows."""
    rows = len(previous) + 1
    cols = len(current) + 1
    table = [[0] * cols for _ in range(rows)]

    for old_index in range(len(previous) - 1, -1, -1):
        for new_index in range(len(current) - 1, -1, -1):
            if previous[old_index] == current[new_index]:
                table[old_index][new_index] = table[old_index + 1][new_index + 1] + 1
            else:
                table[old_index][new_index] = max(
                    table[old_index + 1][new_index],
                    table[old_index][new_index + 1],
                )

    matches = []
    old_index = 0
    new_index = 0
    while old_index < len(previous) and new_index < len(current):
        if previous[old_index] == current[new_index]:
            matches.append((old_index, new_index))
            old_index += 1
            new_index += 1
        elif table[old_index + 1][new_index] >= table[old_index][new_index + 1]:
            old_index += 1
        else:
            new_index += 1
    return tuple(matches)


class TranscriptAnimator:
    """Latest-state transcript animation with no event queue.

    Incoming partials replace the target immediately. Character-by-character
    corrections to the last token update in place instead of restarting the
    animation. Word-boundary changes animate, so 200–250 WPM speech remains
    readable while the renderer always converges on the newest few words.
    """

    def __init__(
        self,
        config: TranscriptConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or TranscriptConfig()
        self._clock = clock
        self.previous_words: tuple[str, ...] = ()
        self.current_words: tuple[str, ...] = ()
        self.matches: tuple[tuple[int, int], ...] = ()
        self.direction: str = LTR
        self.transition_started_at = self._clock()
        self.last_activity_at = self.transition_started_at

    def _extract_words(self, text: str) -> tuple[str, ...]:
        words = tuple((text or "").split())
        return words[-self.config.word_limit :]

    def set_text(self, text: str, now: float | None = None) -> bool:
        """Feed a new partial transcript. Returns ``True`` when it changed."""
        now = self._clock() if now is None else now
        words = self._extract_words(text)

        # The direction belongs to the whole paragraph, not to the word window
        # that is kept: a Persian sentence stays right-to-left even when only
        # its last words are on screen.
        if text:
            self.direction = base_direction(text)

        if words:
            self.last_activity_at = now

        if words == self.current_words:
            return False

        # Realtime providers often revise only the unfinished final token
        # ("trans" -> "transcript"). Do not restart a 200 ms animation for
        # every character; preserve the current transition and replace it.
        if (
            words
            and self.current_words
            and len(words) == len(self.current_words)
            and words[:-1] == self.current_words[:-1]
        ):
            self.current_words = words
            return True

        self._begin_transition(words, now)
        return True

    def clear(self, now: float | None = None) -> bool:
        """Fade the transcript out. Returns ``True`` when it changed."""
        now = self._clock() if now is None else now
        if not self.current_words:
            return False
        self._begin_transition((), now)
        return True

    def _begin_transition(self, words: tuple[str, ...], now: float) -> None:
        self.previous_words = self.current_words
        self.current_words = words
        self.matches = _lcs_matches(self.previous_words, self.current_words)
        self.transition_started_at = now

    def _expire_if_idle(self, now: float) -> None:
        timeout = self.config.idle_timeout_seconds
        if (
            timeout > 0
            and self.current_words
            and (now - self.last_activity_at) >= timeout
        ):
            self._begin_transition((), now)

    def frame(self, now: float | None = None) -> TranscriptFrame:
        """Compute the words to paint for this frame."""
        now = self._clock() if now is None else now
        self._expire_if_idle(now)

        if not self.config.enabled:
            return TranscriptFrame((), (), ())

        elapsed = max(0.0, now - self.transition_started_at)
        stable_by_new = {new: old for old, new in self.matches}
        stable_old = {old for old, _ in self.matches}
        rendered = []

        # Removed words leave first, rising away from the overlay. They are
        # never queued; a newer transcript immediately replaces this transition.
        for old_index, word in enumerate(self.previous_words):
            if old_index in stable_old:
                continue
            reverse_index = len(self.previous_words) - 1 - old_index
            delay = reverse_index * self.config.stagger_seconds * 0.55
            exit_progress = ease_in_out(
                progress(elapsed, self.config.exit_seconds, delay)
            )
            rendered.append(
                RenderWord(
                    word,
                    "previous",
                    old_index,
                    1.0 - exit_progress,
                    y_offset=-self.config.rise_px * exit_progress,
                )
            )

        for new_index, word in enumerate(self.current_words):
            matched_from = stable_by_new.get(new_index)
            if matched_from is not None:
                # A word that survived the diff slides to its new position.
                layout_progress = ease_out_cubic(
                    progress(elapsed, self.config.enter_seconds)
                )
                rendered.append(
                    RenderWord(
                        word,
                        "current",
                        new_index,
                        1.0,
                        matched_from=matched_from,
                        layout_progress=layout_progress,
                    )
                )
                continue

            delay = new_index * self.config.stagger_seconds
            enter_progress = ease_out_cubic(
                progress(elapsed, self.config.enter_seconds, delay)
            )
            rendered.append(
                RenderWord(
                    word,
                    "current",
                    new_index,
                    enter_progress,
                    y_offset=self.config.rise_px * (1.0 - enter_progress),
                )
            )

        return TranscriptFrame(
            self.previous_words,
            self.current_words,
            tuple(rendered),
            self.direction,
        )
