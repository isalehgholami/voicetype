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
        else:
            # the library must be loaded before libwayland, else the layer-shell global is
            # never bound and the overlay silently never appears
            try:
                from .overlay import preload_layer_shell

                lib = preload_layer_shell()
                out.append(Check("layer-shell library preloaded", bool(lib), lib or "libgtk4-layer-shell.so not found", required=False))
                import gi

                gi.require_version("Gtk", "4.0")
                gi.require_version("Gtk4LayerShell", "1.0")
                from gi.repository import Gtk, Gtk4LayerShell

                Gtk.init()
                supported = Gtk4LayerShell.is_supported()
                out.append(
                    Check(
                        "compositor supports layer-shell",
                        supported,
                        "compositor does not implement zwlr_layer_shell_v1" if not supported else "",
                        required=False,
                    )
                )
            except Exception as e:  # noqa: BLE001
                out.append(Check("compositor supports layer-shell", False, str(e), required=False))

        # the font the overlay would draw with
        try:
            from .config import OverlayConfig
            from .overlay import resolve_overlay_font

            family, size = resolve_overlay_font(cfg.overlay if cfg is not None else OverlayConfig())
            out.append(
                Check(
                    "overlay font",
                    True,
                    f"{family or 'system default'} {size:g}pt",
                    required=False,
                )
            )
        except Exception as e:  # noqa: BLE001 - a nicety
            out.append(Check("overlay font", False, str(e), required=False))

    # offline engines
    out.append(Check("vosk installed (offline STT)", has_module("vosk"), "pip install vosk", required=False))
    models = installed()
    out.append(Check("vosk model installed", bool(models), ", ".join(models) or "voicetype models install fa-small", required=False))
    out.append(Check("faster-whisper installed", has_module("faster_whisper"), "pip install faster-whisper", required=False))

    # translation
    has_services = has_module("translators")
    out.append(
        Check(
            "translators installed (translation)",
            has_services,
            "" if has_services else "pip install translators   (or: pip install 'voicetype[translate]')",
            required=False,
        )
    )
    has_googletrans = has_module("googletrans")
    out.append(
        Check(
            "googletrans installed (translation fallback)",
            has_googletrans,
            "" if has_googletrans else "pip install googletrans   (or: pip install 'voicetype[translate]')",
            required=False,
        )
    )
    if cfg is not None and cfg.translate.enabled:
        out.append(Check("translation backend", True, cfg.translate.backend))
        try:
            from .translate import check_translate_config, resolve_service_language

            check_translate_config(cfg.translate)
            code = resolve_service_language(cfg.translate.language)
            out.append(Check("translation language", True, f"{cfg.translate.language} → {code}"))
        except ConfigError as e:
            out.append(Check("translation language", False, str(e)))
        # the packages can be installed and still be blocked — a real request
        # is the only way to know
        try:
            from .translate import probe_services

            ok, detail = probe_services()
            out.append(
                Check(
                    "translation services answer",
                    ok,
                    detail,
                    required=False,
                )
            )
        except Exception as e:  # noqa: BLE001 - a diagnostic must never crash
            out.append(Check("translation services answer", False, str(e), required=False))

    # network / proxy
    try:
        from .network import ProxyError, effective_proxy

        spec = effective_proxy(cfg.network.proxy if cfg else "")
        if spec is None:
            has_env = any(os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"))
            out.append(Check("proxy", True, "from environment variables" if has_env else "none (direct)", required=False))
        elif spec.is_socks and not has_module("socks"):
            out.append(Check("proxy", False, f"{spec.redacted()} needs PySocks: pip install PySocks"))
        else:
            out.append(Check("proxy", True, f"{spec.redacted()} — test with: voicetype net-test", required=False))
    except ProxyError as e:
        out.append(Check("proxy", False, str(e)))

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
