"""The install script must update, repair and configure an existing installation.

The script is exercised against fake ``pipx``/``systemctl`` binaries so that the
real user home is never touched.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "install-user.sh"

PIPX_STUB = """#!/usr/bin/env bash
# fake pipx: records every call and simulates the venv/app layout
set -u
STATE="$STUB_STATE"
LOG="$STATE/pipx.log"
printf '%s\\n' "$*" >> "$LOG"

case "$1" in
list)
    if [ "${2:-}" = "--json" ]; then
        [ -f "$STATE/pipx.json" ] && cat "$STATE/pipx.json" || echo '{}'
    fi
    ;;
environment)
    cat <<EOF
Environment variables (set by user):

PIPX_BIN_DIR=

Derived values (computed by pipx):

PIPX_BIN_DIR=$STATE/bin
PIPX_LOCAL_VENVS=$STATE/venvs
EOF
    ;;
install)
    mkdir -p "$STATE/venvs/voicetype/bin" "$STATE/bin"
    for app in voicetype voicetype-toggle; do
        cat > "$STATE/bin/$app" <<'APP'
#!/usr/bin/env bash
exec env PYTHONPATH="__ROOT__" python3 -m voicetype "$@"
APP
        chmod 755 "$STATE/bin/$app"
    done
    printf 'installed\\n' >> "$LOG"
    ;;
uninstall)
    rm -rf "$STATE/venvs/voicetype"
    printf 'uninstalled\\n' >> "$LOG"
    ;;
runpip)
    printf 'runpip %s\\n' "$*" >> "$LOG"
    ;;
esac
exit 0
"""

APP_STUB = """#!/usr/bin/env bash
exec env PYTHONPATH="__ROOT__" python3 -m voicetype "$@"
"""

SYSTEMCTL_STUB = """#!/usr/bin/env bash
set -u
STATE="$STUB_STATE"
printf '%s\\n' "$*" >> "$STATE/systemctl.log"
case "$1 $2" in
"--user show-environment")
    [ "${STUB_NO_BUS:-0}" = "1" ] && exit 1
    exit 0
    ;;
"--user is-active")
    [ "${STUB_ACTIVE:-0}" = "1" ] && exit 0
    exit 3
    ;;
esac
exit 0
"""


def _write_stub(path: Path, body: str) -> None:
    path.write_text(body.replace("__ROOT__", str(REPO)), encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def env(tmp_path):
    """A fake user environment: HOME, PATH (stubs first) and the stub state."""
    home = tmp_path / "home"
    state = tmp_path / "state"
    bindir = tmp_path / "bin"
    for d in (home, state, bindir):
        d.mkdir(parents=True, exist_ok=True)
    _write_stub(bindir / "pipx", PIPX_STUB)
    _write_stub(bindir / "systemctl", SYSTEMCTL_STUB)
    e = dict(os.environ)
    e.update(
        HOME=str(home),
        PATH=f"{bindir}:{e['PATH']}",
        STUB_STATE=str(state),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_RUNTIME_DIR=str(tmp_path / "run"),
    )
    e.pop("XDG_SESSION_TYPE", None)
    return e, state, home


def _pipx_json(state: Path, spec: str = "/src/voicetype[socks]", installed: bool = True) -> None:
    if not installed:
        (state / "pipx.json").write_text('{"venvs": {}}', encoding="utf-8")
        return
    (state / "pipx.json").write_text(
        json.dumps(
            {
                "venvs": {
                    "voicetype": {
                        "metadata": {
                            "main_package": {
                                "package_or_url": spec,
                                "package_version": "0.1.0",
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _existing_install(state: Path) -> None:
    """Simulate a working installation: venv directory plus the console scripts."""
    (state / "venvs" / "voicetype" / "bin").mkdir(parents=True, exist_ok=True)
    (state / "bin").mkdir(parents=True, exist_ok=True)
    for app in ("voicetype", "voicetype-toggle"):
        _write_stub(state / "bin" / app, APP_STUB)


def _run(env, *args):
    return subprocess.run(
        [str(SCRIPT), *args],
        env=env,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _log(state: Path, name: str) -> str:
    path = state / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_fresh_install_creates_venv_unit_and_config(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    log = _log(state, "pipx.log")
    assert "install --system-site-packages .[socks]" in log
    # the config file is created and is the documented sample
    cfg = home / ".config" / "voicetype" / "config.toml"
    assert cfg.exists()
    assert "[stt]" in cfg.read_text(encoding="utf-8")
    # the unit points at the pipx binaries, not at /usr/bin
    unit = (home / ".config" / "systemd" / "user" / "voicetype.service").read_text(encoding="utf-8")
    assert f"ExecStart={state}/bin/voicetype run" in unit
    assert (home / ".local" / "lib" / "voicetype" / "wait-display").exists()
    assert "enable --now voicetype" in _log(state, "systemctl.log")


def test_existing_install_is_updated_in_place(env):
    e, state, home = env
    _pipx_json(state)
    _existing_install(state)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    log = _log(state, "pipx.log")
    # the venv is updated in place, not thrown away
    assert "uninstalled" not in log
    assert "runpip voicetype install --force-reinstall --no-deps" in log
    # the dependencies are refreshed as well
    assert "runpip voicetype install --no-build-isolation" in log


def test_update_keeps_the_extras_of_the_previous_install(env):
    e, state, home = env
    _pipx_json(state, spec="/src/voicetype[vosk,whisper]")
    _existing_install(state)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    log = _log(state, "pipx.log")
    assert "runpip voicetype install --force-reinstall --no-deps" in log
    assert "vosk" in log and "whisper" in log


def test_reset_extras_drops_the_previous_ones(env):
    e, state, home = env
    _pipx_json(state, spec="/src/voicetype[vosk]")
    _existing_install(state)
    r = _run(e, "--no-hotkey", "--reset-extras", "socks")
    assert r.returncode == 0, r.stderr
    log = _log(state, "pipx.log")
    assert "socks" in log
    assert "vosk" not in log


def test_unknown_extra_is_rejected(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey", "nope")
    assert r.returncode != 0
    assert "unknown extra" in r.stderr


def test_broken_venv_is_rebuilt(env):
    e, state, home = env
    _pipx_json(state)  # pipx still lists voicetype ...
    # ... but the venv directory is gone
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    log = _log(state, "pipx.log")
    assert "uninstalled" in log
    assert "install --system-site-packages" in log


def test_reinstall_flag_rebuilds_the_venv(env):
    e, state, home = env
    _pipx_json(state)
    _existing_install(state)
    r = _run(e, "--no-hotkey", "--reinstall")
    assert r.returncode == 0, r.stderr
    log = _log(state, "pipx.log")
    assert "uninstalled" in log
    assert "install --system-site-packages" in log


def test_broken_config_is_backed_up_and_replaced(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    cfg = home / ".config" / "voicetype" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("this is = not toml [", encoding="utf-8")
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "[stt]" in cfg.read_text(encoding="utf-8")
    backups = list(cfg.parent.glob("config.toml.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "this is = not toml ["


def test_existing_config_is_left_alone(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    cfg = home / ".config" / "voicetype" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[stt]\nlanguage = "en-US"\n', encoding="utf-8")
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert cfg.read_text(encoding="utf-8") == '[stt]\nlanguage = "en-US"\n'
    assert not list(cfg.parent.glob("config.toml.bak-*"))


def test_daemon_is_restarted_so_the_new_code_runs(env):
    e, state, home = env
    _pipx_json(state)
    _existing_install(state)
    e["STUB_ACTIVE"] = "1"
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "restart voicetype" in _log(state, "systemctl.log")


def test_without_a_systemd_user_session_the_install_still_succeeds(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    e["STUB_NO_BUS"] = "1"
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "no systemd user session" in r.stderr
    assert "enable --now voicetype" not in _log(state, "systemctl.log")
    # the config is still created
    assert (home / ".config" / "voicetype" / "config.toml").exists()


def test_hotkey_registration_failure_does_not_fail_the_install(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e)  # no --no-hotkey: install-hotkey.sh runs and fails (no KDE)
    assert r.returncode == 0, r.stderr
    assert (home / ".config" / "voicetype" / "config.toml").exists()


def test_summary_reports_what_happened(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "VoiceType is installed." in r.stdout
    assert "extras   : socks" in r.stdout
    assert "service  : enabled and started" in r.stdout

    _pipx_json(state)
    _existing_install(state)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "VoiceType is updated." in r.stdout


def test_help_lists_the_options(env):
    e, state, home = env
    r = _run(e, "--help")
    assert r.returncode == 0
    for flag in ("--reinstall", "--reset-extras", "--no-hotkey"):
        assert flag in r.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell script")
def test_script_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
