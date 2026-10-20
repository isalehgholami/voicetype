"""`voicetype doctor`: environment checklist."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass

from .config import ConfigError, default_config_path, load_config
from .injector import InjectionError, Tools, choose_backend, detect_session
from .models import installed


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    required: bool = True  # False → shown as a warning, not a failure

    def line(self) -> str:
        mark = "✓" if self.ok else ("✗" if self.required else "!")
        return f"[{mark}] {self.name}" + (f" — {self.detail}" if self.detail else "")


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _user_groups() -> set[str]:
    try:
        import grp  # POSIX only

        return {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except (ImportError, AttributeError, KeyError):
        return set()


def run_checks(
    which: Callable[[str], str | None] = shutil.which,
    env: dict[str, str] | None = None,
    has_module: Callable[[str], bool] = _has_module,
    groups: Callable[[], set[str]] = _user_groups,
    path_exists: Callable[[str], bool] = os.path.exists,
    access: Callable[[str, int], bool] = os.access,
) -> list[Check]:
    env = dict(os.environ) if env is None else env
    out: list[Check] = []
    out.append(Check("Python ≥ 3.11", sys.version_info >= (3, 11), sys.version.split()[0]))

    session = detect_session(env)
    out.append(Check("graphical session", session in ("wayland", "x11"), session))

    # config
    try:
        cfg = load_config()
        out.append(Check("config valid", True, str(default_config_path())))
    except ConfigError as e:
        cfg = None
        out.append(Check("config valid", False, str(e)))

    # text injection
    tools = Tools.detect(which)
    try:
        backend = choose_backend(session, tools, cfg.injector.backend if cfg else "auto")
        out.append(Check("text injection backend", True, backend))
    except InjectionError as e:
        out.append(Check("text injection backend", False, str(e)))
    if session == "wayland":
        out.append(Check("wl-clipboard (wl-copy)", tools.wl_copy, "pacman -S wl-clipboard" if not tools.wl_copy else ""))
        out.append(Check("ydotool", tools.ydotool, "pacman -S ydotool" if not tools.ydotool else ""))
        if tools.ydotool:
            sock = env.get("YDOTOOL_SOCKET") or f"{env.get('XDG_RUNTIME_DIR', '/run/user/1000')}/.ydotool_socket"
            ok = path_exists(sock) or path_exists("/tmp/.ydotool_socket")
            out.append(Check("ydotoold running", ok, "systemctl --user enable --now ydotool" if not ok else sock, required=False))
        uinput_ok = path_exists("/dev/uinput") and access("/dev/uinput", os.W_OK)
        out.append(Check("/dev/uinput writable", uinput_ok, "needed by ydotool (add udev rule / group 'input')" if not uinput_ok else "", required=False))
    g = groups()
    out.append(Check("user in group 'input'", "input" in g, "needed only for hold-mode hotkey and ydotool", required=False))
    out.append(Check("user in group 'audio'", "audio" in g, "", required=False))

    # audio
    if has_module("sounddevice"):
        try:
            import sounddevice as sd

            dev = sd.query_devices(kind="input")
            out.append(Check("microphone", True, dev["name"]))
        except Exception as e:  # noqa: BLE001
            out.append(Check("microphone", False, str(e)))
    else:
        out.append(Check("sounddevice installed", False, "pip install sounddevice"))
    out.append(Check("SpeechRecognition installed", has_module("speech_recognition"), "pip install SpeechRecognition"))

    # overlay
    gi_ok = has_module("gi")
    out.append(Check("python-gobject (gi)", gi_ok, "pacman -S python-gobject" if not gi_ok else "", required=False))
    if gi_ok:
        try:
            import gi

            gi.require_version("Gtk4LayerShell", "1.0")
            out.append(Check("gtk4-layer-shell", True))
        except (ValueError, ImportError) as e:
            out.append(Check("gtk4-layer-shell", False, f"pacman -S gtk4-layer-shell ({e})", required=False))

    # offline engines
    out.append(Check("vosk installed (offline STT)", has_module("vosk"), "pip install vosk", required=False))
    models = installed()
    out.append(Check("vosk model installed", bool(models), ", ".join(models) or "voicetype models install fa-small", required=False))
    out.append(Check("faster-whisper installed", has_module("faster_whisper"), "pip install faster-whisper", required=False))

    # daemon
    try:
        from .control import send_command

        reply = send_command("status")
        out.append(Check("daemon running", True, reply))
    except ConnectionError:
        out.append(Check("daemon running", False, "start with: systemctl --user start voicetype", required=False))
    except Exception as e:  # noqa: BLE001
        out.append(Check("daemon running", False, str(e), required=False))
    return out


def report(checks: list[Check]) -> tuple[str, int]:
    text = "\n".join(c.line() for c in checks)
    failed = sum(1 for c in checks if not c.ok and c.required)
    return text, (1 if failed else 0)
