"""VoiceType UI layer: theme tokens, animation primitives and the floating HUD.

Everything in this package is import-safe without GTK, cairo or numpy: the
toolkit is loaded lazily by :mod:`voicetype.overlay`, and the HUD only ever
*uses* a ``cairo.Context`` handed to it by the caller. That keeps ADR-0001
("the core is free of heavy dependencies and without Qt/GTK") intact and
lets the whole UI layer be unit-tested headlessly.

The visual language is the floating HUD of ADR-0008: one dark card with a
status row, the text that matters and a slim waveform.
"""

from __future__ import annotations

__all__ = ["animations", "easing", "hud", "sounds", "theme", "transcript"]
