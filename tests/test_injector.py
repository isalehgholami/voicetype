import subprocess

import pytest

from voicetype.config import InjectorConfig
from voicetype.injector import InjectionError, TextInjector, Tools, choose_backend, detect_session, sanitize


class Recorder:
    """Fake subprocess runner that records calls and serves canned outputs."""

    def __init__(self, outputs=None):
        self.calls = []
        self.outputs = outputs or {}

    def __call__(self, cmd, **kw):
        self.calls.append((list(cmd), kw.get("input")))
        key = tuple(cmd[:2])
        rc, out = self.outputs.get(key, (0, b""))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=b"boom" if rc else b"")

    def cmds(self):
        return [c for c, _ in self.calls]


WL_FULL = Tools(wl_copy=True, wl_paste=True, wtype=True, ydotool=True)
X11_FULL = Tools(xdotool=True, xclip=True)


def make(cfg=None, session="wayland", tools=WL_FULL, run=None):
    run = run or Recorder()
    return TextInjector(cfg or InjectorConfig(), session=session, tools=tools, run=run, sleep=lambda s: None), run


def test_detect_session():
    assert detect_session({"XDG_SESSION_TYPE": "Wayland"}) == "wayland"
    assert detect_session({"WAYLAND_DISPLAY": "wayland-0"}) == "wayland"
    assert detect_session({"DISPLAY": ":0"}) == "x11"
    assert detect_session({}) == "unknown"


@pytest.mark.parametrize(
    "session,tools,req,expected",
    [
        ("wayland", WL_FULL, "auto", "paste-wl"),
        ("wayland", Tools(wtype=True), "auto", "wtype"),
        ("wayland", Tools(ydotool=True), "auto", "ydotool"),
        ("wayland", Tools(wl_copy=True, ydotool=True), "wtype", None),
        ("wayland", WL_FULL, "wtype", "wtype"),
        ("wayland", WL_FULL, "paste", "paste-wl"),
        ("x11", X11_FULL, "auto", "xdotool"),
        ("x11", X11_FULL, "paste", "paste-x11"),
        ("x11", Tools(xdotool=True), "paste", None),
        ("x11", Tools(), "auto", None),
        ("wayland", Tools(), "auto", None),
    ],
)
def test_backend_selection(session, tools, req, expected):
    if expected is None:
        with pytest.raises(InjectionError):
            choose_backend(session, tools, req)
    else:
        assert choose_backend(session, tools, req) == expected


def test_sanitize_strips_control_characters():
    assert sanitize("a\x00b\x1b[31mc\x7f\x85d\te\nf") == "ab[31mcd\te\nf"
    assert sanitize("  hi  ", strip=True) == "hi"
    assert sanitize("سلام Python") == "سلام Python"


def test_paste_wayland_sequence_with_clipboard_restore():
    run = Recorder(
        {
            ("wl-paste", "--list-types"): (0, b"text/plain;charset=utf-8\ntext/html\n"),
            ("wl-paste", "--no-newline"): (0, b"OLD"),
        }
    )
    inj, run = make(run=run)
    inj.inject("سلام دنیا")
    cmds = run.cmds()
    assert cmds[0][:2] == ["ydotool", "key"] and all(c.endswith(":0") for c in cmds[0][2:])  # modifiers released
    assert ["wl-paste", "--no-newline", "--type", "text/plain;charset=utf-8"] in cmds
    copy = next(c for c in run.calls if c[0][0] == "wl-copy" and c[1] == "سلام دنیا".encode())
    assert copy is not None
    assert ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"] in cmds
    last = run.calls[-1]
    assert last[0][0] == "wl-copy" and last[1] == b"OLD"


def test_paste_wayland_empty_clipboard_not_restored():
    run = Recorder({("wl-paste", "--list-types"): (1, b"")})
    inj, run = make(run=run)
    inj.inject("x")
    assert [c for c in run.calls if c[0][0] == "wl-copy"].__len__() == 1


def test_restore_disabled():
    inj, run = make(InjectorConfig(restore_clipboard=False))
    inj.inject("x")
    assert not any(c[0] == "wl-paste" for c in run.cmds())
    assert sum(1 for c in run.cmds() if c[0] == "wl-copy") == 1


def test_terminal_uses_ctrl_shift_v_via_kdotool():
    tools = Tools(wl_copy=True, wl_paste=True, ydotool=True, kdotool=True)
    run = Recorder({("kdotool", "getactivewindow"): (0, b"org.kde.konsole\n")})
    inj, run = make(tools=tools, run=run)
    inj.inject("x")
    assert ["ydotool", "key", "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"] in run.cmds()


def test_paste_shortcut_override():
    inj, run = make(InjectorConfig(paste_shortcut="ctrl+shift+v"))
    inj.inject("x")
    assert ["ydotool", "key", "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"] in run.cmds()


def test_paste_failure_still_restores_clipboard_and_raises():
    run = Recorder(
        {
            ("wl-paste", "--list-types"): (0, b"text/plain\n"),
            ("wl-paste", "--no-newline"): (0, b"OLD"),
            ("ydotool", "key"): (1, b""),
        }
    )
    # first ydotool key (release modifiers) also fails but is non-fatal; paste key failure must raise
    inj, run = make(run=run)
    with pytest.raises(InjectionError, match="ydotoold"):
        inj.inject("x")
    assert run.calls[-1][0][0] == "wl-copy" and run.calls[-1][1] == b"OLD"


def test_wtype_and_xdotool_commands():
    inj, run = make(InjectorConfig(backend="wtype", typing_delay_ms=5))
    inj.inject("hi")
    assert ["wtype", "-d", "5", "--", "hi"] in run.cmds()
    inj, run = make(InjectorConfig(backend="auto"), session="x11", tools=X11_FULL)
    inj.inject("hi")
    assert ["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", "hi"] in run.cmds()


def test_empty_after_sanitize_does_nothing():
    inj, run = make()
    inj.inject("\x00\x1b")
    assert run.calls == []


def test_x11_paste_backend():
    inj, run = make(InjectorConfig(backend="paste"), session="x11", tools=X11_FULL, run=Recorder({("xclip", "-selection"): (0, b"OLD")}))
    inj.inject("متن")
    assert ["xdotool", "key", "--clearmodifiers", "ctrl+v"] in run.cmds()
    assert run.calls[-1][1] == b"OLD"


def test_release_modifiers_can_be_disabled():
    inj, run = make(InjectorConfig(release_modifiers=False, backend="wtype"))
    inj.inject("x")
    assert not any(c[:2] == ["ydotool", "key"] for c in run.cmds())
