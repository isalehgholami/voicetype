"""Mic disconnect, media pause, notifications, logging/rotation, profiles."""

import logging
import subprocess
import time

import pytest

from tests.test_dispatcher import LOUD, Backend, FakeAudio, Injector, Overlay, speak
from voicetype import log as vtlog
from voicetype.audio import AudioCapture
from voicetype.config import (
    Config,
    ConfigError,
    ConfigManager,
    deep_merge,
    list_profiles,
    load_config,
    parse_config,
)
from voicetype.control import parse_command
from voicetype.dispatcher import Components, Dispatcher, State
from voicetype.media import MediaPauser
from voicetype.notify import Notifier
from voicetype.stt.base import STTUnavailableError
from voicetype.stt.vosk_backend import VoskBackend


# ------------------------------------------------------------------ helpers
class Run:
    def __init__(self, outputs=None):
        self.calls, self.outputs = [], outputs or {}

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout=self.outputs.get(tuple(cmd), b""), stderr=b"")


class Media:
    def __init__(self):
        self.events = []

    def pause(self):
        self.events.append("pause")

    def resume(self):
        self.events.append("resume")


class Notif:
    def __init__(self):
        self.msgs = []

    def error(self, m):
        self.msgs.append(m)


def make(cfg=None, backend=None, **kw):
    ov = Overlay()
    d = Dispatcher(
        Components(cfg or Config(), backend or Backend(), Injector(), lambda t: t),
        lambda on_chunk, c, on_err: FakeAudio(on_chunk),
        ov,
        error_display_seconds=0.02,
        **kw,
    )
    return d, ov


# ------------------------------------------------------------------ mic disconnect (T-2.8)
class DeviceStream:
    def __init__(self, **kw):
        self.kw = kw
        DeviceStream.last = self

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def test_capture_reports_unexpected_stream_end_but_not_normal_stop():
    errors = []
    cap = AudioCapture(lambda c: None, stream_factory=DeviceStream, on_error=errors.append)
    cap.start()
    DeviceStream.last.kw["finished_callback"]()  # device unplugged → PortAudio ends the stream
    assert errors == ["Microphone disconnected"]
    cap.stop()
    DeviceStream.last.kw["finished_callback"]()  # after a normal stop: silent
    assert len(errors) == 1


def test_dispatcher_recovers_when_mic_disappears_mid_recording():
    d, ov = make()
    d.start()
    speak(2)
    d.on_audio_error(d._session, "Microphone disconnected")
    assert d.state is State.IDLE
    assert any(e[0] == "show_error" and "Microphone" in e[1] for e in ov.events)
    assert d.components.injector.texts == []
    assert d.start() == "ok listening"  # not wedged


def test_stale_audio_error_is_ignored():
    d, ov = make()
    d.start()
    old = d._session
    d.cancel()
    d.start()
    d.on_audio_error(old, "late error from an old session")
    assert d.state is State.LISTENING


# ------------------------------------------------------------------ media pause (T-2.13)
def test_media_pauser_pauses_only_playing_players_and_resumes_them():
    run = Run({("playerctl", "-l"): b"spotify\nvlc\n", ("playerctl", "-p", "spotify", "status"): b"Playing\n", ("playerctl", "-p", "vlc", "status"): b"Paused\n"})
    m = MediaPauser(run, which=lambda n: "/usr/bin/playerctl")
    m.pause()
    assert ["playerctl", "-p", "spotify", "pause"] in run.calls and ["playerctl", "-p", "vlc", "pause"] not in run.calls
    m.pause()  # idempotent
    assert run.calls.count(["playerctl", "-p", "spotify", "pause"]) == 1
    m.resume()
    assert ["playerctl", "-p", "spotify", "play"] in run.calls and ["playerctl", "-p", "vlc", "play"] not in run.calls
    n = len(run.calls)
    m.resume()
    assert len(run.calls) == n


def test_media_pauser_without_playerctl_does_nothing():
    run = Run()
    MediaPauser(run, which=lambda n: None).pause()
    assert run.calls == []


def test_dispatcher_pauses_and_resumes_media_around_recording():
    media = Media()
    d, _ = make(cfg=parse_config({"audio": {"pause_media": True}}), media=media)
    d.start()
    speak(2)
    assert media.events == ["pause"]
    d.stop()
    d.wait_idle()
    assert media.events[:2] == ["pause", "resume"]


def test_media_untouched_when_disabled_and_resumed_on_cancel():
    media = Media()
    d, _ = make(media=media)  # pause_media defaults to false
    d.start()
    d.cancel()
    assert "pause" not in media.events
    media2 = Media()
    d2, _ = make(cfg=parse_config({"audio": {"pause_media": True}}), media=media2)
    d2.start()
    d2.cancel()
    assert media2.events == ["pause", "resume"]


# ------------------------------------------------------------------ notifications (T-2.12)
def test_notifier_command():
    run = Run()
    Notifier(run, which=lambda n: "/usr/bin/notify-send").error("Network error")
    assert run.calls[0][0] == "notify-send" and run.calls[0][-1] == "Network error" and "--" in run.calls[0]
    run2 = Run()
    Notifier(run2, which=lambda n: None).error("x")
    assert run2.calls == []


def test_errors_are_notified_unless_disabled():
    from voicetype.stt.base import STTNetworkError

    n = Notif()
    d, _ = make(backend=Backend(exc=STTNetworkError("x")), notifier=n)
    d.start()
    speak()
    d.stop()
    d.wait_idle()
    assert n.msgs and "Network" in n.msgs[0]
    n2 = Notif()
    d2, _ = make(cfg=parse_config({"notify_errors": False}), backend=Backend(exc=STTNetworkError("x")), notifier=n2)
    d2.start()
    speak()
    d2.stop()
    d2.wait_idle()
    assert n2.msgs == []


def test_silence_is_shown_in_the_overlay_but_never_notified():
    """Nothing was said: the overlay says so, the desktop stays quiet."""
    from voicetype.stt.base import STTUnintelligibleError

    n = Notif()
    d, ov = make(backend=Backend(exc=STTUnintelligibleError()), notifier=n)
    d.start()
    speak()
    d.stop()
    d.wait_idle()
    assert any(e[0] == "show_error" and "understand" in e[1] for e in ov.events)
    assert n.msgs == []


def test_a_silent_recording_is_not_notified():
    n = Notif()
    d, ov = make(notifier=n)
    d.start()
    d.stop()  # the microphone recorded nothing at all
    d.wait_idle()
    assert any(e[0] == "show_error" for e in ov.events)
    assert n.msgs == []


# ------------------------------------------------------------------ logging (T-1.1.3 / T-2.9)
def test_every_state_transition_is_logged(caplog):
    d, _ = make()
    with caplog.at_level(logging.INFO, logger="voicetype"):
        d.start()
        speak(2)
        d.stop()
        d.wait_idle()
        time.sleep(0.05)
    text = " ".join(r.getMessage() for r in caplog.records)
    for transition in ("idle -> listening", "listening -> transcribing", "transcribing -> injecting", "injecting -> idle"):
        assert transition in text


def test_log_file_rotates(tmp_path, monkeypatch):
    monkeypatch.setattr(vtlog, "MAX_BYTES", 2000)
    monkeypatch.setattr(vtlog, "BACKUP_COUNT", 3)
    path = tmp_path / "state" / "voicetype.log"
    logger = vtlog.setup_logging("INFO", path)
    try:
        for i in range(400):
            logger.info("line %d %s", i, "x" * 40)
        for h in logger.handlers:
            h.flush()
        files = sorted(p.name for p in path.parent.iterdir())
        assert "voicetype.log" in files and "voicetype.log.1" in files
        assert not any(f.endswith(".4") for f in files)  # at most BACKUP_COUNT backups
    finally:
        vtlog.setup_logging("INFO", False)


def test_unwritable_log_dir_is_not_fatal(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    logger = vtlog.setup_logging("INFO", blocker / "sub" / "x.log")
    logger.info("still works")
    vtlog.setup_logging("INFO", False)


# ------------------------------------------------------------------ vosk missing (T-3.1.4)
def test_vosk_not_installed_gives_install_hint(tmp_path):
    pytest.importorskip  # noqa: B018 - documentation only
    try:
        import vosk  # noqa: F401
    except ImportError:
        with pytest.raises(STTUnavailableError, match="pip install vosk"):
            VoskBackend(str(tmp_path)).open_stream()
    else:
        pytest.skip("vosk is installed here")


# ------------------------------------------------------------------ profiles (T-4.2)
def test_deep_merge():
    assert deep_merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 3}, "c": 4}) == {"a": {"x": 1, "y": 3}, "b": 1, "c": 4}


@pytest.fixture
def prof_env(tmp_path):
    base = tmp_path / "config.toml"
    base.write_text('[postprocess]\nauto_punctuate = true\n[stt]\nlanguage = "fa-IR"\n', encoding="utf-8")
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "code.toml").write_text("[postprocess]\nauto_punctuate = false\ncapitalize_first = false\n", encoding="utf-8")
    (tmp_path / "profiles" / "english.toml").write_text('[stt]\nlanguage = "en-US"\n', encoding="utf-8")
    return base


def test_profile_overlay_keeps_unrelated_keys(prof_env):
    cfg = load_config(prof_env, "code")
    assert cfg.postprocess.auto_punctuate is False and cfg.postprocess.capitalize_first is False
    assert cfg.stt.language == "fa-IR"
    assert list_profiles(prof_env) == ["code", "english"]


@pytest.mark.parametrize("name,msg", [("nope", "not found"), ("../etc", "invalid profile")])
def test_bad_profile_names(prof_env, name, msg):
    with pytest.raises(ConfigError, match=msg):
        load_config(prof_env, name)


def test_manager_profile_switching_and_cycle(prof_env):
    m = ConfigManager(prof_env)
    assert m.next_profile() == "code"
    m.set_profile("code")
    assert m.get().postprocess.auto_punctuate is False
    assert m.next_profile() == "english"
    m.set_profile("english")
    assert m.next_profile() is None  # wraps back to the base config
    m.set_profile(None)
    assert m.get().postprocess.auto_punctuate is True
    with pytest.raises(ConfigError):
        m.set_profile("missing")
    assert m.profile is None  # failed switch changes nothing
    m.set_profile("code")
    assert m.reload().postprocess.auto_punctuate is False  # reload keeps the active profile


def test_profile_control_command_parsing():
    assert parse_command(b"profile\n") == "profile"
    assert parse_command(b"profile code\n") == "profile code"
    assert parse_command(b"profile next\n") == "profile next"
    assert parse_command(b"profile ../../x\n") is None
    assert parse_command(b"toggle now\n") is None


def test_daemon_profile_command(prof_env, monkeypatch):
    from voicetype.daemon import Daemon

    monkeypatch.setenv("VOICETYPE_CONFIG", str(prof_env))
    prof_env.write_text('[overlay]\nenabled = false\n[stt]\nfallback_provider = ""\n[postprocess]\nauto_punctuate = true\n', encoding="utf-8")
    monkeypatch.setattr("voicetype.daemon.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("voicetype.daemon.ControlServer", lambda handler: object())
    d = Daemon()
    assert d.handle_command("profile") == "ok profile=default available=code,english"
    assert d.handle_command("profile code").startswith("ok reloaded profile=code")
    assert d.dispatcher.components.config.postprocess.auto_punctuate is False
    assert d.handle_command("profile next").endswith("profile=english")
    assert d.handle_command("profile next").endswith("profile=default")
    assert d.handle_command("profile default").endswith("profile=default")
    assert d.handle_command("profile ghost").startswith("error")
    assert d.dispatcher.components.config.postprocess.auto_punctuate is True


def test_loud_constant_available():
    assert len(LOUD) == 3200


def test_debug_save_audio_writes_last_wav(tmp_path, monkeypatch):
    import wave

    monkeypatch.setattr("voicetype.dispatcher.default_log_dir", lambda: tmp_path)
    d, _ = make(cfg=parse_config({"audio": {"debug_save_audio": True}}))
    d.start()
    speak(2)
    d.stop()
    d.wait_idle()
    with wave.open(str(tmp_path / "last.wav")) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, 3200)
    d2, _ = make()  # flag off → nothing written
    (tmp_path / "last.wav").unlink()
    d2.start()
    speak(1)
    d2.stop()
    d2.wait_idle()
    assert not (tmp_path / "last.wav").exists()
