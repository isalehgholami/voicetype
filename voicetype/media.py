"""Pause playing media while recording (pattern from hyprwhspr media_pauser) via ``playerctl``."""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Callable, Sequence

from .log import get_logger

log = get_logger("media")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _default_run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, timeout=3)  # noqa: S603


class MediaPauser:
    """pause() stops only players that were *playing*; resume() restarts exactly those. Idempotent."""

    def __init__(self, run: Runner = _default_run, which: Callable[[str], str | None] = shutil.which) -> None:
        self._run = run
        self._which = which
        self._paused: list[str] = []
        self._lock = threading.Lock()

    def pause(self) -> None:
        if not self._which("playerctl"):
            return
        with self._lock:
            if self._paused:
                return
            try:
                players = self._run(["playerctl", "-l"]).stdout.decode("utf-8", "replace").split()
                for p in players:
                    status = self._run(["playerctl", "-p", p, "status"]).stdout.decode().strip()
                    if status == "Playing":
                        self._run(["playerctl", "-p", p, "pause"])
                        self._paused.append(p)
            except (OSError, subprocess.SubprocessError):
                log.debug("playerctl failed", exc_info=True)

    def resume(self) -> None:
        with self._lock:
            paused, self._paused = self._paused, []
        for p in paused:
            try:
                self._run(["playerctl", "-p", p, "play"])
            except (OSError, subprocess.SubprocessError):
                log.debug("playerctl resume failed", exc_info=True)
