"""Optional evdev hotkey listener (hold-to-talk + Esc). Default hotkey path is a KDE Custom
Shortcut running ``voicetype toggle`` (docs/adr/0004). Needs group ``input`` and ``pip install evdev``."""

from __future__ import annotations

import selectors
import threading
from collections.abc import Callable
from dataclasses import dataclass

from .log import get_logger

log = get_logger("hotkey")

_MODIFIERS = {
    "super": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "meta": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "alt": ("KEY_LEFTALT", "KEY_RIGHTALT"),
    "ctrl": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "control": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "shift": ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"),
}
_ALIASES = {"esc": "ESC", "escape": "ESC", "return": "ENTER", "enter": "ENTER", "space": "SPACE", "tab": "TAB"}


class ComboError(ValueError):
    pass


@dataclass(frozen=True)
class Combo:
    """Each element of ``groups`` is a set of key names where *any one* satisfies it."""

    groups: tuple[frozenset[str], ...]

    def satisfied_by(self, held: set[str]) -> bool:
        return all(g & held for g in self.groups)


def parse_combo(text: str) -> Combo:
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ComboError("empty hotkey combo")
    *mods, key = parts
    groups: list[frozenset[str]] = []
    for m in mods:
        if m not in _MODIFIERS:
            raise ComboError(f"unknown modifier '{m}' in '{text}' (use super/alt/ctrl/shift)")
        groups.append(frozenset(_MODIFIERS[m]))
    if key in _MODIFIERS:
        raise ComboError(f"hotkey '{text}' needs a non-modifier key")
    name = _ALIASES.get(key, key.upper())
    groups.append(frozenset({f"KEY_{name}"}))
    return Combo(tuple(groups))


class ComboTracker:
    """Pure state machine: feed key events, get press/release transitions of a combo."""

    def __init__(self, combo: Combo) -> None:
        self.combo = combo
        self.held: set[str] = set()
        self.active = False

    def event(self, key: str, down: bool) -> str | None:
        (self.held.add if down else self.held.discard)(key)
        now = self.combo.satisfied_by(self.held)
        if now and not self.active:
            self.active = True
            return "press"
        if not now and self.active:
            self.active = False
            return "release"
        return None


class EvdevHotkeyListener:
    def __init__(
        self,
        combo: str,
        mode: str,
        on_toggle: Callable[[], None],
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_cancel: Callable[[], None],
        cancel_keys: tuple[str, ...] = ("esc",),
    ) -> None:
        self.tracker = ComboTracker(parse_combo(combo))
        self.mode = mode
        self.on_toggle, self.on_start, self.on_stop, self.on_cancel = on_toggle, on_start, on_stop, on_cancel
        self.cancel_names = {f"KEY_{_ALIASES.get(k.lower(), k.upper())}" for k in cancel_keys}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def handle(self, key: str, down: bool) -> None:
        """Process one key event (also used directly by tests)."""
        if down and key in self.cancel_names:
            self.on_cancel()
        edge = self.tracker.event(key, down)
        if edge == "press":
            (self.on_start if self.mode == "hold" else self.on_toggle)()
        elif edge == "release" and self.mode == "hold":
            self.on_stop()

    def start(self) -> None:
        try:
            import evdev
            from evdev import ecodes
        except ImportError as e:
            raise RuntimeError("evdev hotkey needs: pip install evdev") from e
        devices = []
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
            except PermissionError as e:
                raise RuntimeError("cannot read /dev/input — add yourself to the 'input' group and re-login") from e
            if ecodes.EV_KEY in d.capabilities() and ecodes.KEY_A in d.capabilities()[ecodes.EV_KEY]:
                devices.append(d)
        if not devices:
            raise RuntimeError("no keyboard devices found")
        names = {v: k for k, v in ecodes.ecodes.items() if isinstance(k, str) and k.startswith("KEY_")}
        sel = selectors.DefaultSelector()
        for d in devices:
            sel.register(d, selectors.EVENT_READ)

        def loop() -> None:
            while not self._stop.is_set():
                for key, _ in sel.select(timeout=0.5):
                    try:
                        for ev in key.fileobj.read():  # type: ignore[union-attr]
                            if ev.type == ecodes.EV_KEY and ev.value in (0, 1):
                                name = names.get(ev.code)
                                if isinstance(name, list):
                                    name = name[0]
                                if name:
                                    self.handle(name, ev.value == 1)
                    except OSError:
                        sel.unregister(key.fileobj)

        self._thread = threading.Thread(target=loop, daemon=True, name="hotkey")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(1.0)
