"""The long-running daemon: wires config, STT, injector, overlay, control socket and signals."""

from __future__ import annotations

import signal
import threading
from pathlib import Path

from . import __version__, network
from .audio import AudioCapture
from .config import Config, ConfigManager, OverlayConfig
from .control import ControlServer
from .dispatcher import Components, Dispatcher, NullOverlay, Overlay, close_quietly
from .injector import InjectionError, TextInjector
from .log import get_logger, setup_logging
from .media import MediaPauser
from .notify import Notifier
from .overlay import create_overlay
from .postprocess import Pipeline
from .stt.registry import build_backend
from .translate import build_translator, translate_stage

log = get_logger("daemon")


class _BrokenInjector:
    """Keeps the daemon alive when no injection tool is installed; each attempt reports why."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def inject(self, text: str) -> None:
        raise InjectionError(self.reason)


def build_components(cfg: Config, on_warning=None) -> Components:
    # Proxy first: providers and model downloads must already see it. Raises ProxyError (e.g. PySocks
    # missing) so a broken proxy never silently falls back to a direct connection.
    network.configure(cfg.network.proxy, cfg.network.no_proxy)
    backend = build_backend(cfg, on_warning)
    try:
        injector = TextInjector(cfg.injector)
    except InjectionError as e:
        log.error("%s", e)
        injector = _BrokenInjector(str(e))
    # Translation is optional and off by default; a broken language is a config error.
    translator = build_translator(
        cfg.translate,
        proxy=network.proxy_url(cfg.network.proxy),
        on_warning=on_warning,
    )
    # The post-processing runs before the translation: the card shows the
    # source while the translation is being made, so the source needs its own
    # callable.
    post = Pipeline(cfg.postprocess)
    pipeline = translate_stage(post, translator, cfg.postprocess)
    return Components(cfg, backend, injector, pipeline, translator, post)


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
        overlay = create_overlay(cfg.overlay)
        # the configuration the overlay was built from — a reload only rebuilds
        # it when these values actually changed
        self._overlay_cfg = cfg.overlay
        self._hotkey_cfg = cfg.hotkey
        self._log_level = cfg.log_level
        self.dispatcher = Dispatcher(
            build_components(cfg, on_warning=self._on_warning),
            audio_factory,
            overlay,
            build=lambda c: build_components(c, on_warning=self._on_warning),
            media=MediaPauser(),
            notifier=Notifier(),
        )
        self.control = ControlServer(self.handle_command)
        self._stop = threading.Event()
        self._hotkey = None

    @property
    def overlay(self) -> Overlay:
        """The overlay the daemon is currently using (a reload may replace it)."""
        return self.dispatcher.overlay

    def _on_warning(self, message: str) -> None:
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
            result = self._apply_config(cfg)
        except Exception as e:  # noqa: BLE001 - bad profile: old config stays active
            log.error("profile switch failed: %s", e)
            return f"error {e}"
        self._after_config(cfg)
        log.info("profile -> %s", target or "default")
        return f"{result} profile={target or 'default'}"

    def reload(self) -> str:
        """Re-read the configuration file and apply it to the running daemon."""
        try:
            cfg = self.manager.reload()
            result = self._apply_config(cfg)
        except Exception as e:  # noqa: BLE001 - old config stays active
            log.error("reload failed: %s", e)
            return f"error {e}"
        self._after_config(cfg)
        log.info("configuration reloaded")
        return result

    def _apply_config(self, cfg: Config) -> str:
        """Hand a new configuration to the dispatcher: components and overlay.

        The overlay is only rebuilt when its own settings changed — a reload
        that touches the STT settings must not tear the window down.  A new
        overlay that cannot be built (no GTK, no display) never replaces the
        one that works.

        A configuration that is rejected keeps the old one: the overlay that
        was built for it is closed instead of leaked.
        """
        previous = self._overlay_cfg
        overlay = self._new_overlay(cfg)
        try:
            result = self.dispatcher.reload_config(cfg, overlay=overlay)
        except Exception:
            self._discard_overlay(overlay, previous)
            raise
        if result.startswith("error"):
            self._discard_overlay(overlay, previous)
        return result

    def _discard_overlay(self, overlay: Overlay | None, previous: OverlayConfig) -> None:
        """Throw away an overlay that was never used; the old settings stay."""
        close_quietly(overlay)
        self._overlay_cfg = previous

    def _new_overlay(self, cfg: Config) -> Overlay | None:
        """An overlay for the new configuration; None when the current one still fits."""
        if cfg.overlay == self._overlay_cfg:
            return None
        overlay = create_overlay(cfg.overlay)
        if (
            cfg.overlay.enabled
            and isinstance(overlay, NullOverlay)
            and not isinstance(self.overlay, NullOverlay)
        ):
            log.warning("the overlay could not be rebuilt — keeping the current one")
            return None
        self._overlay_cfg = cfg.overlay
        return overlay

    def _after_config(self, cfg: Config) -> None:
        """Apply the parts of the configuration the dispatcher does not own."""
        if cfg.log_level != self._log_level:
            self._log_level = cfg.log_level
            setup_logging(cfg.log_level)
        self._apply_hotkey(cfg)

    def _apply_hotkey(self, cfg: Config) -> None:
        """Restart the hold-mode listener when its configuration changed."""
        if self._hotkey_cfg == cfg.hotkey:
            return
        self._hotkey_cfg = cfg.hotkey
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None
        self._start_hotkey(cfg)

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
