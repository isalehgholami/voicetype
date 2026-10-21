#!/usr/bin/env bash
# VoiceType — per-user install, update and repair.
#
# No root, and no "externally-managed-environment" (PEP 668) error on Arch:
# the package lives in a pipx venv that also sees the distro's python-gobject
# (needed by the GTK overlay).
#
#   scripts/install-user.sh [extras] [options]
#
#   extras    comma separated list of: socks, vosk, whisper, hotkey
#             (default: socks). Extras of a previous run are kept, so re-running
#             the script never silently removes a feature you installed.
#   options   --reinstall     rebuild the venv from scratch
#             --reset-extras  install exactly the requested extras
#             --no-hotkey     leave the KDE global shortcut alone
#             --help          show this help
#
# What it does:
#   * installs VoiceType, or updates the code *and* the dependencies of an
#     existing installation (re-run it after `git pull`)
#   * rebuilds a broken venv, missing console scripts or a stale unit file
#   * creates ~/.config/voicetype/config.toml — and repairs it when it is broken
#   * restarts the daemon so the new code is actually running
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

EXTRAS=""
RESET_EXTRAS=0
REINSTALL=0
DO_HOTKEY=1
ACTION="installed"

usage() {
    cat <<'EOF'
Usage: scripts/install-user.sh [extras] [options]

  extras    comma separated: socks, vosk, whisper, hotkey   (default: socks)
            extras installed by a previous run are kept
  options   --reinstall     rebuild the venv from scratch
            --reset-extras  install exactly the requested extras
            --no-hotkey     leave the KDE global shortcut alone
            --help          show this help
EOF
}

info() { printf '==> %s\n' "$*"; }
warn() { printf 'voicetype-install: %s\n' "$*" >&2; }
die() {
    printf 'voicetype-install: %s\n' "$*" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --reinstall) REINSTALL=1 ;;
        --reset-extras) RESET_EXTRAS=1 ;;
        --no-hotkey) DO_HOTKEY=0 ;;
        -h | --help)
            usage
            exit 0
            ;;
        -*) die "unknown option: $1 (try --help)" ;;
        *) EXTRAS="${EXTRAS:+$EXTRAS,}$1" ;;
    esac
    shift
done

command -v pipx >/dev/null 2>&1 || die "pipx is missing:  sudo pacman -S python-pipx"

# ------------------------------------------------------------------ helpers
have() { command -v "$1" >/dev/null 2>&1; }

# last non-empty value of a pipx environment variable ("pipx environment" prints
# an empty "set by user" section first and the derived value later)
pipx_env() {
    pipx environment 2>/dev/null | awk -F= -v k="$1" '$1==k && $2!="" {v=$2} END {print v}'
}

# one field of `pipx list --json` for the voicetype venv
pipx_field() {
    printf '%s' "$PIPX_JSON" | python3 -c '
import json, sys

field = sys.argv[1]
try:
    venv = json.load(sys.stdin).get("venvs", {}).get("voicetype") or {}
except Exception:
    venv = {}
main = (venv.get("metadata") or {}).get("main_package") or {}
if field == "installed":
    print("1" if venv else "0")
elif field == "spec":
    print(main.get("package_or_url") or "")
' "$1"
}

# "socks, vosk" -> "socks vosk" (trimmed, empty parts dropped)
split_extras() {
    printf '%s' "${1:-}" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep -v '^$' || true
}

# the extras of a previous install, taken from pipx's recorded package spec
extras_from_spec() {
    case "$1" in
        *'['*']') printf '%s' "${1##*[}" | tr -d ']' ;;
        *) printf '' ;;
    esac
}

dedupe() { awk 'NF && !seen[$0]++'; }

# escape a path for use in a sed replacement (the delimiter is "#")
sed_escape() { printf '%s' "$1" | sed -e 's/[&#\\]/\\&/g'; }

known_extras() {
    python3 -c '
import sys
try:
    import tomllib
except ImportError:
    sys.exit(1)
with open("pyproject.toml", "rb") as fh:
    print(" ".join(tomllib.load(fh)["project"]["optional-dependencies"]))
' 2>/dev/null || printf '%s' "socks vosk whisper hotkey dev"
}

# ------------------------------------------------------------------ state
PIPX_JSON="$(pipx list --json 2>/dev/null || echo '{}')"
INSTALLED="$(pipx_field installed)"
PREV_SPEC="$(pipx_field spec)"

PIPX_BIN_DIR="$(pipx_env PIPX_BIN_DIR)"
[ -n "$PIPX_BIN_DIR" ] || PIPX_BIN_DIR="$HOME/.local/bin"
VENV_ROOT="$(pipx_env PIPX_LOCAL_VENVS)"
[ -n "$VENV_ROOT" ] || VENV_ROOT="${PIPX_HOME:-$HOME/.local/share/pipx}/venvs"
VENV_DIR="$VENV_ROOT/voicetype"

REQ_EXTRAS="$(split_extras "$EXTRAS")"
PREV_EXTRAS="$(split_extras "$(extras_from_spec "$PREV_SPEC")")"

if [ "$RESET_EXTRAS" -eq 1 ]; then
    FINAL_EXTRAS="$(printf '%s\n' "$REQ_EXTRAS" | dedupe | tr '\n' ' ')"
elif [ -n "$(printf '%s' "$REQ_EXTRAS" | tr -d '[:space:]')" ]; then
    FINAL_EXTRAS="$(printf '%s\n%s\n' "$REQ_EXTRAS" "$PREV_EXTRAS" | dedupe | tr '\n' ' ')"
elif [ -n "$(printf '%s' "$PREV_EXTRAS" | tr -d '[:space:]')" ]; then
    FINAL_EXTRAS="$(printf '%s\n' "$PREV_EXTRAS" | dedupe | tr '\n' ' ')"
else
    FINAL_EXTRAS="socks"
fi
FINAL_EXTRAS="$(printf '%s' "$FINAL_EXTRAS" | sed 's/[[:space:]]*$//')"

KNOWN_EXTRAS="$(known_extras)"
for extra in $FINAL_EXTRAS; do
    case " $KNOWN_EXTRAS " in
        *" $extra "*) ;;
        *) die "unknown extra '$extra' (known extras: $KNOWN_EXTRAS)" ;;
    esac
done

if [ -n "$FINAL_EXTRAS" ]; then
    EXTRAS_SUFFIX="[$(printf '%s' "$FINAL_EXTRAS" | tr ' ' ',')]"
else
    EXTRAS_SUFFIX=""
fi

# ------------------------------------------------------------------ install
# pipx's uv backend runs uv in verbose mode → filter its debug chatter away,
# but keep the "Installed ... / ~ voicetype==..." summary lines.
run_venv_pip() {
    local out rc=0
    out="$(pipx runpip voicetype install "$@" 2>&1)" || rc=$?
    printf '%s\n' "$out" | grep -v '^DEBUG ' | grep -v '^[[:space:]]*$' || true
    return "$rc"
}

install_fresh() {
    ACTION="installed"
    info "installing voicetype${EXTRAS_SUFFIX:+" ($FINAL_EXTRAS)"} into a new pipx venv"
    pipx install --system-site-packages ".${EXTRAS_SUFFIX}"
}

update_existing() {
    ACTION="updated"
    info "updating the existing installation${EXTRAS_SUFFAS:+" ($FINAL_EXTRAS)"}"
    # 1) dependencies — needs the network, so a failure here is not fatal
    if run_venv_pip --no-build-isolation "$ROOT$EXTRAS_SUFFIX"; then
        :
    elif run_venv_pip "$ROOT$EXTRAS_SUFFIX"; then
        :
    else
        warn "could not refresh the dependencies (no network?) — updating the code only"
    fi
    # 2) the code itself — no network needed, so this must always work
    if run_venv_pip --force-reinstall --no-deps --no-build-isolation "$ROOT"; then
        return 0
    fi
    warn "retrying the code update with build isolation (needs the network)"
    run_venv_pip --force-reinstall --no-deps "$ROOT"
}

clean_reinstall() {
    ACTION="reinstalled"
    warn "rebuilding the pipx venv from scratch"
    pipx uninstall voicetype >/dev/null 2>&1 || true
    pipx install --system-site-packages ".${EXTRAS_SUFFIX}"
}

verify_install() {
    [ -x "$PIPX_BIN_DIR/voicetype" ] || return 1
    "$PIPX_BIN_DIR/voicetype" --version >/dev/null 2>&1 || return 1
    return 0
}

if [ "$INSTALLED" = "1" ] && [ "$REINSTALL" -eq 1 ]; then
    warn "--reinstall: dropping the existing installation"
    clean_reinstall
elif [ "$INSTALLED" = "1" ]; then
    if [ ! -d "$VENV_DIR" ]; then
        warn "the pipx venv is gone but pipx still lists voicetype — rebuilding it"
        clean_reinstall
    else
        update_existing || clean_reinstall
    fi
else
    install_fresh
fi

if ! verify_install; then
    warn "the installation is not usable — rebuilding it"
    clean_reinstall
fi
verify_install || die "the installation is broken even after a clean reinstall (see the errors above)"

INSTALLED_VERSION="$("$PIPX_BIN_DIR/voicetype" --version 2>/dev/null | head -n1 || true)"
info "voicetype is $ACTION: ${INSTALLED_VERSION:-unknown version} → $PIPX_BIN_DIR/voicetype"

# ------------------------------------------------------------------ service
LIB_DIR="$HOME/.local/lib/voicetype"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$LIB_DIR" "$UNIT_DIR"
install -m755 packaging/wait-display "$LIB_DIR/wait-display"

sed -e "s#/usr/lib/voicetype/wait-display#$(sed_escape "$LIB_DIR/wait-display")#" \
    -e "s#/usr/bin/voicetype#$(sed_escape "$PIPX_BIN_DIR/voicetype")#g" \
    packaging/voicetype.service > "$UNIT_DIR/voicetype.service"

# the daemon has to read the same config file the installer writes
if [ -n "${VOICETYPE_CONFIG:-}" ]; then
    sed -i "s#^Environment=PYTHONUNBUFFERED=1#Environment=PYTHONUNBUFFERED=1\nEnvironment=VOICETYPE_CONFIG=$(sed_escape "$VOICETYPE_CONFIG")#" \
        "$UNIT_DIR/voicetype.service"
fi

systemd_user_available() {
    have systemctl || return 1
    systemctl --user show-environment >/dev/null 2>&1
}

SERVICE_STATE="not started (no systemd user session)"
if systemd_user_available; then
    systemctl --user daemon-reload
    if systemctl --user is-active --quiet voicetype; then
        systemctl --user restart voicetype
        SERVICE_STATE="restarted (the new code is running)"
    elif systemctl --user enable --now voicetype; then
        SERVICE_STATE="enabled and started"
    else
        SERVICE_STATE="could not be started — see: journalctl --user -u voicetype"
    fi
else
    warn "no systemd user session here — start the daemon later with: systemctl --user enable --now voicetype"
fi

# ------------------------------------------------------------------ config
ensure_config() {
    local vt="$PIPX_BIN_DIR/voicetype"
    local py="$VENV_DIR/bin/python"
    if [ -x "$vt" ] && "$vt" config init --repair; then
        return 0
    fi
    warn "could not run 'voicetype config init' — writing the config from the checkout"
    if [ -x "$py" ]; then
        PYTHONPATH="$ROOT" "$py" -c '
from voicetype.config import repair_config
r = repair_config()
print(f"{r.status}: {r.path}" + (f" (backup: {r.backup})" if r.backup else ""))
'
    else
        PYTHONPATH="$ROOT" python3 -c '
from voicetype.config import repair_config
r = repair_config()
print(f"{r.status}: {r.path}" + (f" (backup: {r.backup})" if r.backup else ""))
'
    fi
}

CONFIG_LINE="$(ensure_config)" || die "could not create the configuration file"
info "config: $CONFIG_LINE"

# ------------------------------------------------------------------ hotkey
if [ "$DO_HOTKEY" -eq 1 ]; then
    PIPX_BIN_DIR="$PIPX_BIN_DIR" ./scripts/install-hotkey.sh "${HOTKEY:-Meta+Alt+V}" \
        || warn "the shortcut was not registered — see README.md (KDE) or bind it in your DE"
fi

# ------------------------------------------------------------------ summary
cat <<EOF

VoiceType is $ACTION.
  extras   : ${FINAL_EXTRAS:-none}
  config   : $CONFIG_LINE
  service  : $SERVICE_STATE

Next:
  1. sudo pacman -S ydotool && systemctl --user enable --now ydotool   (needed for paste on Wayland)
  2. voicetype doctor          every required line should be [✓]
  3. Behind a firewall?  set  [network] proxy = "socks5h://127.0.0.1:1080"  in ~/.config/voicetype/config.toml
                         then  voicetype reload && voicetype net-test
EOF

if ! printf '%s' "$PATH" | tr ':' '\n' | grep -qx "$PIPX_BIN_DIR"; then
    warn "$PIPX_BIN_DIR is not in your PATH — run 'pipx ensurepath' once"
fi
