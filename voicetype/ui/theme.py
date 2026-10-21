"""Theme tokens for the VoiceType overlay.

The default palette is *nocturne*: the near-black card with the violet
undertone the floating HUD was designed around (ADR-0008).  Every other
palette — Tokyo Night and the rest — is still there and still works; the
palette only decides the colours, never the layout.

A theme can be selected by name (``overlay.theme = "nocturne"``) or by
pointing at a CSS file that uses GTK's ``@define-color`` syntax, e.g.::

    @define-color background-color #1a1b26;
    @define-color border-color     #2a2e42;
    @define-color accent-color     #7aa2f7;
    @define-color text-color       #c0caf5;

Only the keys listed in :data:`CSS_COLOR_KEYS` are honoured; anything else is
ignored so a desktop theme file can be reused verbatim.

Every colour is an RGBA tuple of floats in ``0.0..1.0`` — the representation
Cairo expects — so no conversion happens on the hot drawing path.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

__all__ = [
    "Palette",
    "PALETTES",
    "PALETTE_NAMES",
    "DEFAULT_PALETTE",
    "Theme",
    "ThemeWatcher",
    "hex_to_rgba",
    "parse_css_colors",
    "palette_from_css",
    "resolve_palette",
]

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3,8})$")
_DEFINE_COLOR_RE = re.compile(r"@define-color\s+([\w-]+)\s+(#[0-9a-fA-F]{3,8})\s*;")


@dataclass(frozen=True)
class Palette:
    """An immutable set of overlay colours."""

    background: tuple = (0.035, 0.035, 0.047, 0.96)
    border: tuple = (0.165, 0.180, 0.259, 0.55)
    text: tuple = (0.753, 0.792, 0.961, 1.0)
    text_dim: tuple = (0.337, 0.373, 0.537, 1.0)
    accent: tuple = (0.478, 0.635, 0.969, 1.0)
    accent_alt: tuple = (0.490, 0.812, 1.0, 1.0)
    recording: tuple = (0.969, 0.463, 0.557, 1.0)
    paused: tuple = (0.878, 0.686, 0.408, 1.0)
    processing: tuple = (0.620, 0.808, 0.416, 1.0)
    error: tuple = (0.969, 0.463, 0.557, 1.0)
    success: tuple = (0.620, 0.808, 0.416, 1.0)
    bar_left: tuple = (0.478, 0.635, 0.969, 1.0)
    bar_right: tuple = (0.490, 0.812, 1.0, 1.0)
    shadow: tuple = (0.0, 0.0, 0.0, 0.34)

    def with_alpha(self, alpha: float) -> Palette:
        """Return a copy with every colour's alpha replaced by ``alpha``."""
        return replace(
            self,
            **{
                name: (c[0], c[1], c[2], alpha)
                for name, c in (
                    (f, getattr(self, f)) for f in self.__dataclass_fields__
                )
            },
        )


# --------------------------------------------------------------------------
# Built-in palettes
# --------------------------------------------------------------------------
# Tokyo Night (hyprwhspr's palette) -- the default.
TOKYO_NIGHT = Palette(
    background=(0.035, 0.035, 0.047, 0.96),
    border=(0.165, 0.180, 0.259, 0.70),
    text=(0.753, 0.792, 0.961, 1.0),
    text_dim=(0.337, 0.373, 0.537, 1.0),
    accent=(0.478, 0.635, 0.969, 1.0),
    accent_alt=(0.490, 0.812, 1.0, 1.0),
    recording=(0.969, 0.463, 0.557, 1.0),
    paused=(0.878, 0.686, 0.408, 1.0),
    processing=(0.620, 0.808, 0.416, 1.0),
    error=(0.969, 0.463, 0.557, 1.0),
    success=(0.620, 0.808, 0.416, 1.0),
    bar_left=(0.478, 0.635, 0.969, 1.0),
    bar_right=(0.490, 0.812, 1.0, 1.0),
    shadow=(0.0, 0.0, 0.0, 0.34),
)

TOKYO_NIGHT_LIGHT = Palette(
    background=(0.960, 0.965, 0.980, 0.96),
    border=(0.180, 0.200, 0.290, 0.45),
    text=(0.180, 0.196, 0.286, 1.0),
    text_dim=(0.420, 0.451, 0.549, 1.0),
    accent=(0.290, 0.482, 0.847, 1.0),
    accent_alt=(0.098, 0.522, 0.702, 1.0),
    recording=(0.843, 0.259, 0.365, 1.0),
    paused=(0.741, 0.494, 0.086, 1.0),
    processing=(0.353, 0.588, 0.180, 1.0),
    error=(0.843, 0.259, 0.365, 1.0),
    success=(0.353, 0.588, 0.180, 1.0),
    bar_left=(0.290, 0.482, 0.847, 1.0),
    bar_right=(0.098, 0.522, 0.702, 1.0),
    shadow=(0.20, 0.22, 0.30, 0.18),
)

CATPPUCCIN_MOCHA = Palette(
    background=(0.094, 0.082, 0.125, 0.96),
    border=(0.427, 0.427, 0.541, 0.60),
    text=(0.925, 0.914, 0.937, 1.0),
    text_dim=(0.541, 0.537, 0.588, 1.0),
    accent=(0.682, 0.788, 0.867, 1.0),
    accent_alt=(0.553, 0.827, 0.831, 1.0),
    recording=(0.969, 0.443, 0.510, 1.0),
    paused=(0.988, 0.749, 0.443, 1.0),
    processing=(0.702, 0.902, 0.616, 1.0),
    error=(0.969, 0.443, 0.510, 1.0),
    success=(0.702, 0.902, 0.616, 1.0),
    bar_left=(0.682, 0.788, 0.867, 1.0),
    bar_right=(0.553, 0.827, 0.831, 1.0),
    shadow=(0.0, 0.0, 0.0, 0.34),
)

ROSE_PINE = Palette(
    background=(0.098, 0.106, 0.141, 0.96),
    border=(0.267, 0.278, 0.325, 0.65),
    text=(0.898, 0.898, 0.906, 1.0),
    text_dim=(0.518, 0.518, 0.573, 1.0),
    accent=(0.404, 0.647, 0.769, 1.0),
    accent_alt=(0.702, 0.651, 0.839, 1.0),
    recording=(0.937, 0.529, 0.588, 1.0),
    paused=(0.980, 0.702, 0.349, 1.0),
    processing=(0.596, 0.788, 0.596, 1.0),
    error=(0.937, 0.529, 0.588, 1.0),
    success=(0.596, 0.788, 0.596, 1.0),
    bar_left=(0.404, 0.647, 0.769, 1.0),
    bar_right=(0.702, 0.651, 0.839, 1.0),
    shadow=(0.0, 0.0, 0.0, 0.34),
)

NORD = Palette(
    background=(0.078, 0.106, 0.149, 0.96),
    border=(0.259, 0.322, 0.392, 0.65),
    text=(0.871, 0.910, 0.918, 1.0),
    text_dim=(0.478, 0.533, 0.596, 1.0),
    accent=(0.639, 0.745, 0.867, 1.0),
    accent_alt=(0.576, 0.867, 0.827, 1.0),
    recording=(0.902, 0.451, 0.451, 1.0),
    paused=(0.898, 0.741, 0.494, 1.0),
    processing=(0.596, 0.788, 0.596, 1.0),
    error=(0.902, 0.451, 0.451, 1.0),
    success=(0.596, 0.788, 0.596, 1.0),
    bar_left=(0.639, 0.745, 0.867, 1.0),
    bar_right=(0.576, 0.867, 0.827, 1.0),
    shadow=(0.0, 0.0, 0.0, 0.34),
)

GRUVBOX_DARK = Palette(
    background=(0.157, 0.145, 0.129, 0.96),
    border=(0.290, 0.271, 0.239, 0.70),
    text=(0.831, 0.796, 0.686, 1.0),
    text_dim=(0.533, 0.478, 0.400, 1.0),
    accent=(0.686, 0.733, 0.298, 1.0),
    accent_alt=(0.416, 0.643, 0.710, 1.0),
    recording=(0.722, 0.216, 0.216, 1.0),
    paused=(0.827, 0.596, 0.239, 1.0),
    processing=(0.596, 0.698, 0.259, 1.0),
    error=(0.722, 0.216, 0.216, 1.0),
    success=(0.596, 0.698, 0.259, 1.0),
    bar_left=(0.686, 0.733, 0.298, 1.0),
    bar_right=(0.416, 0.643, 0.710, 1.0),
    shadow=(0.0, 0.0, 0.0, 0.34),
)

DRACULA = Palette(
    background=(0.098, 0.098, 0.129, 0.96),
    border=(0.267, 0.278, 0.400, 0.65),
    text=(0.949, 0.949, 0.980, 1.0),
    text_dim=(0.627, 0.627, 0.686, 1.0),
    accent=(0.627, 0.443, 1.0, 1.0),
    accent_alt=(0.251, 0.878, 0.816, 1.0),
    recording=(1.0, 0.290, 0.396, 1.0),
    paused=(0.996, 0.749, 0.239, 1.0),
    processing=(0.313, 0.980, 0.588, 1.0),
    error=(1.0, 0.290, 0.396, 1.0),
    success=(0.313, 0.980, 0.588, 1.0),
    bar_left=(0.627, 0.443, 1.0, 1.0),
    bar_right=(0.251, 0.878, 0.816, 1.0),
    shadow=(0.0, 0.0, 0.0, 0.34),
)

# Nocturne -- the palette the floating HUD was designed for (ADR-0008).
# A near-black card with a violet undertone, a violet accent for the
# activity, a muted mint for the live state and a hairline white border.
NOCTURNE = Palette(
    background=(0.098, 0.090, 0.125, 0.97),   # #191720
    border=(1.0, 1.0, 1.0, 0.07),            # a white hairline
    text=(0.953, 0.941, 0.976, 1.0),          # #F3F0F9
    text_dim=(0.663, 0.639, 0.710, 1.0),     # #A9A3B5
    accent=(0.651, 0.592, 1.0, 1.0),         # #A697FF -- the violet
    accent_alt=(0.486, 0.420, 0.878, 1.0),   # a deeper violet
    recording=(0.647, 0.910, 0.549, 1.0),    # #A5E88C -- the mint
    paused=(0.878, 0.722, 0.498, 1.0),       # a quiet amber
    processing=(0.651, 0.592, 1.0, 1.0),     # the violet: work is in progress
    error=(0.898, 0.549, 0.549, 1.0),        # a muted rose
    success=(0.647, 0.910, 0.549, 1.0),      # #A5E88C
    bar_left=(0.651, 0.592, 1.0, 1.0),       # the violet
    bar_right=(0.651, 0.592, 1.0, 1.0),      # the violet
    shadow=(0.0, 0.0, 0.0, 0.34),
)

PALETTES: dict[str, Palette] = {
    "nocturne": NOCTURNE,
    "tokyo-night": TOKYO_NIGHT,
    "tokyo-night-light": TOKYO_NIGHT_LIGHT,
    "catppuccin-mocha": CATPPUCCIN_MOCHA,
    "rose-pine": ROSE_PINE,
    "nord": NORD,
    "gruvbox-dark": GRUVBOX_DARK,
    "dracula": DRACULA,
}

#: Backwards-compatible aliases for the pre-1.0 ``overlay.theme`` values.
_ALIASES = {
    "dark": "tokyo-night",
    "light": "tokyo-night-light",
}

PALETTE_NAMES = tuple(sorted(PALETTES))
DEFAULT_PALETTE = "nocturne"

#: Maps a CSS ``@define-color`` key onto a :class:`Palette` field.
CSS_COLOR_KEYS = {
    "background-color": "background",
    "border-color": "border",
    "text-color": "text",
    "text-dim-color": "text_dim",
    "accent-color": "accent",
    "accent-alt-color": "accent_alt",
    "recording-color": "recording",
    "paused-color": "paused",
    "processing-color": "processing",
    "error-color": "error",
    "success-color": "success",
    "bar-color-left": "bar_left",
    "bar-color-right": "bar_right",
    "shadow-color": "shadow",
}


def hex_to_rgba(value: str, alpha: float | None = None) -> tuple:
    """Convert ``#rgb`` / ``#rrggbb`` / ``#rrggbbaa`` to an RGBA float tuple."""
    raw = value.strip()
    match = _HEX_RE.match(raw)
    if match is None:
        raise ValueError(f"invalid hex colour: {value!r}")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    elif len(digits) == 4:
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) not in (6, 8):
        raise ValueError(f"invalid hex colour: {value!r}")
    r = int(digits[0:2], 16) / 255.0
    g = int(digits[2:4], 16) / 255.0
    b = int(digits[4:6], 16) / 255.0
    a = int(digits[6:8], 16) / 255.0 if len(digits) == 8 else 1.0
    if alpha is not None:
        a = alpha
    return (r, g, b, a)


def parse_css_colors(path: os.PathLike | str) -> dict:
    """Extract ``@define-color name #hex;`` declarations from a CSS file.

    Returns a mapping of colour name to an RGBA tuple. Unreadable or malformed
    files yield an empty mapping rather than raising: a broken theme must never
    take the overlay down.
    """
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    colors: dict = {}
    for name, value in _DEFINE_COLOR_RE.findall(content):
        try:
            colors[name] = hex_to_rgba(value)
        except ValueError:
            continue
    return colors


def palette_from_css(path: os.PathLike | str, base: Palette | None = None) -> Palette | None:
    """Build a palette from a CSS file, starting from ``base``.

    Returns ``None`` when the file has no usable colour declarations.
    """
    colors = parse_css_colors(path)
    if not colors:
        return None
    overrides = {}
    for css_key, field in CSS_COLOR_KEYS.items():
        value = colors.get(css_key)
        if value is not None:
            overrides[field] = value
    if not overrides:
        return None
    return replace(base or PALETTES[DEFAULT_PALETTE], **overrides)


def resolve_palette(name: str, base: Palette | None = None) -> Palette:
    """Resolve an ``overlay.theme`` value to a palette.

    Accepts a built-in palette name, the legacy ``dark``/``light`` aliases, or
    a path to a CSS file. Unknown names fall back to the default palette.
    """
    key = (name or "").strip()
    if not key:
        return PALETTES[DEFAULT_PALETTE]
    alias = _ALIASES.get(key.lower())
    if alias is not None:
        return PALETTES[alias]
    lowered = key.lower()
    if lowered in PALETTES:
        return PALETTES[lowered]
    candidate = Path(key).expanduser()
    if candidate.suffix == ".css" and candidate.exists():
        return palette_from_css(candidate, base or PALETTES[DEFAULT_PALETTE]) or (
            PALETTES[DEFAULT_PALETTE]
        )
    return PALETTES[DEFAULT_PALETTE]


class Theme:
    """Holds the active palette and can reload it from disk.

    Instances are cheap; the overlay keeps one and calls :meth:`reload` when
    the user edits their theme file.
    """

    def __init__(self, name: str = DEFAULT_PALETTE) -> None:
        self._name = name
        self._palette = resolve_palette(name)

    @property
    def name(self) -> str:
        return self._name

    @property
    def palette(self) -> Palette:
        return self._palette

    def reload(self) -> Palette:
        """Re-resolve the palette from disk and return it."""
        self._palette = resolve_palette(self._name)
        return self._palette

    def set_name(self, name: str) -> Palette:
        """Switch to a different theme and return the new palette."""
        self._name = name
        return self.reload()


class ThemeWatcher:
    """Polls a theme file for changes so edits apply without a daemon restart.

    A1 Hz poll is used rather than inotify: theme engines commonly swap the
    file with an atomic rename, which a file monitor cannot see reliably.
    """

    def __init__(self, path: os.PathLike | str, on_change=None) -> None:
        self.path = Path(path)
        self._on_change = on_change
        # The current state is the baseline: only a *change* is a change.
        self._last_mtime = self._mtime()
        self._timer_id = 0

    def _mtime(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def start(self) -> bool:
        """Begin polling. Returns ``False`` when no GLib main loop is running."""
        from gi.repository import GLib  # noqa: PLC0415 - optional GUI dependency

        self._last_mtime = self._mtime()
        self._timer_id = GLib.timeout_add(1000, self._check)
        return True

    def stop(self) -> None:
        """Stop polling."""
        if self._timer_id:
            try:
                from gi.repository import GLib  # noqa: PLC0415 - optional GUI dependency

                GLib.source_remove(self._timer_id)
            except Exception:  # noqa: BLE001 - shutting down anyway
                pass
            self._timer_id = 0

    def _check(self) -> bool:
        current = self._mtime()
        if current != self._last_mtime:
            self._last_mtime = current
            if self._on_change is not None:
                try:
                    self._on_change()
                except Exception:  # noqa: BLE001 - a broken reload must not kill the poll
                    pass
        return True

    def check_now(self) -> bool:
        """Poll once synchronously. Returns ``True`` when the file changed."""
        current = self._mtime()
        if current == self._last_mtime:
            return False
        self._last_mtime = current
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:  # noqa: BLE001
                pass
        return True
