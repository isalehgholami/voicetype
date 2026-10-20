"""Text injection. Default on Wayland = clipboard + simulated paste key (docs/adr/0002)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .config import InjectorConfig
from .log import get_logger

log = get_logger("injector")

# Linux evdev keycodes used by `ydotool key`
KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_LEFTALT, KEY_LEFTMETA, KEY_V = 29, 42, 56, 125, 47
KEY_RIGHTCTRL, KEY_RIGHTSHIFT, KEY_RIGHTALT, KEY_RIGHTMETA = 97, 54, 100, 126
ALL_MODIFIERS = (
    KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_LEFTALT, KEY_LEFTMETA,
    KEY_RIGHTCTRL, KEY_RIGHTSHIFT, KEY_RIGHTALT, KEY_RIGHTMETA,
)  # fmt: skip

TERMINAL_CLASSES = {
    "konsole", "org.kde.konsole", "yakuake", "kitty", "alacritty", "foot", "wezterm", "org.wezfurlong.wezterm",
    "gnome-terminal", "gnome-terminal-server", "xterm", "urxvt", "terminator", "tilix", "ghostty",
    "com.mitchellh.ghostty", "st", "st-256color", "xfce4-terminal", "lxterminal",
}  # fmt: skip

_TEXT_TYPES = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "STRING", "TEXT")


class InjectionError(RuntimeError):
    pass


Runner = Callable[..., "subprocess.CompletedProcess"]


def sanitize(text: str, strip: bool = False) -> str:
    """Remove control characters (keep \\n and \\t) so transcribed text can never act as input commands."""
    out = "".join(c for c in text if c in "\n\t" or (c >= " " and c != "\x7f" and not 0x80 <= ord(c) < 0xA0))
    return out.strip() if strip else out


def detect_session(env: dict[str, str] | None = None) -> str:
    """'wayland' | 'x11' | 'unknown'."""
    env = os.environ if env is None else env
    st = env.get("XDG_SESSION_TYPE", "").lower()
    if st in ("wayland", "x11"):
        return st
    if env.get("WAYLAND_DISPLAY"):
        return "wayland"
    if env.get("DISPLAY"):
        return "x11"
    return "unknown"


def _default_run(cmd: Sequence[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("timeout", 10)
    return subprocess.run(list(cmd), **kw)  # noqa: S603


@dataclass
class Tools:
    wl_copy: bool = False
    wl_paste: bool = False
    wtype: bool = False
    ydotool: bool = False
    xdotool: bool = False
    xclip: bool = False
    kdotool: bool = False

    @classmethod
    def detect(cls, which: Callable[[str], str | None] = shutil.which) -> Tools:
        return cls(
            wl_copy=bool(which("wl-copy")),
            wl_paste=bool(which("wl-paste")),
            wtype=bool(which("wtype")),
            ydotool=bool(which("ydotool")),
            xdotool=bool(which("xdotool")),
            xclip=bool(which("xclip")),
            kdotool=bool(which("kdotool")),
        )


def choose_backend(session: str, tools: Tools, requested: str = "auto") -> str:
    """Returns one of paste-wl, paste-x11, wtype, ydotool, xdotool; raises InjectionError."""
    if requested == "paste":
        requested = "auto-paste"
    if requested in ("wtype", "ydotool", "xdotool"):
        if not getattr(tools, requested):
            raise InjectionError(f"backend '{requested}' requested but the tool is not installed")
        return requested
    wayland = session == "wayland"
    if wayland:
        if tools.wl_copy and tools.ydotool:
            return "paste-wl"
        if requested == "auto-paste":
            raise InjectionError("paste needs wl-clipboard and ydotool (and a running ydotoold)")
        if tools.wtype:
            return "wtype"
        if tools.ydotool:
            return "ydotool"
    else:
        if requested == "auto-paste":
            if tools.xdotool and tools.xclip:
                return "paste-x11"
            raise InjectionError("paste on X11 needs xclip and xdotool")
        if tools.xdotool:
            return "xdotool"
    raise InjectionError(
        "no injection backend found — install wl-clipboard + ydotool (Wayland) or xdotool (X11)"
        if wayland
        else "no injection backend found — install xdotool"
    )


class TextInjector:
    def __init__(
        self,
        cfg: InjectorConfig,
        *,
        session: str | None = None,
        tools: Tools | None = None,
        run: Runner = _default_run,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self.session = session or detect_session()
        self.tools = tools or Tools.detect()
        self._run = run
        self._sleep = sleep
        self.backend = choose_backend(self.session, self.tools, cfg.backend)
        log.info("text injector backend: %s (session=%s)", self.backend, self.session)

    # ------------------------------------------------------------------ public
    def inject(self, text: str) -> None:
        text = sanitize(text, self.cfg.strip_whitespace)
        if not text:
            return
        if self.cfg.release_modifiers:
            self._release_modifiers()
        if self.backend == "paste-wl":
            self._paste_wayland(text)
        elif self.backend == "paste-x11":
            self._paste_x11(text)
        elif self.backend == "wtype":
            self._check(self._run(["wtype", "-d", str(self.cfg.typing_delay_ms), "--", text]), "wtype")
        elif self.backend == "ydotool":
            self._check(self._run(["ydotool", "type", "-d", str(self.cfg.typing_delay_ms), "--", text]), "ydotool type")
        elif self.backend == "xdotool":
            self._check(
                self._run(["xdotool", "type", "--clearmodifiers", "--delay", str(self.cfg.typing_delay_ms), "--", text]),
                "xdotool",
            )

    # ------------------------------------------------------------------ helpers
    def _check(self, proc, what: str) -> None:
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            raise InjectionError(f"{what} failed (exit {proc.returncode}): {err}")

    def _release_modifiers(self) -> None:
        """Send key-up for every modifier so a still-held hotkey doesn't turn the paste into Super+Ctrl+V."""
        if self.backend == "paste-wl" or self.backend == "ydotool":
            self._run(["ydotool", "key", *[f"{k}:0" for k in ALL_MODIFIERS]])

    def paste_shortcut(self) -> str:
        want = self.cfg.paste_shortcut
        if want != "auto":
            return want
        if self.tools.kdotool:
            r = self._run(["kdotool", "getactivewindow", "getwindowclassname"])
            if r.returncode == 0 and r.stdout.decode("utf-8", "replace").strip().lower() in TERMINAL_CLASSES:
                return "ctrl+shift+v"
        elif self.session == "x11" and self.tools.xdotool:
            r = self._run(["xdotool", "getactivewindow", "getwindowclassname"])
            if r.returncode == 0 and r.stdout.decode("utf-8", "replace").strip().lower() in TERMINAL_CLASSES:
                return "ctrl+shift+v"
        return "ctrl+v"

    # -- clipboard save/restore -------------------------------------------------
    def _wl_save(self) -> tuple[str, bytes] | None:
        if not (self.cfg.restore_clipboard and self.tools.wl_paste):
            return None
        r = self._run(["wl-paste", "--list-types"])
        if r.returncode != 0:
            return None  # empty clipboard
        types = r.stdout.decode("utf-8", "replace").split()
        if not types:
            return None
        chosen = next((t for t in _TEXT_TYPES if t in types), types[0])
        d = self._run(["wl-paste", "--no-newline", "--type", chosen])
        return (chosen, d.stdout) if d.returncode == 0 else None

    def _wl_restore(self, saved: tuple[str, bytes] | None) -> None:
        if saved is None:
            return
        mime, data = saved
        self._run(["wl-copy", "--type", mime], input=data)

    def _paste_wayland(self, text: str) -> None:
        saved = self._wl_save()
        self._check(self._run(["wl-copy", "--type", "text/plain;charset=utf-8"], input=text.encode("utf-8")), "wl-copy")
        self._sleep(0.05)  # let the compositor propagate the selection
        shortcut = self.paste_shortcut()
        keys = (
            [f"{KEY_LEFTCTRL}:1", f"{KEY_LEFTSHIFT}:1", f"{KEY_V}:1", f"{KEY_V}:0", f"{KEY_LEFTSHIFT}:0", f"{KEY_LEFTCTRL}:0"]
            if shortcut == "ctrl+shift+v"
            else [f"{KEY_LEFTCTRL}:1", f"{KEY_V}:1", f"{KEY_V}:0", f"{KEY_LEFTCTRL}:0"]
        )
        try:
            self._check(self._run(["ydotool", "key", *keys]), "ydotool key (is ydotoold running?)")
        finally:
            self._sleep(0.25)  # the target app reads the clipboard asynchronously
            self._wl_restore(saved)

    def _paste_x11(self, text: str) -> None:
        saved = None
        if self.cfg.restore_clipboard and self.tools.xclip:
            r = self._run(["xclip", "-selection", "clipboard", "-o"])
            saved = r.stdout if r.returncode == 0 else None
        self._check(self._run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8")), "xclip")
        shortcut = self.paste_shortcut()
        self._check(self._run(["xdotool", "key", "--clearmodifiers", shortcut]), "xdotool key")
        self._sleep(0.25)
        if saved is not None:
            self._run(["xclip", "-selection", "clipboard"], input=saved)
