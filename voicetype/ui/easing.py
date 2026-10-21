"""Easing curves and small math helpers shared by the overlay animations.

Pure functions only: no GTK, no cairo, no numpy. Every curve takes and returns
plain floats so the animation maths can be unit-tested without a compositor.
"""

from __future__ import annotations

__all__ = [
    "clamp",
    "lerp",
    "ease_out_cubic",
    "ease_in_out",
    "ease_out_quad",
    "ease_out_back",
    "progress",
    "mix_rgba",
]


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Constrain ``value`` to ``[minimum, maximum]``."""
    return max(minimum, min(maximum, value))


def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between ``a`` and ``b``."""
    return a + (b - a) * t


def ease_out_cubic(value: float) -> float:
    """Decelerating curve; fast start, soft landing. Used for word entrances."""
    value = clamp(value, 0.0, 1.0)
    return 1.0 - (1.0 - value) ** 3


def ease_in_out(value: float) -> float:
    """Symmetric smoothstep. Used for word exits."""
    value = clamp(value, 0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def ease_out_quad(value: float) -> float:
    """Quadratic decelerating curve."""
    value = clamp(value, 0.0, 1.0)
    return 1.0 - (1.0 - value) ** 2


def ease_out_back(value: float) -> float:
    """Slight overshoot; used for the overlay's appear transition."""
    value = clamp(value, 0.0, 1.0)
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (value - 1.0) ** 3 + c1 * (value - 1.0) ** 2


def progress(elapsed: float, duration: float, delay: float = 0.0) -> float:
    """Normalised ``[0, 1]`` progress of an animation ``duration`` long.

    ``delay`` shifts the start, which is how the staggered per-word
    entrance/exit timings are expressed.
    """
    if duration <= 0.0:
        return 1.0
    return clamp((elapsed - delay) / duration, 0.0, 1.0)


def mix_rgba(a, b, t: float):
    """Blend two RGBA tuples; ``t=0`` returns ``a``, ``t=1`` returns ``b``."""
    t = clamp(t, 0.0, 1.0)
    return tuple(lerp(a[i], b[i], t) for i in range(4))
