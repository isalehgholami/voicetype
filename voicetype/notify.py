"""Desktop notifications for errors (``notify-send``)."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence

from .log import get_logger

log = get_logger("notify")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _default_run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, timeout=3)  # noqa: S603


class Notifier:
    def __init__(self, run: Runner = _default_run, which: Callable[[str], str | None] = shutil.which) -> None:
        self._run = run
        self._available = bool(which("notify-send"))

    def error(self, message: str) -> None:
        if not self._available:
            return
        cmd = ["notify-send", "-a", "VoiceType", "-i", "audio-input-microphone", "-t", "4000", "--", "VoiceType", message[:300]]
        try:
            self._run(cmd)
        except (OSError, subprocess.SubprocessError):
            log.debug("notify-send failed", exc_info=True)
