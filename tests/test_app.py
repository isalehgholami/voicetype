"""Daemon wiring, CLI, doctor, models, overlay helpers."""

import io
import json
import zipfile

import pytest

from voicetype import cli, doctor, models
from voicetype.config import Config
from voicetype.daemon import Daemon, _BrokenInjector, build_components
from voicetype.dispatcher import NullOverlay
from voicetype.injector import InjectionError
from voicetype.overlay import create_overlay, smooth


# ---------------------------------------------------------------- overlay helpers
def test_smooth_converges_monotonically():
    v = 0.0
    prev = -1.0
    for _ in range(60):
        v = smooth(v, 1.0)
        assert prev < v <= 1.0
        prev = v
    assert v > 0.999


def test_overlay_disabled_or_unavailable_falls_back():
    from voicetype.config import OverlayConfig

    assert isinstance(create_overlay(OverlayConfig(enabled=False)), NullOverlay)
    # no gi / no compositor in the test environment → must not raise
    assert create_overlay(OverlayConfig(enabled=True)) is not None


# ---------------------------------------------------------------- daemon
@pytest.fixture
def daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICETYPE_CONFIG", str(tmp_path / "c.toml"))
    (tmp_path / "c.toml").write_text('[overlay]\nenabled = false\n[stt]\nfallback_provider = ""\n', encoding="utf-8")
    monkeypatch.setattr("voicetype.daemon.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("voicetype.daemon.ControlServer", lambda handler: type("S", (), {"start": lambda s: None, "stop": lambda s: None})())
    return Daemon()


def test_daemon_commands(daemon):
    assert daemon.handle_command("status").startswith("ok idle provider=google_free")
    assert daemon.handle_command("cancel") == "ok idle"
    assert daemon.handle_command("stop").startswith("not listening")
    assert daemon.handle_command("bogus") == "error unknown command"
    assert daemon.handle_command("quit") == "ok bye" and daemon._stop.is_set()


def test_daemon_reload_applies_and_survives_bad_config(daemon, tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[overlay]\nenabled = false\n[stt]\nprovider = "vosk"\nfallback_provider = ""\n', encoding="utf-8")
    assert daemon.handle_command("reload") == "ok reloaded"
    assert daemon.dispatcher.components.backend.name == "vosk"
    p.write_text("[stt]\nprovider = 'nope'\n", encoding="utf-8")
    assert daemon.handle_command("reload").startswith("error")
    assert daemon.dispatcher.components.backend.name == "vosk"  # old config still active


def test_broken_injector_reports_reason():
    with pytest.raises(InjectionError, match="nothing installed"):
        _BrokenInjector("nothing installed").inject("x")


def test_build_components_survives_missing_injection_tools(monkeypatch):
    def boom(cfg):
        raise InjectionError("no injection backend found")

    monkeypatch.setattr("voicetype.daemon.TextInjector", boom)
    comps = build_components(Config())
    assert isinstance(comps.injector, _BrokenInjector)


# ---------------------------------------------------------------- cli
def test_parser_accepts_all_commands():
    p = cli.build_parser()
    for cmd in ("run", "toggle", "start", "stop", "cancel", "status", "reload", "quit", "doctor"):
        assert p.parse_args([cmd]).cmd == cmd
    assert p.parse_args(["models", "install", "fa-small", "--sha256", "ab"]).sha256 == "ab"
    with pytest.raises(SystemExit):
        p.parse_args([])


def test_client_when_daemon_down(monkeypatch, capsys):
    monkeypatch.setattr(cli, "send_command", lambda c: (_ for _ in ()).throw(ConnectionError("daemon is not running")))
    assert cli.main(["toggle"]) == 3
    assert "not running" in capsys.readouterr().err


def test_client_prints_reply(monkeypatch, capsys):
    monkeypatch.setattr(cli, "send_command", lambda c: "ok listening")
    assert cli.main(["toggle"]) == 0
    assert capsys.readouterr().out.strip() == "ok listening"
    monkeypatch.setattr(cli, "send_command", lambda c: "error busy")
    assert cli.main(["toggle"]) == 1


def test_config_commands(tmp_path, monkeypatch, capsys):
    p = tmp_path / "cfg" / "c.toml"
    monkeypatch.setenv("VOICETYPE_CONFIG", str(p))
    assert cli.main(["config", "path"]) == 0
    assert cli.main(["config", "init"]) == 0 and p.exists()
    assert cli.main(["config", "check"]) == 0
    p.write_text("[stt]\nprovider = 'x'\n", encoding="utf-8")
    assert cli.main(["config", "check"]) == 2
    assert "stt.provider" in capsys.readouterr().err


# ---------------------------------------------------------------- doctor
def test_doctor_reports_missing_tools_with_hints():
    checks = doctor.run_checks(
        which=lambda n: None,
        env={"XDG_SESSION_TYPE": "wayland", "XDG_RUNTIME_DIR": "/run/user/1"},
        has_module=lambda n: False,
        groups=lambda: set(),
        path_exists=lambda p: False,
        access=lambda p, m: False,
    )
    by = {c.name: c for c in checks}
    assert not by["text injection backend"].ok
    assert "wl-clipboard" in by["wl-clipboard (wl-copy)"].detail
    assert not by["sounddevice installed"].ok
    text, code = doctor.report(checks)
    assert code == 1 and "[✗]" in text and "[!]" in text


def test_doctor_all_good_wayland(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICETYPE_CONFIG", str(tmp_path / "none.toml"))
    checks = doctor.run_checks(
        which=lambda n: "/usr/bin/" + n,
        env={"XDG_SESSION_TYPE": "wayland", "XDG_RUNTIME_DIR": "/run/user/1"},
        has_module=lambda n: n not in ("sounddevice", "gi"),
        groups=lambda: {"input", "audio"},
        path_exists=lambda p: True,
        access=lambda p, m: True,
    )
    by = {c.name: c for c in checks}
    assert by["text injection backend"].detail == "paste-wl"
    assert by["ydotoold running"].ok and by["user in group 'input'"].ok


# ---------------------------------------------------------------- models
def make_zip(path, dirname="vosk-model-small-fa-0.42", evil=False):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{dirname}/am/final.mdl", "x")
        if evil:
            z.writestr("../../escape.txt", "pwned")


def test_model_install_from_local_zip(tmp_path):
    zp = tmp_path / "m.zip"
    make_zip(zp)
    dest = tmp_path / "models"
    path = models.install("fa-small", dest, url=zp.as_uri())
    assert (path / "am" / "final.mdl").exists()
    manifest = json.loads((path / "voicetype-manifest.json").read_text())
    assert manifest["sha256"] == models.sha256_file(zp)
    assert models.install("fa-small", dest, url="file:///does/not/matter") == path  # already installed
    assert "fa-small" in models.installed([dest])


def test_model_checksum_enforced(tmp_path):
    zp = tmp_path / "m.zip"
    make_zip(zp)
    with pytest.raises(models.ModelError, match="checksum mismatch"):
        models.install("fa-small", tmp_path / "d", url=zp.as_uri(), expected_sha256="0" * 64)
    assert models.install("fa-small", tmp_path / "d2", url=zp.as_uri(), expected_sha256=models.sha256_file(zp))


def test_model_zip_slip_rejected(tmp_path):
    zp = tmp_path / "m.zip"
    make_zip(zp, evil=True)
    with pytest.raises(models.ModelError, match="unsafe path"):
        models.install("fa-small", tmp_path / "d", url=zp.as_uri())
    assert not (tmp_path / "escape.txt").exists()


def test_model_bad_inputs(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(models.ModelError, match="valid zip"):
        models.install("fa-small", tmp_path / "d", url=bad.as_uri())
    with pytest.raises(models.ModelError, match="unknown model"):
        models.install("zz", tmp_path / "d")
    with pytest.raises(models.ModelError, match="download failed"):
        models.install("fa-small", tmp_path / "d", url=(tmp_path / "missing.zip").as_uri())
    wrong = tmp_path / "w.zip"
    make_zip(wrong, dirname="something-else")
    with pytest.raises(models.ModelError, match="does not contain"):
        models.install("fa-small", tmp_path / "d", url=wrong.as_uri())


def test_models_cli_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(models, "search_dirs", lambda: [tmp_path])
    assert cli.main(["models", "list"]) == 0
    assert "fa-small" in capsys.readouterr().out
    assert cli.main(["models", "install"]) == 2
    assert io is not None
