"""Overlay visualizations.

* :class:`~voicetype.ui.visualizations.pill.PillVisualization` — the compact
  floating pill, the default.
* :class:`~voicetype.ui.visualizations.waveform.WaveformVisualization` — a
  bar-style waveform with a state dot.
* :class:`~voicetype.ui.visualizations.vu_meter.VUMeterVisualization` — a
  horizontal level meter with a peak hold.

All three are pure Cairo and import-safe without GTK.
"""

from __future__ import annotations

from .base import BaseVisualization
from .pill import PillVisualization
from .vu_meter import VUMeterVisualization
from .waveform import WaveformVisualization

#: Maps the ``overlay.style`` config value to its implementation.
STYLES: dict[str, type[BaseVisualization]] = {
    "pill": PillVisualization,
    "waveform": WaveformVisualization,
    "vu_meter": VUMeterVisualization,
}

#: The style used when the configuration names an unknown one.
DEFAULT_STYLE = "pill"

__all__ = [
    "BaseVisualization",
    "PillVisualization",
    "VUMeterVisualization",
    "WaveformVisualization",
    "STYLES",
    "DEFAULT_STYLE",
    "create_visualization",
]


def create_visualization(style: str, palette=None) -> BaseVisualization:
    """Build the visualization named by ``style``.

    Unknown styles fall back to the pill rather than raising: a typo in the
    config must not take the overlay down.
    """
    cls = STYLES.get((style or "").strip().lower(), STYLES[DEFAULT_STYLE])
    return cls(palette)
