import os
import socket
import stat
import threading

import pytest

from voicetype.control import ControlServer, handle_connection, parse_command, send_command
from voicetype.hotkey import ComboError, ComboTracker, EvdevHotkeyListener, parse_combo

needs_unix = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets unavailable")


def roundtrip(line: bytes, handler):
    a, b = socket.socketpair()
    t = threading.Thread(target=handle_connection, args=(b, handler))
    t.start()
    a.sendall(line)
    a.settimeout(3)
    data = a.recv(1024)
    t.join(3)
    a.close()
    return data.decode()


def test_parse_command():
    assert parse_command(b" TOGGLE \n") == "toggle"
    assert parse_command(b"rm -rf /") is None
    assert parse_command(b"") is None


def test_connection_dispatches_known_command():
    seen = []
    assert roundtrip(b"toggle\n", lambda c: seen.append(c) or "ok toggled") == "ok toggled\n"
    assert seen == ["toggle"]


def test_connection_rejects_unknown_without_calling_handler():
    called = []
    out = roundtrip(b"format-disk\n", lambda c: called.append(c) or "ok")
    assert out.startswith("error unknown command") and not called


def test_connection_reports_handler_exception():
    def boom(c):
        raise RuntimeError("kaput")

    assert roundtrip(b"status\n", boom) == "error kaput\n"


def test_connection_oversized_line_is_bounded():
    out = roundtrip(b"x" * 5000, lambda c: "ok")
    assert out.startswith("error")


@needs_unix
def test_server_end_to_end_with_0600_socket(tmp_path):
    path = tmp_path / "v.sock"
    srv = ControlServer(lambda c: f"ok {c}", path)
    srv.start()
    try:
        assert send_command("status", path) == "ok status"
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        with pytest.raises(RuntimeError, match="already running"):
            ControlServer(lambda c: "x", path).start()
    finally:
        srv.stop()
    assert not path.exists()


@needs_unix
def test_stale_socket_file_is_replaced(tmp_path):
    path = tmp_path / "v.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.close()  # leaves a stale file
    srv = ControlServer(lambda c: "ok", path)
    srv.start()
    try:
        assert send_command("status", path) == "ok"
    finally:
        srv.stop()


@needs_unix
def test_send_command_when_daemon_down(tmp_path):
    with pytest.raises(ConnectionError, match="not running"):
        send_command("toggle", tmp_path / "none.sock")


# ---------------------------------------------------------------- hotkey
def test_parse_combo_variants():
    c = parse_combo("super+alt+v")
    assert c.satisfied_by({"KEY_LEFTMETA", "KEY_RIGHTALT", "KEY_V"})
    assert not c.satisfied_by({"KEY_LEFTMETA", "KEY_V"})
    assert parse_combo("ctrl+space").satisfied_by({"KEY_LEFTCTRL", "KEY_SPACE"})
    assert parse_combo("super+f9").satisfied_by({"KEY_RIGHTMETA", "KEY_F9"})
    assert parse_combo("esc").satisfied_by({"KEY_ESC"})


@pytest.mark.parametrize("bad", ["", "hyper+v", "ctrl+alt", "+"])
def test_parse_combo_errors(bad):
    with pytest.raises(ComboError):
        parse_combo(bad)


def test_tracker_press_release():
    t = ComboTracker(parse_combo("super+alt+v"))
    assert t.event("KEY_LEFTMETA", True) is None
    assert t.event("KEY_LEFTALT", True) is None
    assert t.event("KEY_V", True) == "press"
    assert t.event("KEY_V", False) == "release"
    assert t.event("KEY_V", True) == "press"


def make_listener(mode):
    calls = []
    lis = EvdevHotkeyListener(
        "ctrl+space", mode,
        on_toggle=lambda: calls.append("toggle"),
        on_start=lambda: calls.append("start"),
        on_stop=lambda: calls.append("stop"),
        on_cancel=lambda: calls.append("cancel"),
    )  # fmt: skip
    return lis, calls


def test_listener_press_mode_toggles():
    lis, calls = make_listener("press")
    lis.handle("KEY_LEFTCTRL", True)
    lis.handle("KEY_SPACE", True)
    lis.handle("KEY_SPACE", False)
    lis.handle("KEY_SPACE", True)
    assert calls == ["toggle", "toggle"]


def test_listener_hold_mode_push_to_talk():
    lis, calls = make_listener("hold")
    lis.handle("KEY_LEFTCTRL", True)
    lis.handle("KEY_SPACE", True)
    lis.handle("KEY_SPACE", False)
    assert calls == ["start", "stop"]


def test_listener_escape_cancels():
    lis, calls = make_listener("press")
    lis.handle("KEY_ESC", True)
    assert calls == ["cancel"]
