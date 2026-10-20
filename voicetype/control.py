"""Local control channel: a Unix socket in $XDG_RUNTIME_DIR (mode 0600). Replaces `pkill -SIGUSR1`.

Protocol: client sends one line (``toggle`` | ``start`` | ``stop`` | ``cancel`` | ``status`` |
``reload`` | ``quit``), server replies with one line (``ok ...`` or ``error ...``).
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
import stat
import threading
from collections.abc import Callable
from pathlib import Path

from .log import get_logger

log = get_logger("control")

COMMANDS = ("toggle", "start", "stop", "cancel", "status", "reload", "quit", "profile")
MAX_LINE = 256
CONN_TIMEOUT = 2.0


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        runtime = f"/tmp/voicetype-{os.getuid()}" if hasattr(os, "getuid") else str(Path.home())
        Path(runtime).mkdir(mode=0o700, exist_ok=True)
    return Path(runtime) / "voicetype.sock"


_PROFILE_ARG = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def parse_command(line: bytes) -> str | None:
    text = line.decode("utf-8", "replace").strip()
    word, _, arg = text.partition(" ")
    word, arg = word.lower(), arg.strip()
    if word == "profile":
        if not arg:
            return "profile"
        return f"profile {arg}" if _PROFILE_ARG.match(arg) else None
    return word if word in COMMANDS and not arg else None


def _drain(conn: socket.socket, limit: int = 1 << 16) -> None:
    """Discard unread request bytes so closing doesn't RST the connection and lose our reply."""
    conn.settimeout(0.05)
    try:
        while limit > 0:
            chunk = conn.recv(4096)
            if not chunk:
                break
            limit -= len(chunk)
    except OSError:  # timeout / reset: nothing more to drain
        pass


def handle_connection(conn: socket.socket, handler: Callable[[str], str]) -> None:
    """Serve exactly one request on ``conn`` (testable with socket.socketpair on any OS)."""
    conn.settimeout(CONN_TIMEOUT)
    try:
        data = b""
        while b"\n" not in data and len(data) < MAX_LINE:
            chunk = conn.recv(MAX_LINE)
            if not chunk:
                break
            data += chunk
        cmd = parse_command(data.split(b"\n", 1)[0])
        if cmd is None:
            reply = "error unknown command (expected: " + ", ".join(COMMANDS) + ")"
        else:
            try:
                reply = handler(cmd)
            except Exception as e:  # noqa: BLE001 - report to client, keep serving
                log.exception("command %s failed", cmd)
                reply = f"error {e}"
        conn.sendall((reply + "\n").encode("utf-8"))
        _drain(conn)
    except (TimeoutError, OSError) as e:
        log.debug("control connection dropped: %s", e)
    finally:
        with contextlib.suppress(OSError):
            conn.close()


class ControlServer:
    def __init__(self, handler: Callable[[str], str], path: Path | None = None) -> None:
        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("Unix sockets are not available on this platform")
        self.handler = handler
        self.path = path or socket_path()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._claim_path()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)  # socket is created 0600
        try:
            s.bind(str(self.path))
        finally:
            os.umask(old_umask)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        s.listen(4)
        s.settimeout(0.5)
        self._sock = s
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, daemon=True, name="control")
        self._thread.start()
        log.info("control socket: %s", self.path)

    def _claim_path(self) -> None:
        if not self.path.exists():
            return
        # A live daemon owns it → refuse; a stale file → remove.
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect(str(self.path))
        except OSError:
            self.path.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"another voicetype instance is already running ({self.path})")
        finally:
            probe.close()

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=handle_connection, args=(conn, self.handler), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            with contextlib.suppress(OSError):
                self._sock.close()
        if self._thread:
            self._thread.join(1.0)
        self.path.unlink(missing_ok=True)


def send_command(cmd: str, path: Path | None = None, timeout: float = 3.0) -> str:
    """Client side. Raises ConnectionError with a helpful message if the daemon isn't running."""
    if not hasattr(socket, "AF_UNIX"):
        raise ConnectionError("Unix sockets are not available on this platform")
    path = path or socket_path()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        s.sendall((cmd + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(1024)
            if not chunk:
                break
            data += chunk
        return data.decode("utf-8", "replace").strip()
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise ConnectionError("voicetype daemon is not running (start it with: voicetype run)") from e
    finally:
        s.close()
