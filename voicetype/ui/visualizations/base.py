"""Base class and shared helpers for the overlay visualizations.

A visualization is a pure-Cairo renderer: it receives a context and a size and
paints. It never imports GTK, and it never imports numpy — the port from
hyprwhspr replaced the array maths with plain Python lists so VoiceType keeps
its "no heavy dependencies" promise (ADR-0001).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence

from ..animations import StateManager, VisualizerState
from ..theme import TOKYO_NIGHT, Palette

__all__ = ["BaseVisualization", "rounded_rect", "clamp"]

#: Cairo's toy font API constants, duplicated so this module stays importable
#: without ``pycairo`` installed. They are stable ABI values.
_FONT_SLANT_NORMAL = 0
_FONT_WEIGHT_NORMAL = 0


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """Constrain ``value`` to ``[minimum, maximum]``."""
    return max(minimum, min(maximum, value))


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


class BaseVisualization(ABC):
    """Abstract base for the audio visualizations.

    Subclasses implement :meth:`draw`; :meth:`draw_background` is optional
    and paints the surface behind the visualization.
    """

    #: A short identifier used in configuration and logs.
    style = "base"

    def __init__(self, palette: Palette | None = None, animated: bool = True) -> None:
        self.palette = palette or TOKYO_NIGHT
        self.audio_level = 0.0
        self.audio_samples: list[float] = []
        #: Scales the card the visualization is drawn on (``overlay.opacity``).
        #: The waveform and the VU meter fill their surface with the palette's
        #: background colour, whose alpha the overlay already scales; the pill
        #: paints its own near-black body and needs this factor for the same
        #: effect.
        self.surface_opacity = 1.0
        self.state_manager = StateManager(self.palette, animated=animated)

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> VisualizerState:
        return self.state_manager.current_state

    def set_state(self, state: str) -> bool:
        """Switch the visual state from its string form."""
        return self.state_manager.set_state_from_string(state)

    def set_palette(self, palette: Palette) -> None:
        """Swap the palette (used when the user edits their theme file)."""
        self.palette = palette
        self.state_manager.palette = palette

    def set_surface_opacity(self, opacity: float) -> None:
        """Scale the card's opacity (``overlay.opacity``).

        0.0 makes the card fully transparent, 1.0 leaves it as the palette
        painted it.
        """
        self.surface_opacity = clamp(float(opacity))

    def set_animated(self, animated: bool) -> None:
        """Turn the state animations on or off (``overlay.animations``)."""
        self.state_manager.set_animated(animated)

    # ------------------------------------------------------------------ data
    def update(self, level: float, samples: Sequence[float] | None = None) -> None:
        """Feed a new audio frame.

        ``level`` is a ``0..1`` amplitude and ``samples`` is an optional
        sequence of raw PCM amplitudes used by the waveform.  An empty
        sequence clears the buffer — that is how the overlay silences the
        meter when ``overlay.show_audio_meter`` is off.
        """
        self.audio_level = clamp(float(level))
        if samples is not None:
            self.audio_samples = [float(s) for s in samples]
        self.state_manager.update()

    # ------------------------------------------------------------------ paint
    @abstractmethod
    def draw(self, cr, width: int, height: int) -> None:
        """Paint the visualization onto the Cairo context."""

    def draw_background(self, cr, width: int, height: int) -> None:
        """Paint the surface behind the visualization.

        The default implementation paints nothing so the overlay can host a
        floating shape (the pill) on a transparent surface. Subclasses that
        need a backdrop override this.
        """
        return None

    def draw_state_dot(self, cr, x: float, center_y: float, radius: float = 6.0) -> None:
        """Draw the pulsing state indicator dot (record / pause / error …)."""
        pulse = self.state_manager.get_animation_value()
        if pulse <= 0.001:
            return
        color = self.state_manager.get_state_color()
        cr.set_source_rgba(color[0], color[1], color[2], 0.30 * pulse)
        cr.arc(x, center_y, radius + 3, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(color[0], color[1], color[2], pulse)
        cr.arc(x, center_y, radius, 0, 2 * math.pi)
        cr.fill()
