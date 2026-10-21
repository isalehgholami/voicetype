"""The floating HUD: the card, its states, and the waveform that signs it.

The overlay used to draw a small pill with the live transcript floating above
it.  The HUD replaces both with a single card: a status row, the text that
matters, and a slim waveform.  It is display-only — there is nothing in here
the user can click, and nothing in here drives the application.

Everything in this module is pure.  No GTK, no cairo at import time: the
painters take the context they draw on as an argument, exactly like the
visualizations they replace, so the whole visual language stays unit-testable
without a compositor (ADR-0001).

The card has a **fixed size**.  It does not follow the text it holds: a card
that grows and shrinks with every word is a card that never settles.  The
text lives in a fixed box inside it instead, and the box *rolls*: text that
does not fit scrolls through it, and a text that is replaced rolls out while
its successor rolls in.  :class:`TextTicker` is that motion.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from .animations import AutoGain
from .easing import clamp, ease_in_out, mix_rgba, progress
from .theme import Palette

__all__ = [
    "ColorTween",
    "Crossfade",
    "HudContent",
    "HudFrame",
    "HudMeasures",
    "HudMetrics",
    "HudState",
    "TextTicker",
    "Waveform",
    "draw_card",
    "draw_waveform",
    "hud_color",
    "hud_font_sizes",
    "hud_label",
    "hud_layout",
    "inner_surface",
    "rounded_rect",
]


class HudState(Enum):
    """What the card is currently showing."""

    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    TRANSLATED = "translated"
    DONE = "done"
    READY = "ready"
    ERROR = "error"
    NOTICE = "notice"


#: The label every state shows.  The labels are upper case and letter-spaced:
#: they are a whisper, not a headline.
STATE_LABELS: dict[HudState, str] = {
    HudState.LISTENING: "LISTENING",
    HudState.TRANSCRIBING: "TRANSCRIBING",
    HudState.TRANSLATING: "TRANSLATING",
    HudState.TRANSLATED: "TRANSLATED",
    HudState.DONE: "DONE",
    HudState.READY: "READY",
    HudState.ERROR: "ERROR",
    HudState.NOTICE: "NOTICE",
}

#: The states whose waveform carries activity rather than audio.
BUSY_STATES = (HudState.TRANSCRIBING, HudState.TRANSLATING)


def hud_label(state: HudState) -> str:
    """The status label of ``state``."""
    return STATE_LABELS.get(state, STATE_LABELS[HudState.READY])


def hud_color(palette: Palette, state: HudState) -> tuple:
    """The colour that represents ``state``.

    The live and the finished states share the palette's success colour (the
    mint of the default palette), everything that is being worked on shares
    the accent (the violet), and the quiet states stay out of the way.
    """
    if state in (HudState.LISTENING, HudState.TRANSLATED, HudState.DONE):
        return palette.success
    if state in BUSY_STATES:
        return palette.accent
    if state == HudState.ERROR:
        return palette.error
    if state == HudState.NOTICE:
        return palette.paused
    return palette.text_dim


def inner_surface(palette: Palette) -> tuple:
    """The card's inner surface: the card's own colour, pushed towards black.

    It is what the language chip is filled with, so the chip reads as a recess
    in the card instead of another layer on top of it.
    """
    background = palette.background
    return mix_rgba(background, (0.0, 0.0, 0.0, 1.0), 0.32)


def hud_font_sizes(configured: float, system_size: float) -> tuple[float, float, float]:
    """The point sizes of the card's three texts.

    Returns ``(primary, status, secondary)``.  A configured size wins; without
    one the desktop's own size is used, clamped to the band that reads well in
    a card this small: the main text lands around 15 px and the status label
    around 11 px.
    """
    primary = float(configured) if configured and configured > 0.0 else clamp(float(system_size), 10.0, 12.5)
    return (primary, primary * 0.72, primary * 0.86)


@dataclass(frozen=True)
class HudMetrics:
    """The card's geometry, in surface pixels.

    The numbers are the ones the design was drawn at; they scale with the
    text, so a bigger font still fits.
    """

    padding_x: float = 16.0
    padding_y: float = 14.0
    radius: float = 20.0
    #: The transparent margin around the card, so its shadow is not clipped.
    surface_margin: float = 9.0
    dot_radius: float = 3.0
    dot_gap: float = 7.0
    language_gap: float = 9.0
    language_padding: float = 6.0
    language_padding_y: float = 2.0
    stack_gap: float = 11.0
    text_gap: float = 7.0
    wave_gap: float = 13.0
    wave_height: float = 20.0
    wave_bar_width: float = 2.5
    wave_gap_x: float = 3.0
    wave_bars: int = 19
    wave_min_height: float = 2.0
    #: How long a state change takes, in seconds.
    transition: float = 0.20

    @property
    def wave_width(self) -> float:
        """The width the whole waveform occupies."""
        return self.wave_bars * self.wave_bar_width + (self.wave_bars - 1) * self.wave_gap_x


@dataclass(frozen=True)
class HudContent:
    """What the card shows right now."""

    state: HudState
    #: The text the card leads with: the speech, or the translation.
    primary: str = ""
    #: The text that supports it: the source of a translation, or the text
    #: that was already on screen when something went wrong.
    secondary: str = ""
    #: The language of the translation ("GERMAN"), when it is known.
    language: str = ""
    #: The status label; empty means the state's own label.
    label: str = ""


@dataclass
class HudMeasures:
    """What the caller measured for one frame.

    Pango does the measuring (it is the only thing that knows the font), so
    the HUD is told the sizes instead of asking for them.  Only the status
    row is measured: the card has a fixed width, so the texts are wrapped
    inside it instead of deciding how wide it is.
    """

    status_width: float = 0.0
    status_height: float = 0.0
    language_width: float = 0.0


@dataclass(frozen=True)
class HudFrame:
    """Where everything sits, relative to the card's top-left corner."""

    width: float
    height: float
    status_y: float
    status_height: float
    dot_center: tuple[float, float]
    label_x: float
    label_y: float
    language: tuple[float, float, float, float]
    #: The window the text rolls through: ``(x, y, width, height)``.
    text_box: tuple[float, float, float, float]
    wave: tuple[float, float, float, float]

    @property
    def has_wave(self) -> bool:
        return self.wave[3] > 0.0


def hud_layout(
    content: HudContent,
    measures: HudMeasures,
    metrics: HudMetrics,
    *,
    width: float,
    height: float,
    line_height: float = 0.0,
) -> HudFrame:
    """Lay the card out for one frame.

    The card has a fixed size: its width and its height come from the
    configuration and do not depend on what the card holds.  The text box is
    whatever is left between the status row and the waveform, and it is the
    same in every state — so the text never moves when the state does.

    A card that is too short for its own furniture is grown to fit: the text
    box always keeps room for one line.
    """
    pad_x = metrics.padding_x
    pad_y = metrics.padding_y

    status_height = max(measures.status_height, metrics.dot_radius * 2.0)
    label_x = pad_x + metrics.dot_radius * 2.0 + metrics.dot_gap
    language_width = 0.0
    if content.language:
        language_width = measures.language_width + metrics.language_padding * 2.0

    furniture = (
        pad_y * 2.0
        + status_height
        + metrics.stack_gap
        + max(line_height, 0.0)
        + metrics.wave_gap
        + metrics.wave_height
    )
    card_width = max(float(width), pad_x * 2.0)
    card_height = max(float(height), furniture)

    text_y = pad_y + status_height + metrics.stack_gap
    wave_y = card_height - pad_y - metrics.wave_height
    text_height = max(0.0, wave_y - metrics.wave_gap - text_y)

    wave_height = 0.0
    if content.state in (HudState.LISTENING, *BUSY_STATES, HudState.TRANSLATED):
        wave_height = metrics.wave_height
    wave_x = (card_width - metrics.wave_width) / 2.0

    language_x = label_x + measures.status_width + metrics.language_gap
    language_x = min(language_x, card_width - pad_x - language_width)
    language_y = pad_y - metrics.language_padding_y
    language_height = status_height + metrics.language_padding_y * 2.0

    return HudFrame(
        width=card_width,
        height=card_height,
        status_y=pad_y,
        status_height=status_height,
        dot_center=(pad_x + metrics.dot_radius, pad_y + status_height / 2.0),
        label_x=label_x,
        label_y=pad_y,
        language=(language_x, language_y, language_width, language_height),
        text_box=(pad_x, text_y, card_width - pad_x * 2.0, text_height),
        wave=(wave_x, wave_y, metrics.wave_width, wave_height),
    )


class TextTicker:
    """The card's text box: a fixed window the text rolls through.

    The card has a fixed size, so its text lives in a fixed box, and two
    motions happen inside that box:

    * text that does not fit scrolls — the box follows the newest line, so a
      growing sentence rolls instead of jumping;
    * text that is *replaced* does not cross-fade — the old document rolls out
      of the box and the new one rolls in.

    The scroll answers two different needs.  While the live preview grows
    word by word it follows the newest line quickly, because that is the text
    the user is waiting for.  A document that was rolled in drifts instead:
    its beginning is read first, and the rest of it rolls past afterwards.
    """

    #: How quickly the box catches up with the newest line, in seconds.
    LIVE_SCROLL_TAU = 0.09

    #: How quickly a finished document drifts through the box, in seconds.
    DRIFT_SCROLL_TAU = 1.10

    def __init__(
        self,
        metrics: HudMetrics,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.metrics = metrics
        self._clock = clock
        #: How long a roll takes, in seconds.
        self.duration = metrics.transition
        self._current: tuple[str, str] = ("", "")
        self._previous: tuple[str, str] | None = None
        self._started_at = clock()
        self._progress = 1.0
        self._scroll = 0.0
        self._target = 0.0
        self._roll = 0.0
        self._previous_scroll = 0.0
        self._tau = self.DRIFT_SCROLL_TAU

    # ------------------------------------------------------------------ state
    @property
    def current(self) -> tuple[str, str]:
        """The document in the box: the main text and the one below it."""
        return self._current

    @property
    def previous(self) -> tuple[str, str] | None:
        """The document that is rolling out, while one is."""
        return self._previous

    @property
    def progress(self) -> float:
        """How far the roll has come: 0 = the old text is leaving, 1 = settled."""
        return self._progress

    @property
    def scroll(self) -> float:
        """How far the document has scrolled inside the box, in pixels."""
        return self._scroll

    @property
    def previous_scroll(self) -> float:
        """How far the leaving document had scrolled, in pixels."""
        return self._previous_scroll

    @property
    def roll(self) -> float:
        """How far one roll carries the text, in pixels."""
        return self._roll

    # ----------------------------------------------------------------- actions
    def set(
        self,
        primary: str,
        secondary: str = "",
        instant: bool = False,
        live: bool = False,
    ) -> bool:
        """Put a new document into the box.  Returns ``True`` when it changed.

        An *instant* change stays where it is: the live preview grows word by
        word, and rolling it would make it unreadable.  Every other change
        rolls: the old document leaves the box and the new one comes in.

        A *live* document follows the newest line quickly; a finished one
        drifts, so its beginning is read before the rest rolls past.
        """
        document = (primary, secondary)
        if document == self._current:
            return False
        if instant:
            self._previous = None
            self._progress = 1.0
        else:
            self._previous = self._current
            self._previous_scroll = self._scroll
            self._scroll = 0.0
            self._target = 0.0
            self._progress = 0.0
            self._started_at = self._clock()
        self._tau = self.LIVE_SCROLL_TAU if live else self.DRIFT_SCROLL_TAU
        self._current = document
        return True

    def set_roll(self, distance: float) -> None:
        """How far one roll carries the text.

        The box's own height is the right distance: the old text leaves the
        window upwards and the new one comes in from below it, and the two
        never share the same part of the window.
        """
        self._roll = max(0.0, float(distance))

    def reset(self) -> None:
        """Empty the box: the next session starts at the top of it."""
        self._current = ("", "")
        self._previous = None
        self._progress = 1.0
        self._scroll = 0.0
        self._target = 0.0

    def set_overflow(self, overflow: float) -> None:
        """How much of the document does not fit into the box."""
        self._target = max(0.0, float(overflow))

    def advance(self, dt: float) -> None:
        """Move the ticker on: the roll and the scroll."""
        if self._previous is not None:
            t = progress(self._clock() - self._started_at, self.duration)
            self._progress = ease_in_out(t)
            if t >= 1.0:
                self._previous = None
        if self.duration <= 0.0:
            # Without motion there is nothing to ease: the box simply shows
            # the newest line.
            self._scroll = self._target
            return
        factor = 1.0 - math.exp(-max(0.0, dt) / self._tau)
        self._scroll += (self._target - self._scroll) * factor


class Crossfade:
    """Swaps a value in and out over a short, eased transition.

    The old value stays on screen while it fades out and the new one fades
    in, so a state change never blinks.  The transition is clock-driven and
    injectable, so its timings can be tested without a compositor.
    """

    def __init__(self, duration: float = 0.20, clock: Callable[[], float] = time.monotonic) -> None:
        self.duration = max(0.0, float(duration))
        self._clock = clock
        self._previous: object | None = None
        self._current: object | None = None
        self._started_at = clock()

    @property
    def current(self) -> object | None:
        return self._current

    @property
    def previous(self) -> object | None:
        return self._previous

    def set(self, value: object | None, instant: bool = False) -> bool:
        """Set the value. Returns ``True`` when it changed.

        An ``instant`` change replaces the value without a transition: the
        live preview grows word by word, and crossfading it would show two
        versions of the same sentence at once.
        """
        if value == self._current:
            return False
        self._previous = None if instant else self._current
        self._current = value
        self._started_at = self._clock()
        return True

    def alphas(self, now: float | None = None) -> tuple[float, float]:
        """The opacity of the old and the new value."""
        if self._current is None:
            return 0.0, 0.0
        if self._previous is None:
            return 0.0, 1.0
        now = self._clock() if now is None else now
        t = progress(now - self._started_at, self.duration)
        eased = ease_in_out(t)
        return 1.0 - eased, eased


class ColorTween:
    """Eases a colour towards a new one instead of snapping to it."""

    def __init__(self, duration: float = 0.20, clock: Callable[[], float] = time.monotonic) -> None:
        self.duration = max(0.0, float(duration))
        self._clock = clock
        self._from: tuple = (0.0, 0.0, 0.0, 1.0)
        self._to: tuple = (0.0, 0.0, 0.0, 1.0)
        self._started_at = clock()
        self._known = False

    def set(self, color: tuple) -> bool:
        """Start a move towards ``color``. Returns ``True`` when it changed."""
        if color == self._to:
            return False
        if not self._known:
            # The first colour is simply taken: there is nothing to ease from.
            self._known = True
            self._from = tuple(color)
            self._to = tuple(color)
            self._started_at = self._clock()
            return True
        self._from = self.value()
        self._to = tuple(color)
        self._started_at = self._clock()
        return True

    def value(self, now: float | None = None) -> tuple:
        """The colour of this frame."""
        if self.duration <= 0.0:
            return self._to
        now = self._clock() if now is None else now
        t = ease_in_out(progress(now - self._started_at, self.duration))
        return mix_rgba(self._from, self._to, t)


def _resample(values: Sequence[float], count: int) -> list[float]:
    """Resample ``values`` down to ``count`` points with linear interpolation."""
    n = len(values)
    if count <= 0:
        return []
    if n == 0:
        return [0.0] * count
    if n == count:
        return [float(v) for v in values]
    if n == 1:
        return [float(values[0])] * count

    out: list[float] = []
    for i in range(count):
        pos = i * (n - 1) / (count - 1)
        low = int(pos)
        high = min(low + 1, n - 1)
        frac = pos - low
        out.append(float(values[low]) * (1.0 - frac) + float(values[high]) * frac)
    return out


class Waveform:
    """The bar heights of the card's waveform.

    One model, three behaviours:

    * while the microphone is open the bars follow the real amplitude the
      application measured — the history of levels, resampled across the bars;
    * while something is being transcribed or translated they carry a quiet
      travelling wave, so the card reads as busy without pretending that the
      microphone is still live;
    * when the work is done they settle to a flat line.

    The heights are smoothed on the way, so the bars glide instead of jumping.
    """

    #: Radians per second the travelling wave advances.
    PHASE_RATE = 4.2
    #: How fast a bar rises, and how fast it falls back.
    RISE = 16.0
    FALL = 7.0
    #: The height a settled bar keeps.
    SETTLED = 0.06
    #: The quiet floor the bars never fall below while the microphone is open.
    FLOOR = 0.08

    def __init__(
        self,
        metrics: HudMetrics,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.metrics = metrics
        self._clock = clock
        self._heights: list[float] = [0.0] * metrics.wave_bars
        self._phase = 0.0
        self._last = clock()
        self._gain = AutoGain(min_gain=4.0, noise_floor=0.002)
        #: When the desktop (or the user) asks for less motion, the bars jump
        #: to their target instead of gliding there.
        self.animated = True

    @property
    def heights(self) -> list[float]:
        """The bar heights of this frame, ``0..1``."""
        return self._heights

    def reset(self) -> None:
        """Forget everything: the next session starts from silence."""
        self._heights = [0.0] * len(self._heights)
        self._gain.reset()

    def update(
        self,
        state: HudState,
        level: float = 0.0,
        history: Sequence[float] | None = None,
        dt: float | None = None,
    ) -> None:
        """Advance the waveform by one frame."""
        if dt is None:
            now = self._clock()
            dt = min(0.1, max(0.0, now - self._last))
            self._last = now
        else:
            dt = min(0.1, max(0.0, dt))
        if not self.animated:
            dt = 0.0
        self._phase = (self._phase + dt * self.PHASE_RATE) % (2 * math.pi)

        if state == HudState.LISTENING:
            target = self._audio_target(level, history)
        elif state in BUSY_STATES:
            target = self._activity_target()
        elif state == HudState.TRANSLATED:
            target = [self.SETTLED] * len(self._heights)
        else:
            target = [0.0] * len(self._heights)

        if not self.animated:
            self._heights = list(target)
            return

        rise = 1.0 - math.exp(-dt * self.RISE)
        fall = 1.0 - math.exp(-dt * self.FALL)
        heights = self._heights
        for index, value in enumerate(target):
            blend = rise if value > heights[index] else fall
            heights[index] += (value - heights[index]) * blend

    def _audio_target(self, level: float, history: Sequence[float] | None) -> list[float]:
        """The bar heights the microphone asks for."""
        values = [float(v) for v in history] if history else []
        if not values:
            values = [float(level)]
        peak = max(values) if values else 0.0
        gain = self._gain.update(peak)
        scaled = [min(1.0, v * gain) for v in values]
        bars = _resample(scaled, len(self._heights))
        # A whisper of motion under the bars: a silent room should look calm,
        # not broken.
        floor = self.FLOOR * self._organic()
        return [max(value, floor * (0.6 + 0.4 * index / max(1, len(bars) - 1))) for index, value in enumerate(bars)]

    def _activity_target(self) -> list[float]:
        """A quiet travelling wave: the card is working, not listening."""
        count = len(self._heights)
        out: list[float] = []
        for index in range(count):
            position = index / max(1, count - 1)
            wave = 0.5 + 0.5 * math.sin(self._phase - position * 3.4)
            out.append(0.10 + 0.34 * wave)
        return out

    def _organic(self) -> float:
        """A slow, natural movement used as the floor of the live bars."""
        return 0.5 + 0.5 * math.sin(self._phase * 0.37)


def rounded_rect(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    """Append a rounded rectangle to the current Cairo path.

    The radius is clamped so it can never exceed half of the shorter side,
    which would otherwise draw a self-intersecting path.
    """
    radius = min(radius, width / 2.0, height / 2.0)
    if radius <= 0:
        cr.rectangle(x, y, width, height)
        return
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_card(
    cr,
    x: float,
    y: float,
    width: float,
    height: float,
    radius: float,
    palette: Palette,
    opacity: float = 1.0,
) -> None:
    """Paint the card: a soft shadow, the body, and a hairline border."""
    # The shadow is three stacked shapes: each one a little wider and a
    # little fainter than the last, which reads as a soft blur without a
    # single blurred surface.
    for spread, alpha in ((8.0, 0.08), (5.0, 0.10), (2.5, 0.12)):
        rounded_rect(
            cr,
            x - spread,
            y - spread * 0.5 + 3.0,
            width + spread * 2.0,
            height + spread,
            radius + spread,
        )
        cr.set_source_rgba(0.0, 0.0, 0.0, alpha * opacity)
        cr.fill()

    rounded_rect(cr, x, y, width, height, radius)
    cr.set_source_rgba(*palette.background)
    cr.fill_preserve()
    cr.set_source_rgba(*palette.border)
    cr.set_line_width(1.0)
    cr.stroke()


def draw_waveform(
    cr,
    rect: tuple[float, float, float, float],
    heights: Sequence[float],
    color: tuple,
    metrics: HudMetrics,
    opacity: float = 1.0,
) -> None:
    """Paint the bars of the waveform."""
    x, y, width, height = rect
    if height <= 0.0 or not heights:
        return
    count = len(heights)
    bar_width = metrics.wave_bar_width
    gap = metrics.wave_gap_x
    total = count * bar_width + (count - 1) * gap
    start = x + (width - total) / 2.0
    center = y + height / 2.0

    for index, value in enumerate(heights):
        level = clamp(float(value), 0.0, 1.0)
        bar_height = max(metrics.wave_min_height, level * height)
        bar_x = start + index * (bar_width + gap)
        rounded_rect(cr, bar_x, center - bar_height / 2.0, bar_width, bar_height, bar_width / 2.0)
        cr.set_source_rgba(color[0], color[1], color[2], opacity * (0.55 + 0.45 * level))
        cr.fill()
