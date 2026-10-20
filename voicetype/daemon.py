"""The long-running daemon: wires config, STT, injector, overlay, control socket and signals."""

from __future__ import annotations

import signal
import threading
from pathlib import Path

from . import __version__
from .audio import AudioCapture
from .config import Config, ConfigError, ConfigManager
from .control import ControlServer
from .dispatcher import Components, Dispatcher
from .injector import InjectionError, TextInjector
from .log import get_logger, setup_logging
from .media import MediaPauser
from .notify import Notifier
from .overlay import create_overlay
from .postprocess import Pipeline
from .stt.registry import build_backend

log = get_logger("daemon")


class _BrokenInjector:
    """Keeps the daemon alive when no injection tool is installed; each attempt reports why."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def inject(self, text: str) -> None:
        raise InjectionError(self.reason)


def build_components(cfg: Config, on_fallback=None) -> Components:
    backend = build_backend(cfg, on_fallback)
    try:
        injector = TextInjector(cfg.injector)
    except InjectionError as e:
        log.error("%s", e)
        injector = _BrokenInjector(str(e))
    return Components(cfg, backend, injector, Pipeline(cfg.postprocess))


def audio_factory(on_chunk, cfg: Config, on_error=None) -> AudioCapture:
    return AudioCapture(
        on_chunk,
        on_error=on_error,
        device=cfg.audio.device,
        blocksize=cfg.audio.blocksize,
        max_buffer_seconds=cfg.audio.max_buffer_seconds,
    )


class Daemon:
    def __init__(self, config_path: Path | None = None) -> None:
        self.manager = ConfigManager(config_path)
        cfg = self.manager.get()
        setup_logging(cfg.log_level)
        self.overlay = create_overlay(cfg.overlay)
        self.dispatcher = Dispatcher(
            build_components(cfg, on_fallback=self._on_fallback),
            audio_factory,
            self.overlay,
            build=lambda c: build_components(c, on_fallback=self._on_fallback),
            media=MediaPauser(),
            notifier=Notifier(),
        )
        self.control = ControlServer(self.handle_command)
        self._stop = threading.Event()
        self._hotkey = None

    def _on_fallback(self, message: str) -> None:
        self.dispatcher.warn(message)

    # ------------------------------------------------------------------ commands
    def handle_command(self, cmd: str) -> str:
        d = self.dispatcher
        if cmd == "toggle":
            return d.toggle()
        if cmd == "start":
            return d.start()
        if cmd == "stop":
            return d.stop()
        if cmd == "cancel":
            return d.cancel()
        if cmd == "status":
            return f"ok {d.status()} version={__version__}"
        if cmd == "reload":
            return self.reload()
        if cmd == "profile" or cmd.startswith("profile "):
            return self.profile_command(cmd[len("profile"):].strip())
        if cmd == "quit":
            self._stop.set()
            return "ok bye"
        return "error unknown command"

    def profile_command(self, arg: str) -> str:
        """``profile`` → list; ``profile next`` → cycle; ``profile <name>`` / ``profile default`` → switch."""
        from .config import list_profiles

        if not arg:
            names = list_profiles(self.manager.path)
            return f"ok profile={self.manager.profile or 'default'} available={','.join(names) or '-'}"
        try:
            target = self.manager.next_profile() if arg == "next" else (None if arg == "default" else arg)
            cfg = self.manager.set_profile(target)
            result = self.dispatcher.reload_config(cfg)
        except Exception as e:  # noqa: BLE001 - bad profile: old config stays active
            log.error("profile switch failed: %s", e)
            return f"error {e}"
        log.info("profile -> %s", target or "default")
        return f"{result} profile={target or 'default'}"

    def reload(self) -> str:
        try:
            cfg = self.manager.reload()
            result = self.dispatcher.reload_config(cfg)
        except (ConfigError, Exception) as e:  # noqa: BLE001 - old config stays active
            log.error("reload failed: %s", e)
            return f"error {e}"
        log.info("configuration reloaded")
        return result

    # ------------------------------------------------------------------ lifecycle
    def run(self) -> int:
        cfg = self.manager.get()
        if cfg.stt.provider == "google_free":
            log.warning("privacy: provider 'google_free' sends your audio to Google servers. "
                        "Use provider = \"vosk\" or \"whisper\" for fully offline operation.")
        self.control.start()
        self._start_hotkey(cfg)
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, lambda *_: threading.Thread(target=self.reload, daemon=True).start())
        log.info("voicetype %s ready (provider=%s)", __version__, cfg.stt.provider)
        try:
            self._stop.wait()
        finally:
            self.shutdown()
        return 0

    def _start_hotkey(self, cfg: Config) -> None:
        if cfg.hotkey.toggle_mode != "hold":
            return  # press mode uses the KDE Custom Shortcut → `voicetype toggle`
        from .hotkey import EvdevHotkeyListener

        d = self.dispatcher
        try:
            self._hotkey = EvdevHotkeyListener(
                cfg.hotkey.combo, "hold", d.toggle, d.start, d.stop, d.cancel,
                cancel_keys=cfg.hotkey.extra_stops if cfg.hotkey.escape_stops else (),
            )  # fmt: skip
            self._hotkey.start()
            log.info("evdev hotkey listener active (%s, hold)", cfg.hotkey.combo)
        except Exception as e:  # noqa: BLE001
            log.error("hold-mode hotkey unavailable: %s", e)

    def shutdown(self) -> None:
        log.info("shutting down")
        if self._hotkey:
            self._hotkey.stop()
        self.control.stop()
        self.dispatcher.shutdown()
