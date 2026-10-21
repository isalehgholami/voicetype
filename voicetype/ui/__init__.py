"""VoiceType UI layer: theme tokens, animation primitives and audio visualizations.

Everything in this package is import-safe without GTK, cairo or numpy: the
toolkit is loaded lazily by :mod:`voicetype.overlay`, and the visualizations
only ever *use* a ``cairo.Context`` handed to them by the caller. That keeps
ADR-0001 ("the core is free of heavy dependencies and without Qt/GTK") intact
and lets the whole UI layer be unit-tested headlessly.

The visual language is adapted from hyprwhspr's ``lib/mic_osd`` (Tokyo Night
palette, pill / waveform / VU-meter visualizations, state-machine animation).
"""

from __future__ import annotations

__all__ = ["animations", "easing", "sounds", "theme", "transcript", "visualizations"]
