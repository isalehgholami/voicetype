"""Desktop notifications for errors and status messages (``notify-send``)."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence

from .log import get_logger

log = get_logger("notify")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]

#: How long each kind of notification stays on screen (milliseconds).
TIMEOUTS_MS = {
    "critical": 4000,
    "normal": 3000,
    "low": 2000,
}


def _default_run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, timeout=3)  # noqa: S603


class Notifier:
    """Sends desktop notifications through ``notify-send``.

    The urgency vocabulary mirrors the overlay's: a failure is ``critical``,
    a recoverable problem is ``normal`` and everything else is a quiet
    ``low`` hint that does not steal focus.
    """

    def __init__(self, run: Runner = _default_run, which: Callable[[str], str | None] = shutil.which) -> None:
        self._run = run
        self._available = bool(which("notify-send"))

    def error(self, message: str) -> None:
        self._send("critical", message)

    def warning(self, message: str) -> None:
        self._send("normal", message)

    def info(self, message: str) -> None:
        self._send("low", message)

    def success(self, message: str) -> None:
        self._send("low", message)

    def _send(self, urgency: str, message: str) -> None:
        if not self._available:
            return
        timeout = TIMEOUTS_MS.get(urgency, TIMEOUTS_MS["normal"])
        cmd = [
            "notify-send",
            "-a",
            "VoiceType",
            "-i",
            "audio-input-microphone",
            "-u",
            urgency,
            "-t",
            str(timeout),
            "--",
            "VoiceType",
            message[:300],
        ]
        try:
            self._run(cmd)
        except (OSError, subprocess.SubprocessError):
            log.debug("notify-send failed", exc_info=True)
