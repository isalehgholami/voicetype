"""The install script must update, repair and configure an existing installation.

The script is exercised against fake ``pipx``/``systemctl`` binaries so that the
real user home is never touched.
"""

import ast
import json
import os
import re
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
    [ -f "$STATE/voicetype.active" ] && exit 0
    exit 3
    ;;
"--user daemon-reload")
    [ "${STUB_FAIL_RELOAD:-0}" = "1" ] && exit 1
    exit 0
    ;;
"--user enable")
    # "enable --now" starts the unit, a plain "enable" only registers it
    if [ "${3:-}" = "--now" ] && [ "${4:-}" = "voicetype" ]; then
        [ "${STUB_NO_START:-0}" = "1" ] && exit 1
        : > "$STATE/voicetype.active"
    fi
    exit 0
    ;;
"--user restart")
    if [ "${3:-}" = "voicetype" ]; then
        [ "${STUB_NO_START:-0}" = "1" ] && exit 1
        : > "$STATE/voicetype.active"
    fi
    exit 0
    ;;
"--user stop")
    rm -f "$STATE/voicetype.active"
    exit 0
    ;;
esac
exit 0
"""

# a sudo that never asks for a password — the tests must never need a real one
SUDO_STUB = """#!/usr/bin/env bash
set -u
if [ "$1" = "-v" ]; then exit 0; fi
printf '%s\\n' "$*" >> "$STUB_STATE/sudo.log"
exec "$@"
"""

# nothing is installed, and every install is logged
APT_GET_STUB = """#!/usr/bin/env bash
set -u
printf '%s\\n' "$*" >> "$STUB_STATE/apt.log"
exit 0
"""

DPKG_QUERY_STUB = """#!/usr/bin/env bash
exit 1
"""

PACMAN_STUB = """#!/usr/bin/env bash
set -u
printf '%s\\n' "$*" >> "$STUB_STATE/pacman.log"
case "$1" in
-Qq) exit 1 ;;   # nothing is installed
esac
exit 0
"""

ID_STUB = """#!/usr/bin/env bash
if [ "$1" = "-u" ]; then echo 0; exit 0; fi
echo root
exit 0
"""

KWRITECONFIG_STUB = """#!/usr/bin/env bash
set -u
printf '%s\\n' "$*" >> "$STUB_STATE/kwriteconfig.log"
exit 0
"""

# a gsettings that keeps the custom keybindings in a file
GSETTINGS_STUB = """#!/usr/bin/env python3
import os
import sys

state = os.environ["STUB_STATE"]
with open(os.path.join(state, "gsettings.log"), "a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")

store = os.path.join(state, "gsettings-custom-keybindings")
command = sys.argv[1]
if command == "list-schemas":
    print("org.gnome.settings-daemon.plugins.media-keys")
elif command == "get":
    if os.path.exists(store):
        with open(store, encoding="utf-8") as fh:
            sys.stdout.write(fh.read())
    else:
        print("[]")
elif command == "set":
    with open(store, "w", encoding="utf-8") as fh:
        fh.write(sys.argv[-1])
"""

# a user that is in no group at all
ID_NO_GROUPS_STUB = """#!/usr/bin/env bash
case "$1" in
-u) echo 1000 ;;
-nG) echo "users" ;;
-un) echo "tester" ;;
*) echo "tester" ;;
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
    runtime = tmp_path / "run"
    for d in (home, state, bindir, runtime):
        d.mkdir(parents=True, exist_ok=True)
    _write_stub(bindir / "pipx", PIPX_STUB)
    _write_stub(bindir / "systemctl", SYSTEMCTL_STUB)
    e = dict(os.environ)
    e.update(
        HOME=str(home),
        PATH=f"{bindir}:{e['PATH']}",
        STUB_STATE=str(state),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_RUNTIME_DIR=str(runtime),
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
    assert "install --system-site-packages .[socks,translate]" in log
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
    e["XDG_CURRENT_DESKTOP"] = "SomeDesktop"
    e["XDG_SESSION_DESKTOP"] = ""
    r = _run(e)  # no --no-hotkey: the shortcut cannot be registered
    assert r.returncode == 0, r.stderr
    assert (home / ".config" / "voicetype" / "config.toml").exists()
    # the user is told what to add by hand
    assert "add the binding by hand" in r.stdout


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
    for flag in ("--reinstall", "--reset-extras", "--no-hotkey", "--system-deps", "--no-service",
                 "--desktop", "--hotkey-only"):
        assert flag in r.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell script")
def test_script_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------
# the merged installer: distribution awareness, reporting, repair
# --------------------------------------------------------------------------


def _os_release(path: Path, ident: str, name: str = "Test Linux") -> Path:
    path.write_text(f'NAME="{name}"\nID={ident}\nID_LIKE={ident}\n', encoding="utf-8")
    return path


def test_a_new_config_gets_a_short_guide(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    cfg = (home / ".config" / "voicetype" / "config.toml").read_text(encoding="utf-8")
    assert "quick guide" in cfg
    assert "voicetype doctor" in cfg
    # the sample itself is still there
    assert "[stt]" in cfg


def test_an_existing_config_is_never_rewritten(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    cfg = home / ".config" / "voicetype" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[stt]\nlanguage = "en-US"\n', encoding="utf-8")
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert cfg.read_text(encoding="utf-8") == '[stt]\nlanguage = "en-US"\n'


def test_every_step_reports_what_it_did(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    for step in ("Environment", "System packages", "Groups and audio", "Microphone",
                 "Python environment", "Service", "Configuration", "Shortcut", "Verification"):
        assert step in r.stdout
    assert "VoiceType is installed." in r.stdout


def test_the_system_packages_are_only_checked_by_default(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    bindir = Path(e["PATH"].split(":")[0])
    _write_stub(bindir / "sudo", SUDO_STUB)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    # nothing was installed without --system-deps
    assert not (state / "sudo.log").exists()


def test_debian_package_names_are_used(env, tmp_path):
    e, state, home = env
    _pipx_json(state, installed=False)
    bindir = Path(e["PATH"].split(":")[0])
    _write_stub(bindir / "sudo", SUDO_STUB)
    _write_stub(bindir / "apt-get", APT_GET_STUB)
    _write_stub(bindir / "dpkg-query", DPKG_QUERY_STUB)
    e["VOICETYPE_OS_RELEASE"] = str(_os_release(tmp_path / "os-release", "debian"))
    r = _run(e, "--no-hotkey", "--system-deps")
    assert r.returncode == 0, r.stderr
    log = _log(state, "apt.log")
    # the Debian names, not the Arch ones
    assert "gir1.2-gtklayershell-0.0" in log
    assert "libgtk-4-1" in log


def test_arch_package_names_are_used(env, tmp_path):
    e, state, home = env
    _pipx_json(state, installed=False)
    bindir = Path(e["PATH"].split(":")[0])
    _write_stub(bindir / "sudo", SUDO_STUB)
    _write_stub(bindir / "pacman", PACMAN_STUB)
    e["VOICETYPE_OS_RELEASE"] = str(_os_release(tmp_path / "os-release", "arch"))
    r = _run(e, "--no-hotkey", "--system-deps")
    assert r.returncode == 0, r.stderr
    log = _log(state, "pacman.log")
    assert "--noconfirm gtk4" in log
    assert "--noconfirm gtk4-layer-shell" in log


def test_the_script_refuses_to_run_as_root(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    bindir = Path(e["PATH"].split(":")[0])
    _write_stub(bindir / "id", ID_STUB)
    r = _run(e, "--no-hotkey")
    assert r.returncode != 0
    assert "as root" in r.stderr


def test_no_service_leaves_the_unit_alone(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey", "--no-service")
    assert r.returncode == 0, r.stderr
    assert not (home / ".config" / "systemd" / "user" / "voicetype.service").exists()
    assert "enable --now voicetype" not in _log(state, "systemctl.log")
    assert "skipped (--no-service)" in r.stdout


def test_the_microphone_test_can_be_skipped(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey", "--no-mic-check")
    assert r.returncode == 0, r.stderr
    assert "skipped (--no-mic-check)" in r.stdout


def test_the_hotkey_combination_is_forwarded(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    bindir = Path(e["PATH"].split(":")[0])
    _write_stub(bindir / "kwriteconfig6", KWRITECONFIG_STUB)
    e["XDG_CURRENT_DESKTOP"] = "KDE"
    e["XDG_SESSION_DESKTOP"] = ""
    r = _run(e, "--hotkey", "Ctrl+Alt+Space")
    assert r.returncode == 0, r.stderr
    assert "Ctrl+Alt+Space" in _log(state, "kwriteconfig.log")


def test_the_extras_survive_a_lost_pipx_metadata(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey", "vosk")
    assert r.returncode == 0, r.stderr
    # pipx forgets the extras (a hand-made venv, an upgrade, ...) — the installer
    # still remembers them
    _pipx_json(state, spec="/src/voicetype", installed=True)
    _existing_install(state)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "vosk" in _log(state, "pipx.log")


def test_an_unknown_option_is_rejected(env):
    e, state, home = env
    r = _run(e, "--nope")
    assert r.returncode != 0
    assert "unknown option" in r.stderr


def test_no_temporary_files_are_left_behind(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    unit_dir = home / ".config" / "systemd" / "user"
    leftovers = [p.name for p in unit_dir.glob(".voicetype.service.*")] if unit_dir.exists() else []
    assert leftovers == []
    config_dir = home / ".config" / "voicetype"
    assert [p.name for p in config_dir.glob("*.tmp.*")] == []


def test_the_groups_are_added_with_a_comma_separated_list(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    bindir = Path(e["PATH"].split(":")[0])
    _write_stub(bindir / "sudo", SUDO_STUB)
    _write_stub(bindir / "id", ID_NO_GROUPS_STUB)
    r = _run(e, "--no-hotkey", "--system-deps")
    assert r.returncode == 0, r.stderr
    # usermod expects "input,audio" — not "input audio"
    assert "usermod -aG input,audio tester" in _log(state, "sudo.log")


def test_a_failing_daemon_reload_does_not_abort_the_install(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    e["STUB_FAIL_RELOAD"] = "1"
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    # the rest of the installation still happened
    assert (home / ".config" / "voicetype" / "config.toml").exists()
    assert "VoiceType is installed." in r.stdout


def test_a_config_that_cannot_be_repaired_is_reported(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    cfg = home / ".config" / "voicetype" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # a valid file, but a value the daemon cannot use
    cfg.write_text('[translate]\nenabled = true\nlanguage = "zzz-not-a-language"\n', encoding="utf-8")
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "could not be repaired automatically" in r.stdout
    # the user's file was not thrown away
    assert cfg.read_text(encoding="utf-8") == '[translate]\nenabled = true\nlanguage = "zzz-not-a-language"\n'
    assert not list(cfg.parent.glob("config.toml.bak-*"))


# --------------------------------------------------------------------------
# the daemon: started, checked and reported
# --------------------------------------------------------------------------


def test_the_daemon_is_started_after_the_installation(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    log = _log(state, "systemctl.log")
    assert "enable --now voicetype" in log
    assert "the daemon is running" in r.stdout
    assert "service  : enabled and started" in r.stdout


def test_a_daemon_that_does_not_start_is_reported(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    e["STUB_NO_START"] = "1"
    e["VOICETYPE_DAEMON_WAIT_TRIES"] = "4"  # do not wait 15 s in the test
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "the daemon does not answer" in r.stdout
    # the summary does not claim that everything is fine
    assert "service  : enabled and started" not in r.stdout


def test_the_translate_extra_is_installed_even_without_translation(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    # googletrans is always there: the daemon validates the config with it
    log = _log(state, "pipx.log")
    assert "translate" in log
    assert "extras   : socks translate" in r.stdout


def test_the_extras_are_remembered_comma_separated(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey", "vosk")
    assert r.returncode == 0, r.stderr
    remembered = home / ".local" / "state" / "voicetype" / "extras"
    assert remembered.exists()
    content = remembered.read_text(encoding="utf-8")
    assert "vosk" in content and "translate" in content
    # the file is read back with split_extras(), which splits on commas
    assert "," in content


def test_a_tilde_in_the_config_path_is_expanded(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    e["VOICETYPE_CONFIG"] = "~/my-voicetype.toml"
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    # the shell does not expand a ~ that comes from an environment variable
    assert (home / "my-voicetype.toml").exists()
    unit = (home / ".config" / "systemd" / "user" / "voicetype.service").read_text(encoding="utf-8")
    assert f'Environment="VOICETYPE_CONFIG={home}/my-voicetype.toml"' in unit


def test_the_unit_escapes_a_quotes_in_the_config_path(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    e["VOICETYPE_CONFIG"] = '~/we"ird.toml'
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    unit = (home / ".config" / "systemd" / "user" / "voicetype.service").read_text(encoding="utf-8")
    # systemd reads the value inside double quotes → the quote has to be escaped
    assert 'we\\"ird.toml' in unit


def test_an_unusable_config_is_not_checked_off(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    cfg = home / ".config" / "voicetype" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[translate]\nenabled = true\nlanguage = "zzz-not-a-language"\n', encoding="utf-8")
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    # the checklist must not show a green tick for a config that does not work
    assert "[!] configuration file" in r.stdout
    assert "[✓] configuration file" not in r.stdout


def test_a_working_config_is_checked_off(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "[✓] configuration file" in r.stdout


# --------------------------------------------------------------------------
# the defects an independent review found
# --------------------------------------------------------------------------


def test_the_unit_is_written_even_without_a_systemd_session(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    e["STUB_NO_BUS"] = "1"
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    # writing a file needs no systemd session — only starting the daemon does
    unit = home / ".config" / "systemd" / "user" / "voicetype.service"
    assert unit.exists()
    assert "ExecStart=" in unit.read_text(encoding="utf-8")
    # ... but without a session nothing is enabled or started
    assert "enable --now voicetype" not in _log(state, "systemctl.log")
    assert "enable voicetype" not in _log(state, "systemctl.log")


def test_the_reported_backup_is_the_one_of_this_run(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    cfg = home / ".config" / "voicetype" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # an old backup with a far future timestamp must not be reported
    (cfg.parent / "config.toml.bak-99990101-000000").write_text("old", encoding="utf-8")
    cfg.write_text("this is = not toml [", encoding="utf-8")
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "bak-99990101-000000" not in r.stdout
    assert "backed up as" in r.stdout


def test_a_failed_restart_is_not_checked_off(env):
    e, state, home = env
    _pipx_json(state)
    _existing_install(state)
    e["STUB_ACTIVE"] = "1"  # the daemon runs ...
    e["STUB_NO_START"] = "1"  # ... but the restart fails
    e["VOICETYPE_DAEMON_WAIT_TRIES"] = "4"
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "the daemon could not be restarted" in r.stdout
    # the checklist must not show a green tick for a daemon that did not restart
    assert "[!] the daemon" in r.stdout
    assert "restart failed" in r.stdout
    assert "→ running" not in r.stdout


def test_a_config_of_another_user_does_not_abort_the_install(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    # "~root/..." is expanded by the app, but the file is not writable
    e["VOICETYPE_CONFIG"] = "~root/voicetype.toml"
    r = _run(e, "--no-hotkey")
    assert r.returncode == 0, r.stderr
    assert "VoiceType is installed." in r.stdout
    assert "could not create the config" in r.stdout


def test_the_audio_tools_package_is_mapped_per_distribution():
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"REQUIREMENTS='(.*?)'", text, re.S)
    assert match, "the package table is missing"
    columns = {}
    for line in match.group(1).splitlines():
        role, _tool, *pkgs = line.split("|")
        columns[role] = pkgs
    # the pw-* tools live in their own package on most distributions
    assert columns["audio tools"] == [
        "pipewire-audio",  # arch
        "pipewire-bin",  # debian
        "pipewire-utils",  # fedora
        "pipewire-tools",  # suse
        "pipewire-tools",  # alpine
        "pipewire",  # void
    ]


# --------------------------------------------------------------------------
# the global shortcut: every desktop keeps it in a different place
# --------------------------------------------------------------------------


def _gnome_bindings(state: Path) -> list:
    """The custom keybindings that the gsettings stub keeps."""
    store = state / "gsettings-custom-keybindings"
    if not store.exists():
        return []
    return ast.literal_eval(store.read_text(encoding="utf-8"))


def test_the_shortcut_is_registered_on_gnome(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    bindir = Path(e["PATH"].split(":")[0])
    _write_stub(bindir / "gsettings", GSETTINGS_STUB)
    # a binding of the user has to survive
    (state / "gsettings-custom-keybindings").write_text(
        "[{'name': 'Other', 'command': 'other', 'binding': '<Super>e'}]", encoding="utf-8"
    )
    e["XDG_CURRENT_DESKTOP"] = "GNOME"
    e["XDG_SESSION_DESKTOP"] = ""
    r = _run(e)
    assert r.returncode == 0, r.stderr
    bindings = _gnome_bindings(state)
    names = [b.get("name") for b in bindings]
    assert "VoiceType Toggle" in names
    assert "Other" in names  # the binding of the user is still there
    ours = next(b for b in bindings if b.get("name") == "VoiceType Toggle")
    assert ours["binding"] == "<Super><Alt>v"
    assert ours["command"].endswith("voicetype-toggle")


def test_the_shortcut_is_registered_on_hyprland(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    e["XDG_CURRENT_DESKTOP"] = "Hyprland"
    e["XDG_SESSION_DESKTOP"] = ""
    r = _run(e)
    assert r.returncode == 0, r.stderr
    ours = (home / ".config" / "hypr" / "voicetype.conf").read_text(encoding="utf-8")
    assert "bind = SUPER_ALT, v, exec," in ours
    assert "voicetype-toggle" in ours
    # hyprland.conf has to read the file
    main = (home / ".config" / "hypr" / "hyprland.conf").read_text(encoding="utf-8")
    assert "voicetype.conf" in main
    assert "source" in main


def test_the_hyprland_binding_is_not_written_twice(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    e["XDG_CURRENT_DESKTOP"] = "Hyprland"
    e["XDG_SESSION_DESKTOP"] = ""
    assert _run(e).returncode == 0
    assert _run(e).returncode == 0
    main = (home / ".config" / "hypr" / "hyprland.conf").read_text(encoding="utf-8")
    assert main.count("voicetype.conf") == 1
    ours = (home / ".config" / "hypr" / "voicetype.conf").read_text(encoding="utf-8")
    assert ours.count("bind = ") == 1


def test_the_shortcut_is_registered_on_sway(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    cfg = home / ".config" / "sway" / "config"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("bindsym $mod+Return exec kitty\n", encoding="utf-8")
    e["XDG_CURRENT_DESKTOP"] = "sway"
    e["XDG_SESSION_DESKTOP"] = ""
    r = _run(e)
    assert r.returncode == 0, r.stderr
    text = cfg.read_text(encoding="utf-8")
    # the config of the user is kept
    assert "bindsym $mod+Return exec kitty" in text
    assert "bindsym Super+Alt+v, exec," in text
    # a second run replaces the binding instead of adding a second one
    assert _run(e).returncode == 0
    text = cfg.read_text(encoding="utf-8")
    assert text.count("bindsym Super+Alt+v") == 1


def test_the_shortcut_is_registered_on_i3(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    e["XDG_CURRENT_DESKTOP"] = "i3"
    e["XDG_SESSION_DESKTOP"] = ""
    r = _run(e)
    assert r.returncode == 0, r.stderr
    text = (home / ".config" / "i3" / "config").read_text(encoding="utf-8")
    # i3 knows the X11 modifiers, not the aliases of sway
    assert "bindsym Mod4+Mod1+v, exec," in text


def test_the_desktop_can_be_chosen_by_hand(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    e["XDG_CURRENT_DESKTOP"] = ""
    e["XDG_SESSION_DESKTOP"] = ""
    r = _run(e, "--desktop", "hyprland")
    assert r.returncode == 0, r.stderr
    assert (home / ".config" / "hypr" / "voicetype.conf").exists()


def test_an_unknown_desktop_is_rejected(env):
    e, state, home = env
    _pipx_json(state, installed=False)
    r = _run(e, "--desktop", "windows")
    assert r.returncode != 0
    assert "unknown desktop" in r.stderr


def test_the_shortcut_can_be_registered_without_an_installation(env, tmp_path):
    e, state, home = env
    # a PATH without pipx: the shortcut does not need a venv
    minimal = tmp_path / "minimal-bin"
    minimal.mkdir()
    _write_stub(minimal / "kwriteconfig6", KWRITECONFIG_STUB)
    e["PATH"] = f"{minimal}:/usr/bin:/bin"
    e["XDG_CURRENT_DESKTOP"] = "KDE"
    e["XDG_SESSION_DESKTOP"] = ""
    r = _run(e, "--hotkey-only", "--hotkey", "Meta+Alt+V")
    assert r.returncode == 0, r.stderr
    # nothing was installed
    assert not (state / "pipx.log").exists()
    assert not (home / ".config" / "systemd" / "user" / "voicetype.service").exists()
    # but the shortcut was registered
    assert "Meta+Alt+V" in _log(state, "kwriteconfig.log")


