#!/usr/bin/env bash
# VoiceType — install, update and repair VoiceType for the current user.
#
# One script for every Linux distribution:
#   1. checks the environment (python, pipx, session type)
#   2. checks the system packages — and installs them with --system-deps
#   3. checks the groups and the audio services
#   4. tests the microphone
#   5. installs VoiceType into a pipx venv (no root, no PEP 668 error)
#   6. creates ~/.config/voicetype/config.toml (with a short guide)
#   7. writes the systemd *user* unit
#   8. starts the daemon and waits until it answers
#   9. registers the global shortcut
#  10. verifies everything and prints a report
#
# Usage: scripts/install-user.sh [extras] [options]
#
#   extras    comma separated list of: socks, vosk, whisper, hotkey, translate
#             (default: socks). Extras of a previous run are kept, so re-running
#             the script never silently removes a feature you installed.
#   options   --system-deps  install the missing system packages (needs sudo)
#             --reinstall    rebuild the venv from scratch
#             --reset-extras install exactly the requested extras
#             --no-hotkey    leave the global shortcut alone
#             --no-service   do not touch the systemd user unit
#             --no-mic-check skip the microphone test
#             --hotkey COMBO the shortcut to register (default: Meta+Alt+V)
#             -v, --verbose  show the full output of every command
#             -q, --quiet    only report problems
#             --help         show this help
#
# The script never needs root: everything is installed into your own account.
# It is idempotent — run it again after `git pull`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ORIGINAL_PATH="$PATH"
PATH_FIXED=0

# ------------------------------------------------------------------ options
EXTRAS=""
RESET_EXTRAS=0
REINSTALL=0
DO_HOTKEY=1
DO_SERVICE=1
DO_SYSTEM_DEPS=0
DO_MIC_CHECK=1
HOTKEY_COMBO="${HOTKEY:-Meta+Alt+V}"
VERBOSE=0
QUIET=0

usage() {
    cat <<'EOF'
Usage: scripts/install-user.sh [extras] [options]

  extras    comma separated: socks, vosk, whisper, hotkey, translate   (default: socks)
            extras installed by a previous run are kept
  options   --system-deps  install the missing system packages (needs sudo)
            --reinstall     rebuild the venv from scratch
            --reset-extras  install exactly the requested extras
            --no-hotkey     leave the global shortcut alone
            --no-service    do not touch the systemd user unit
            --no-mic-check  skip the microphone test
            --hotkey COMBO  shortcut to register (default: Meta+Alt+V)
            -v, --verbose   show the full output of every command
            -q, --quiet     only report problems
            --help          show this help

Examples:
  scripts/install-user.sh                       # install / update, keep the old extras
  scripts/install-user.sh socks,vosk            # add the offline engine
  scripts/install-user.sh --system-deps         # also install the system packages
  HOTKEY=Ctrl+Alt+Space scripts/install-user.sh
EOF
}

# ------------------------------------------------------------------ output
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_B=$'\033[1m'; C_D=$'\033[2m'; C_R=$'\033[31m'; C_G=$'\033[32m'
    C_Y=$'\033[33m'; C_C=$'\033[36m'; C_0=$'\033[0m'
else
    C_B=""; C_D=""; C_R=""; C_G=""; C_Y=""; C_C=""; C_0=""
fi

info() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*"; }
note() { [ "$QUIET" -eq 1 ] || printf '    %s[·]%s %s\n' "$C_D" "$C_0" "$*"; }
warn() { printf 'voicetype-install: %s\n' "$*" >&2; }
die() {
    printf 'voicetype-install: %s\n' "$*" >&2
    exit 1
}

STEP_NO=0
STEP_TOTAL=10
step() {
    STEP_NO=$((STEP_NO + 1))
    [ "$QUIET" -eq 1 ] || printf '\n%s[%d/%d]%s %s%s%s\n' "$C_C" "$STEP_NO" "$STEP_TOTAL" "$C_0" "$C_B" "$*" "$C_0"
}
res_ok() { [ "$QUIET" -eq 1 ] || printf '    %s[✓]%s %s\n' "$C_G" "$C_0" "$*"; }
res_warn() { printf '    %s[!]%s %s\n' "$C_Y" "$C_0" "$*"; }
res_skip() { [ "$QUIET" -eq 1 ] || printf '    %s[-]%s %s\n' "$C_D" "$C_0" "$*"; }
res_info() { [ "$QUIET" -eq 1 ] || printf '    %s[·]%s %s\n' "$C_D" "$C_0" "$*"; }

# final checklist: "status|what|detail"
CHECKS=()
check_add() { CHECKS+=("$1|$2|${3:-}"); }

# ------------------------------------------------------------------ cleanup
WORK_DIR=""
UNIT_TMP=""
cleanup() {
    rc=$?
    if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
        rm -rf "$WORK_DIR"
    fi
    if [ -n "$UNIT_TMP" ] && [ -f "$UNIT_TMP" ]; then
        rm -f "$UNIT_TMP"
    fi
    if [ "$rc" -ne 0 ] && [ "$QUIET" -eq 0 ]; then
        printf '\nvoicetype-install: the installation did not finish (exit %d)\n' "$rc" >&2
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# ------------------------------------------------------------------ helpers
have() { command -v "$1" >/dev/null 2>&1; }

# is there a systemd *user* session we can talk to?
systemd_user_available() {
    have systemctl || return 1
    systemctl --user show-environment >/dev/null 2>&1
}

# last non-empty value of a pipx environment variable ("pipx environment" prints
# an empty "set by user" section first and the derived value later)
pipx_env() {
    pipx environment 2>/dev/null | awk -F= -v k="$1" '$1==k && $2!="" {v=$2} END {print v}' || true
}

# one field of `pipx list --json` for the voicetype venv
pipx_field() {
    printf '%s' "$PIPX_JSON" | "$PYTHON" -c '
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

# escape a value for a systemd "Environment=" line — the value sits inside
# double quotes, so a backslash and a quote have to be escaped (C style)
systemd_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

known_extras() {
    "$PYTHON" -c '
import sys
try:
    import tomllib
except ImportError:
    sys.exit(1)
with open("pyproject.toml", "rb") as fh:
    print(" ".join(tomllib.load(fh)["project"]["optional-dependencies"]))
' 2>/dev/null || printf '%s' "socks vosk whisper hotkey translate dev"
}

# ------------------------------------------------------------------ arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --reinstall) REINSTALL=1 ;;
        --reset-extras) RESET_EXTRAS=1 ;;
        --no-hotkey) DO_HOTKEY=0 ;;
        --no-service) DO_SERVICE=0 ;;
        --no-mic-check) DO_MIC_CHECK=0 ;;
        --system-deps) DO_SYSTEM_DEPS=1 ;;
        --hotkey)
            [ $# -ge 2 ] || die "--hotkey needs a combination (e.g. --hotkey Meta+Alt+V)"
            HOTKEY_COMBO="$2"
            shift
            ;;
        --hotkey=*) HOTKEY_COMBO="${1#*=}" ;;
        -v | --verbose) VERBOSE=1 ;;
        -q | --quiet) QUIET=1 ;;
        -h | --help)
            usage
            exit 0
            ;;
        -*) die "unknown option: $1 (try --help)" ;;
        *) EXTRAS="${EXTRAS:+$EXTRAS,}$1" ;;
    esac
    shift
done

# ------------------------------------------------------------------ preflight
if [ "$(id -u)" -eq 0 ]; then
    die "do not run this script as root — it installs into your own user account.
     Run it as your normal user; add --system-deps if the system packages are missing."
fi

if [ -z "${HOME:-}" ]; then
    HOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6 || true)"
    export HOME
fi
[ -n "${HOME:-}" ] && [ -d "${HOME:-}" ] || die "HOME is not set — cannot install anything"

PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || die "python3 is missing — install it first (e.g. 'sudo apt install python3')"
if [ "$("$PYTHON" -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)' 2>/dev/null || echo 0)" != "1" ]; then
    die "python 3.11 or newer is required (found: $("$PYTHON" -V 2>&1))"
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/voicetype-install.XXXXXX")"

# a second installer must not fight the first one over the same files
lock_dir="${XDG_RUNTIME_DIR:-}"
if [ -z "$lock_dir" ] || [ ! -d "$lock_dir" ]; then
    # no runtime directory (ssh, container) — a private directory in /tmp
    lock_dir="/tmp/voicetype-$(id -u)"
    mkdir -p "$lock_dir" 2>/dev/null || lock_dir=""
    chmod 700 "$lock_dir" 2>/dev/null || true
fi
if have flock && [ -n "$lock_dir" ] && [ -d "$lock_dir" ]; then
    lock_file="$lock_dir/install.lock"
    # a directory that cannot be written to must not look like a running
    # installation — so the lock is only used when it can be created
    if : > "$lock_file" 2>/dev/null; then
        if exec 9>>"$lock_file"; then
            if ! flock -n 9; then
                die "another VoiceType installation is already running — wait for it to finish"
            fi
        else
            warn "cannot open $lock_file — no lock against a parallel installation"
        fi
    else
        warn "cannot write $lock_file — no lock against a parallel installation"
    fi
fi

# ------------------------------------------------------------------ distro
# the os-release file can be overridden (exotic systems, tests)
OS_RELEASE_FILE="${VOICETYPE_OS_RELEASE:-/etc/os-release}"

os_field() {
    local key="$1" file="${2:-$OS_RELEASE_FILE}" line=""
    [ -r "$file" ] || return 1
    line="$(grep -m1 "^${key}=" "$file" 2>/dev/null || true)"
    [ -n "$line" ] || return 1
    line="${line#*=}"
    line="${line%\"}"; line="${line#\"}"
    line="${line%\'}"; line="${line#\'}"
    printf '%s' "$line"
}

OS_ID="$(os_field ID || echo unknown)"
OS_NAME="$(os_field NAME || echo "$OS_ID")"
OS_LIKE="$(os_field ID_LIKE || echo "")"

PM=""
PM_FAMILY="unknown"
case "$OS_ID $OS_LIKE" in
    *arch*) PM=pacman; PM_FAMILY=arch ;;
    *debian* | *ubuntu*) PM=apt-get; PM_FAMILY=debian ;;
    *fedora* | *rhel* | *centos*) PM=dnf; PM_FAMILY=fedora ;;
    *suse* | *opensuse*) PM=zypper; PM_FAMILY=suse ;;
    *alpine*) PM=apk; PM_FAMILY=alpine ;;
    *void*) PM=xbps-install; PM_FAMILY=void ;;
esac
if [ -n "$PM" ] && ! have "$PM"; then
    PM=""
fi
if [ -z "$PM" ]; then
    # no usable /etc/os-release (containers, exotic systems) — guess from the tools
    if have pacman; then PM=pacman; PM_FAMILY=arch
    elif have apt-get; then PM=apt-get; PM_FAMILY=debian
    elif have dnf; then PM=dnf; PM_FAMILY=fedora
    elif have yum; then PM=yum; PM_FAMILY=fedora
    elif have zypper; then PM=zypper; PM_FAMILY=suse
    elif have apk; then PM=apk; PM_FAMILY=alpine
    elif have xbps-install; then PM=xbps-install; PM_FAMILY=void
    fi
fi

# the exact command that installs a package on this distribution
pm_hint() {
    case "$PM" in
        pacman) printf 'sudo pacman -S --needed %s' "$*" ;;
        apt-get) printf 'sudo apt-get install -y %s' "$*" ;;
        dnf) printf 'sudo dnf install -y %s' "$*" ;;
        yum) printf 'sudo yum install -y %s' "$*" ;;
        zypper) printf 'sudo zypper --non-interactive install %s' "$*" ;;
        apk) printf 'sudo apk add %s' "$*" ;;
        xbps-install) printf 'sudo xbps-install -y %s' "$*" ;;
        *) printf '# install: %s' "$*" ;;
    esac
}

pm_installed() {
    case "$PM" in
        pacman) pacman -Qq "$1" >/dev/null 2>&1 ;;
        apt-get) dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "ok installed" ;;
        dnf | yum | zypper) rpm -q "$1" >/dev/null 2>&1 ;;
        apk) apk info -e "$1" >/dev/null 2>&1 ;;
        xbps-install) xbps-query -Rs "^$1\$" >/dev/null 2>&1 ;;
        *) return 1 ;;
    esac
}

pm_install_one() {
    case "$PM" in
        pacman) sudo pacman -S --needed --noconfirm "$1" ;;
        apt-get) sudo apt-get install -y "$1" ;;
        dnf) sudo dnf install -y "$1" ;;
        yum) sudo yum install -y "$1" ;;
        zypper) sudo zypper --non-interactive install "$1" ;;
        apk) sudo apk add "$1" ;;
        xbps-install) sudo xbps-install -y "$1" ;;
        *) return 1 ;;
    esac
}

# role | how it is detected | package name per distribution
# (columns: arch, debian, fedora, suse, alpine, void)
# note: the pw-* tools live in their own package on most distributions
# (pipewire-audio, pipewire-bin, pipewire-utils, pipewire-tools)
REQUIREMENTS='audio daemon|pkg:pipewire|pipewire|pipewire|pipewire|pipewire|pipewire|pipewire
audio tools|pw-record|pipewire-audio|pipewire-bin|pipewire-utils|pipewire-tools|pipewire-tools|pipewire
audio session|wireplumber|wireplumber|wireplumber|wireplumber|wireplumber|wireplumber|wireplumber
python|python3|python|python3|python3|python3|python3|python3
pipx|pipx|python-pipx|pipx|pipx|pipx|pipx|pipx
wayland clipboard|wl-copy|wl-clipboard|wl-clipboard|wl-clipboard|wl-clipboard|wl-clipboard|wl-clipboard
wayland typing|wtype|wtype|wtype|wtype|wtype|wtype|wtype
wayland paste key|ydotool|ydotool|ydotool|ydotool|ydotool|ydotool|ydotool
x11 injection|xdotool|xdotool|xdotool|xdotool|xdotool|xdotool|xdotool
x11 clipboard|xclip|xclip|xclip|xclip|xclip|xclip|xclip
overlay (gtk4)|pkg:gtk4|gtk4|libgtk-4-1|gtk4|gtk4|gtk4|gtk4
overlay (layer-shell)|pkg:gtk4-layer-shell|gtk4-layer-shell|gir1.2-gtklayershell-0.0|gtk4-layer-shell|gtk4-layer-shell|gtk4-layer-shell|gtk4-layer-shell
python bindings (gi)|module:gi|python-gobject|python3-gi|python3-gobject|python3-gobject|py3-gobject|python3-gobject
desktop notifications|notify-send|libnotify|libnotify-bin|libnotify|libnotify|libnotify|libnotify'

pm_column() {
    case "$PM_FAMILY" in
        arch) printf '3' ;;
        debian) printf '4' ;;
        fedora) printf '5' ;;
        suse) printf '6' ;;
        alpine) printf '7' ;;
        void) printf '8' ;;
        *) printf '0' ;;
    esac
}

# the name of the pipx package on this distribution ("pipx", "python-pipx", ...)
pm_pipx_package() {
    local col
    col="$(pm_column)"
    if [ "$col" = "0" ]; then
        printf 'pipx'
        return 0
    fi
    printf '%s\n' "$REQUIREMENTS" | awk -F'|' -v c="$col" '$1=="pipx" {print $c; exit}'
}

# is the thing this role needs available?
tool_present() {
    case "$1" in
        module:*) "$PYTHON" -c "import ${1#module:}" >/dev/null 2>&1 ;;
        pkg:*) pm_installed "${1#pkg:}" ;;
        *) have "$1" ;;
    esac
}

# pipx is needed for the whole installation
if ! have pipx; then
    for candidate in "$HOME/.local/bin/pipx" /usr/local/bin/pipx /usr/bin/pipx; do
        if [ -x "$candidate" ]; then
            PATH="$(dirname "$candidate"):$PATH"
            export PATH
            break
        fi
    done
fi
if ! have pipx; then
    if [ -n "$PM" ]; then
        die "pipx is missing — install it with: $(pm_hint "$(pm_pipx_package)")
     (or run this script with --system-deps)"
    fi
    die "pipx is missing — install it with: pipx install pipx"
fi

# ------------------------------------------------------------------ state
if ! PIPX_JSON="$(pipx list --json 2>/dev/null)"; then
    warn "could not read the pipx state — treating this as a fresh installation"
    PIPX_JSON='{}'
fi
INSTALLED="$(pipx_field installed || true)"
PREV_SPEC="$(pipx_field spec || true)"

PIPX_BIN_DIR="$(pipx_env PIPX_BIN_DIR)"
[ -n "$PIPX_BIN_DIR" ] || PIPX_BIN_DIR="$HOME/.local/bin"
VENV_ROOT="$(pipx_env PIPX_LOCAL_VENVS)"
[ -n "$VENV_ROOT" ] || VENV_ROOT="${PIPX_HOME:-$HOME/.local/share/pipx}/venvs"
VENV_DIR="$VENV_ROOT/voicetype"

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/voicetype"
EXTRAS_STATE="$STATE_DIR/extras"

REQ_EXTRAS="$(split_extras "$EXTRAS")"
PREV_EXTRAS="$(split_extras "$(extras_from_spec "$PREV_SPEC")")"
# pipx's metadata is the primary source; our own state file is the fallback
# (a venv installed by hand, or by an older version, has no recorded extras)
if [ -z "$PREV_EXTRAS" ] && [ -f "$EXTRAS_STATE" ]; then
    PREV_EXTRAS="$(split_extras "$(cat "$EXTRAS_STATE")")"
fi

if [ "$RESET_EXTRAS" -eq 1 ]; then
    ALL_EXTRAS="$REQ_EXTRAS"
    if [ -z "$(printf '%s' "$ALL_EXTRAS" | tr -d '[:space:]')" ]; then
        warn "--reset-extras without a list drops every extra (only 'translate' is always installed)"
    fi
elif [ -n "$(printf '%s' "$REQ_EXTRAS" | tr -d '[:space:]')" ]; then
    ALL_EXTRAS="$(printf '%s\n%s\n' "$REQ_EXTRAS" "$PREV_EXTRAS")"
elif [ -n "$(printf '%s' "$PREV_EXTRAS" | tr -d '[:space:]')" ]; then
    ALL_EXTRAS="$PREV_EXTRAS"
else
    ALL_EXTRAS="socks"
fi

# googletrans is always installed: the daemon validates the config with it and
# 'voicetype config check' fails without it — no matter whether the translation
# is switched on in the config
ALL_EXTRAS="$(printf '%s\n%s\n' "$ALL_EXTRAS" "translate")"

FINAL_EXTRAS="$(printf '%s\n' "$ALL_EXTRAS" | tr ' ' '\n' | dedupe | tr '\n' ' ')"
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

# the venv exists even when pipx's metadata is stale (or missing)
VENV_EXISTS=0
[ -d "$VENV_DIR" ] && VENV_EXISTS=1

ACTION="installed"
CONFIG_STATUS="unknown"
CONFIG_FILE="${VOICETYPE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/voicetype/config.toml}"
# a leading ~ is not expanded by the shell when it comes from an environment
# variable — do it here, so "~/..." and "~user/..." work everywhere
case "$CONFIG_FILE" in
    "~") CONFIG_FILE="$HOME" ;;
    "~/"*) CONFIG_FILE="$HOME/${CONFIG_FILE:2}" ;;
    "~"*)
        # "~user/..." — the home directory of another user
        rest="${CONFIG_FILE:1}"
        user="${rest%%/*}"
        user_home="$(getent passwd "$user" 2>/dev/null | cut -d: -f6 || true)"
        if [ -n "$user_home" ]; then
            CONFIG_FILE="$user_home${rest#"$user"}"
        fi
        ;;
esac
SERVICE_STATE="not started (no systemd user session)"
HOTKEY_STATE="not registered"

# ============================================================ 1. environment
step "Environment"

info "  distribution : $OS_NAME"
info "  package mgr  : ${PM:-none detected}"
info "  python       : $("$PYTHON" -V 2>&1) ($PYTHON)"
info "  pipx         : $(command -v pipx)"

if ! printf '%s' "$PATH" | tr ':' '\n' | grep -qxF "$PIPX_BIN_DIR"; then
    # pipx's own remedy: add ~/.local/bin to the shell profile
    if pipx ensurepath >/dev/null 2>&1; then
        PATH_FIXED=1
        res_ok "$PIPX_BIN_DIR was added to your shell profile (open a new shell to use it)"
    else
        res_warn "$PIPX_BIN_DIR is not in your PATH — add it to ~/.bashrc (or ~/.zshrc)"
    fi
    PATH="$PIPX_BIN_DIR:$PATH"
    export PATH
fi
res_ok "PATH is ready"

SESSION_TYPE="${XDG_SESSION_TYPE:-unknown}"
if [ "$SESSION_TYPE" = "unknown" ]; then
    if [ -n "${WAYLAND_DISPLAY:-}" ]; then SESSION_TYPE="wayland"
    elif [ -n "${DISPLAY:-}" ]; then SESSION_TYPE="x11"
    fi
fi
res_ok "session type: $SESSION_TYPE"

# ============================================================ 2. system packages
step "System packages"

SYS_MISSING=""
SYS_CHECKED=0
if [ -z "$PM" ]; then
    res_warn "no supported package manager found — install the missing tools by hand"
else
    col="$(pm_column)"
    if [ "$col" = "0" ]; then
        res_warn "no package list for '$OS_ID' — install the missing tools by hand"
    else
        SYS_CHECKED=1
        while IFS='|' read -r role tool pkg; do
            [ -n "$role" ] || continue
            if tool_present "$tool"; then
                [ "$VERBOSE" -eq 1 ] && res_ok "$role"
                continue
            fi
            if pm_installed "$pkg"; then
                # the package is there, but the tool is not — a broken install
                res_warn "$role — the package '$pkg' is installed, but '$tool' is missing"
                continue
            fi
            SYS_MISSING="$SYS_MISSING $pkg"
            res_warn "$role — missing (package '$pkg')"
        done <<EOF
$(printf '%s\n' "$REQUIREMENTS" | awk -F'|' -v c="$col" '{print $1 "|" $2 "|" $c}')
EOF
    fi
fi

if [ -n "$SYS_MISSING" ]; then
    SYS_MISSING="${SYS_MISSING# }"
    SYS_INSTALLED=0
    if [ "$DO_SYSTEM_DEPS" -eq 1 ]; then
        if ! have sudo; then
            res_warn "sudo is not available — install them manually: $(pm_hint "$SYS_MISSING")"
        elif ! sudo -v; then
            res_warn "no sudo access — install them manually: $(pm_hint "$SYS_MISSING")"
        else
            SYS_INSTALLED=1
            for pkg in $SYS_MISSING; do
                if pm_install_one "$pkg"; then
                    res_ok "installed $pkg"
                else
                    res_warn "could not install '$pkg' — it may not exist for $OS_ID"
                    SYS_INSTALLED=0
                fi
            done
        fi
    else
        res_skip "not installed (the script stays root-free by default)"
        note "install them with: $(pm_hint "$SYS_MISSING")"
        note "or re-run with --system-deps"
    fi
    if [ "$SYS_INSTALLED" -eq 1 ]; then
        check_add "ok" "system packages" "installed"
    else
        check_add "warn" "system packages" "$(pm_hint "$SYS_MISSING")"
    fi
elif [ "$SYS_CHECKED" -eq 1 ]; then
    res_ok "everything the daemon needs is installed"
    check_add "ok" "system packages" ""
else
    check_add "warn" "system packages" "no package manager found"
fi

# ============================================================ 3. groups & audio
step "Groups and audio services"

GROUPS_MISSING=""
for group in input audio; do
    if id -nG 2>/dev/null | tr ' ' '\n' | grep -qx "$group"; then
        res_ok "group '$group'"
    else
        res_warn "not in group '$group'"
        GROUPS_MISSING="$GROUPS_MISSING $group"
    fi
done
GROUPS_MISSING="${GROUPS_MISSING# }"
# usermod expects a comma separated list (-G "input,audio")
GROUPS_CSV="$(printf '%s' "$GROUPS_MISSING" | tr ' ' ',')"
if [ -n "$GROUPS_MISSING" ]; then
    if [ "$DO_SYSTEM_DEPS" -eq 1 ] && have sudo && sudo -v 2>/dev/null; then
        if sudo usermod -aG "$GROUPS_CSV" "$(id -un)"; then
            res_ok "added to group(s) '$GROUPS_CSV' — log out and back in to apply"
        else
            res_warn "could not update the groups — run: sudo usermod -aG $GROUPS_CSV $(id -un)"
        fi
    else
        note "run: sudo usermod -aG $GROUPS_CSV $(id -un)   (then log out and back in)"
    fi
    check_add "warn" "user groups" "$GROUPS_CSV"
else
    check_add "ok" "user groups" "input, audio"
fi

if systemd_user_available; then
    if systemctl --user enable --now pipewire wireplumber >/dev/null 2>&1; then
        res_ok "pipewire + wireplumber enabled"
    else
        res_skip "pipewire/wireplumber could not be enabled (maybe another audio server is running)"
    fi
    if systemctl --user enable --now ydotool.service >/dev/null 2>&1; then
        res_ok "ydotool daemon enabled"
    else
        res_info "no ydotool user unit — start it by hand: sudo ydotoold &"
    fi
else
    res_skip "no systemd user session — the audio services were left alone"
fi

# ============================================================ 4. microphone
step "Microphone test"

if [ "$DO_MIC_CHECK" -eq 0 ]; then
    res_skip "skipped (--no-mic-check)"
elif ! have pw-record || ! have timeout; then
    res_skip "no pw-record (the PipeWire tools are not installed)"
else
    mic_wav="$WORK_DIR/mic-test.wav"
    mic_err="$(timeout --signal=INT 3 pw-record "$mic_wav" 2>&1 >/dev/null || true)"
    if [ ! -s "$mic_wav" ]; then
        res_warn "no audio was captured — check that the microphone is the default source"
    elif mic_out="$("$PYTHON" - "$mic_wav" "$mic_err" 2>&1 <<'PY'
import array
import math
import sys
import wave

path, pw_err = sys.argv[1], sys.argv[2]


def fail(msg: str) -> None:
    print(f"pw-record FAILED — {msg}")
    if pw_err.strip():
        print(f"  pw-record said: {pw_err.strip()}")
    sys.exit(1)


try:
    with wave.open(path) as w:
        rate = w.getframerate()
        frames = w.getnframes()
        raw = w.readframes(frames)
except Exception as exc:  # missing file, truncated header, ...
    fail(f"cannot read {path}: {exc}")

if frames == 0:
    fail("no audio frames captured")

samples = array.array("h")
samples.frombytes(raw[: len(raw) // 2 * 2])
if sys.byteorder == "big":
    samples.byteswap()
rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0
dbfs = 20 * math.log10(rms) if rms > 0 else -99.0
print(f"pw-record OK — {frames / rate:.1f} s captured, RMS {dbfs:.0f} dBFS")
if dbfs < -60:
    print("hint: level is very low — check the mic is unmuted and is the default source")
PY
    )"; then
        res_ok "the microphone works"
        printf '%s\n' "$mic_out" | sed 's/^/      /'
    else
        res_warn "the recording could not be validated — check the microphone"
        printf '%s\n' "$mic_out" | sed 's/^/      /'
    fi
fi

# ============================================================ 5. python env
step "Python environment"

# pipx's uv backend runs uv in verbose mode → filter its debug chatter away,
# but keep the "Installed ... / ~ voicetype==..." summary lines.
run_venv_pip() {
    local out rc=0
    out="$(pipx runpip voicetype install "$@" 2>&1)" || rc=$?
    if [ "$VERBOSE" -eq 1 ]; then
        printf '%s\n' "$out"
    else
        printf '%s\n' "$out" | grep -v '^DEBUG ' | grep -v '^[[:space:]]*$' || true
    fi
    return "$rc"
}

install_fresh() {
    ACTION="installed"
    info "  installing voicetype${EXTRAS_SUFFIX:+" ($FINAL_EXTRAS)"} into a new pipx venv"
    pipx install --system-site-packages ".${EXTRAS_SUFFIX}" && return 0
    warn "the install failed — retrying with an explicit Python"
    pipx install --system-site-packages --python "$PYTHON" ".${EXTRAS_SUFFIX}"
}

update_existing() {
    ACTION="updated"
    info "  updating the existing installation${EXTRAS_SUFFIX:+" ($FINAL_EXTRAS)"}"
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
    pipx install --system-site-packages ".${EXTRAS_SUFFIX}" || \
        pipx install --system-site-packages --python "$PYTHON" ".${EXTRAS_SUFFIX}"
}

verify_install() {
    [ -x "$PIPX_BIN_DIR/voicetype" ] || return 1
    "$PIPX_BIN_DIR/voicetype" --version >/dev/null 2>&1 || return 1
    return 0
}

if [ "$INSTALLED" = "1" ] && [ "$REINSTALL" -eq 1 ]; then
    warn "--reinstall: dropping the existing installation"
    clean_reinstall || die "the reinstall failed (see the errors above)"
elif [ "$INSTALLED" = "1" ] || [ "$VENV_EXISTS" -eq 1 ]; then
    if [ ! -d "$VENV_DIR" ]; then
        warn "the pipx venv is gone but pipx still lists voicetype — rebuilding it"
        clean_reinstall || die "the rebuild failed (see the errors above)"
    else
        update_existing || clean_reinstall || die "the update failed (see the errors above)"
    fi
else
    install_fresh || die "the installation failed (see the errors above)"
fi

if ! verify_install; then
    warn "the installation is not usable — rebuilding it"
    clean_reinstall || die "the rebuild failed (see the errors above)"
fi
verify_install || die "the installation is broken even after a clean reinstall (see the errors above)"

INSTALLED_VERSION="$("$PIPX_BIN_DIR/voicetype" --version 2>/dev/null | head -n1 || true)"
res_ok "voicetype is $ACTION: ${INSTALLED_VERSION:-unknown version} → $PIPX_BIN_DIR/voicetype"
check_add "ok" "python environment" "$PIPX_BIN_DIR/voicetype"

# remember the extras — pipx's metadata is not always available
# (the file is comma separated, exactly like the "extras" argument)
if ! mkdir -p "$STATE_DIR" 2>/dev/null ||
    ! printf '%s\n' "$(printf '%s' "$FINAL_EXTRAS" | tr ' ' ',')" 2>/dev/null > "$EXTRAS_STATE"; then
    res_warn "could not remember the extras in $EXTRAS_STATE"
fi

# ============================================================ 6. configuration
step "Configuration"

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
        PYTHONPATH="$ROOT" "$PYTHON" -c '
from voicetype.config import repair_config
r = repair_config()
print(f"{r.status}: {r.path}" + (f" (backup: {r.backup})" if r.backup else ""))
'
    fi
}

# a brand new config gets a short guide at the top — the sample is documented,
# but a first-time user should not have to guess what to do with it
write_config_guide() {
    local path="$1"
    local tmp="$path.tmp.$$"
    # a failing guide must never abort the installation
    if ! {
        cat <<EOF
# ─────────────────────────────────────────────────────────────────────────
#  VoiceType — quick guide
#
#  1. Press  $HOTKEY_COMBO  (or run 'voicetype toggle') and speak.
#  2. Press it again — or stay silent for 3 s — to stop. The text is pasted
#     into the window that has the focus.
#  3. 'voicetype doctor'  → checks the whole environment.
#     'voicetype status'  → is the daemon alive?
#
#  Every key below is optional: delete a line to fall back to its default.
#  Full reference: https://github.com/isalehgholami/voicetype#configuration
# ─────────────────────────────────────────────────────────────────────────
EOF
        cat "$path"
    } > "$tmp" 2>/dev/null; then
        rm -f "$tmp"
        return 1
    fi
    chmod 600 "$tmp" 2>/dev/null || true
    if ! mv -f "$tmp" "$path"; then
        rm -f "$tmp"
        return 1
    fi
    return 0
}

# the backup of this very run — the CLI reports it, a file name alone is not
# reliable (a backup keeps the timestamp of the file it was copied from)
reported_backup() {
    local line="$1" found=""
    found="$(printf '%s' "$line" | sed -n 's/.*backed up as \(.*\))/\1/p')"
    [ -n "$found" ] || found="$(printf '%s' "$line" | sed -n 's/.*backup: \(.*\))/\1/p')"
    if [ -n "$found" ] && [ -f "$found" ]; then
        printf '%s' "$found"
        return 0
    fi
    # no report — take the one with the newest name
    ls -1 "$CONFIG_FILE".bak-* 2>/dev/null | sort | tail -n1 || true
}

CONFIG_EXISTED=0
CONFIG_WAS_VALID=1
if [ -f "$CONFIG_FILE" ]; then
    CONFIG_EXISTED=1
    "$PIPX_BIN_DIR/voicetype" config check >/dev/null 2>&1 || CONFIG_WAS_VALID=0
fi

if ! CONFIG_LINE="$(ensure_config)"; then
    # a config that cannot be written must not stop the whole installation
    res_warn "could not create the configuration file — check the path: $CONFIG_FILE"
    CONFIG_LINE=""
fi
[ -n "$CONFIG_LINE" ] && info "  $CONFIG_LINE"

if [ "$CONFIG_EXISTED" -eq 0 ]; then
    if [ -f "$CONFIG_FILE" ]; then
        CONFIG_STATUS="created"
        if write_config_guide "$CONFIG_FILE"; then
            res_ok "created: $CONFIG_FILE (with a short guide)"
        else
            res_ok "created: $CONFIG_FILE"
            res_warn "could not add the guide to the config — check the permissions of $CONFIG_FILE"
        fi
    else
        CONFIG_STATUS="invalid"
        res_warn "there is no config file at $CONFIG_FILE — run: voicetype config init"
    fi
elif [ "$CONFIG_WAS_VALID" -eq 1 ]; then
    CONFIG_STATUS="ok"
    res_ok "kept your config: $CONFIG_FILE"
elif "$PIPX_BIN_DIR/voicetype" config check >/dev/null 2>&1; then
    CONFIG_STATUS="repaired"
    # the file was replaced by the sample → it gets the guide too
    write_config_guide "$CONFIG_FILE" ||
        res_warn "could not add the guide to the config — check the permissions of $CONFIG_FILE"
    backup="$(reported_backup "$CONFIG_LINE")"
    if [ -n "$backup" ]; then
        res_ok "repaired: $CONFIG_FILE (the broken file was backed up as $backup)"
    else
        res_ok "repaired: $CONFIG_FILE"
    fi
else
    # the file parses, but a value is not acceptable (e.g. a wrong translation
    # language) — 'config init --repair' does not touch those
    CONFIG_STATUS="invalid"
    res_warn "the config could not be repaired automatically — run: voicetype config check   (it shows the reason)"
    reason="$("$PIPX_BIN_DIR/voicetype" config check 2>&1 | head -n2 || true)"
    if printf '%s' "$reason" | grep -qi "googletrans"; then
        res_info "the reason: $reason"
        res_info "fix it with: pipx install 'voicetype[translate]'   (or: pip install googletrans)"
    fi
fi
case "$CONFIG_STATUS" in
    ok | created | repaired) check_add "ok" "configuration file" "$CONFIG_FILE" ;;
    *) check_add "warn" "configuration file" "$CONFIG_FILE" ;;
esac

# ============================================================ 7. service unit
step "Service unit"

LIB_DIR="$HOME/.local/lib/voicetype"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_WRITTEN=0
SERVICE_ENABLED=0

if [ "$DO_SERVICE" -eq 0 ]; then
    res_skip "skipped (--no-service)"
    SERVICE_STATE="skipped (--no-service)"
elif [ ! -f packaging/voicetype.service ] || [ ! -f packaging/wait-display ]; then
    res_warn "packaging/voicetype.service or packaging/wait-display is missing — no unit written"
    SERVICE_STATE="not installed (the packaging files are missing)"
else
    # writing the unit is just a file — it does not need a systemd session
    if ! mkdir -p "$LIB_DIR" "$UNIT_DIR"; then
        res_warn "could not create $LIB_DIR or $UNIT_DIR — no unit written"
        SERVICE_STATE="not installed (the directories could not be created)"
    else
        # cp + chmod (busybox has no "install")
        if cp packaging/wait-display "$LIB_DIR/wait-display" 2>/dev/null; then
            chmod 755 "$LIB_DIR/wait-display" 2>/dev/null || true
        else
            res_warn "could not copy packaging/wait-display to $LIB_DIR"
        fi

        # write the unit atomically: a half-written file would break the daemon
        unit_tmp="$UNIT_DIR/.voicetype.service.$$"
        UNIT_TMP="$unit_tmp"
        if ! sed -e "s#/usr/lib/voicetype/wait-display#$(sed_escape "$LIB_DIR/wait-display")#" \
            -e "s#/usr/bin/voicetype#$(sed_escape "$PIPX_BIN_DIR/voicetype")#g" \
            packaging/voicetype.service > "$unit_tmp" 2>/dev/null; then
            res_warn "could not write the unit — see: $UNIT_DIR/voicetype.service"
            rm -f "$unit_tmp"
            UNIT_TMP=""
        else
            # the daemon has to read the same config file the installer writes
            # (systemd splits Environment= on whitespace → the value must be quoted)
            if [ -n "${VOICETYPE_CONFIG:-}" ]; then
                # the value goes into the file as it is — only the systemd
                # quoting has to be escaped (a backslash and a quote)
                unit_env="Environment=\"VOICETYPE_CONFIG=$(systemd_escape "$CONFIG_FILE")\""
                unit_new="$unit_tmp.new"
                UNIT_TMP="$unit_new"
                inserted=0
                : > "$unit_new" 2>/dev/null || true
                while IFS= read -r line || [ -n "$line" ]; do
                    printf '%s\n' "$line" >> "$unit_new" 2>/dev/null || true
                    case "$line" in
                        Environment=* | ExecStart=*)
                            if [ "$inserted" -eq 0 ]; then
                                printf '%s\n' "$unit_env" >> "$unit_new" 2>/dev/null || true
                                inserted=1
                            fi
                            ;;
                    esac
                done < "$unit_tmp"
                if [ "$inserted" -eq 0 ]; then
                    printf '%s\n' "$unit_env" >> "$unit_new" 2>/dev/null || true
                fi
                if ! mv -f "$unit_new" "$unit_tmp" 2>/dev/null; then
                    res_warn "could not add the config path to the unit"
                    rm -f "$unit_new"
                    UNIT_TMP="$unit_tmp"
                fi
            fi
            chmod 644 "$unit_tmp" 2>/dev/null || true
            if mv -f "$unit_tmp" "$UNIT_DIR/voicetype.service"; then
                UNIT_TMP=""
                UNIT_WRITTEN=1
                res_ok "unit written: $UNIT_DIR/voicetype.service"
            else
                res_warn "could not write $UNIT_DIR/voicetype.service"
            fi
        fi
    fi

    if [ "$UNIT_WRITTEN" -eq 1 ]; then
        if systemd_user_available; then
            systemctl --user daemon-reload || warn "systemd could not reload the units — try: systemctl --user daemon-reload"
            if systemctl --user enable voicetype; then
                SERVICE_ENABLED=1
                res_ok "the daemon starts automatically at boot"
            else
                res_warn "the unit could not be enabled — run: systemctl --user enable voicetype"
            fi
        else
            # the unit is ready, but there is no session to enable it in
            res_skip "no systemd user session — enable it later with: systemctl --user enable --now voicetype"
            warn "no systemd user session here — the unit is ready, enable it later with: systemctl --user enable --now voicetype"
            SERVICE_STATE="not started (no systemd user session)"
        fi
        if [ "$SERVICE_ENABLED" -eq 1 ]; then
            check_add "ok" "systemd user unit" "$UNIT_DIR/voicetype.service"
        else
            check_add "warn" "systemd user unit" "$UNIT_DIR/voicetype.service"
        fi
    fi
fi

# ============================================================ 8. daemon
step "Daemon"

# the daemon needs a moment to start and to create its socket — wait for both,
# so that 'voicetype reload' works right after the installation
# (the unit waits up to 15 s for a display before it starts)
DAEMON_WAIT_TRIES="${VOICETYPE_DAEMON_WAIT_TRIES:-80}"
case "$DAEMON_WAIT_TRIES" in
    '' | *[!0-9]*) DAEMON_WAIT_TRIES=80 ;;
esac
wait_for_daemon() {
    local i=0 pid="" sock=""
    SOCKET_MISSING=0
    while [ "$i" -lt "$DAEMON_WAIT_TRIES" ]; do
        if systemctl --user is-active --quiet voicetype; then
            break
        fi
        i=$((i + 1))
        sleep 0.25
    done
    [ "$i" -lt "$DAEMON_WAIT_TRIES" ] || return 1

    # the socket is what the CLI talks to — wait for it, but only when the
    # daemon really runs (a container or a test has no pid to look at)
    pid="$(systemctl --user show -p MainPID --value voicetype 2>/dev/null || true)"
    case "$pid" in
        "" | 0) return 0 ;;
    esac
    [ -n "${XDG_RUNTIME_DIR:-}" ] || return 0
    sock="$XDG_RUNTIME_DIR/voicetype.sock"
    i=0
    while [ "$i" -lt 20 ]; do
        if [ -S "$sock" ]; then
            return 0
        fi
        i=$((i + 1))
        sleep 0.25
    done
    # the daemon runs, but the socket is somewhere else — the CLI may not
    # reach it (this is only a hint, the daemon itself is fine)
    SOCKET_MISSING=1
    return 0
}

# does the user manager know a display? (the daemon needs one for the overlay)
user_manager_has_display() {
    local env_dump
    env_dump="$(systemctl --user show-environment 2>/dev/null || true)"
    printf '%s' "$env_dump" | grep -qE '^(WAYLAND_DISPLAY|DISPLAY)=.+'
}

if [ "$DO_SERVICE" -eq 0 ]; then
    res_skip "skipped (--no-service)"
elif ! systemd_user_available; then
    res_skip "no systemd user session — start the daemon later with: systemctl --user enable --now voicetype"
elif [ "$UNIT_WRITTEN" -eq 0 ]; then
    res_skip "no unit was written — nothing to start"
else
    DAEMON_ACTION="started"
    if systemctl --user is-active --quiet voicetype; then
        # the daemon is already running — restart it, so the new code is used
        if systemctl --user restart voicetype; then
            DAEMON_ACTION="restarted"
            res_ok "the daemon was restarted — the new code is running"
        else
            DAEMON_ACTION="restart-failed"
            res_warn "the daemon could not be restarted — see: journalctl --user -u voicetype"
        fi
    elif systemctl --user enable --now voicetype; then
        res_ok "the daemon was started"
    else
        DAEMON_ACTION="start-failed"
        res_warn "the daemon could not be started — see: journalctl --user -u voicetype"
    fi

    if wait_for_daemon; then
        case "$DAEMON_ACTION" in
            restarted)
                res_ok "the daemon is running (with the new code)"
                SERVICE_STATE="enabled and restarted (the new code is running)"
                check_add "ok" "the daemon" "running" ;;
            restart-failed)
                res_warn "the daemon still runs, but the restart failed — the old code is still active"
                SERVICE_STATE="running, but the restart failed — see: journalctl --user -u voicetype"
                check_add "warn" "the daemon" "restart failed" ;;
            start-failed)
                res_warn "the daemon is running, but the start failed"
                SERVICE_STATE="enabled, but the start failed — see: journalctl --user -u voicetype"
                check_add "warn" "the daemon" "start failed" ;;
            *)
                res_ok "the daemon is running"
                SERVICE_STATE="enabled and started"
                check_add "ok" "the daemon" "running" ;;
        esac
        if [ "$SOCKET_MISSING" -eq 1 ]; then
            res_info "the socket is not in $XDG_RUNTIME_DIR — 'voicetype reload' may not reach the daemon"
        fi
    else
        res_warn "the daemon does not answer — see: journalctl --user -u voicetype -n 20"
        if ! user_manager_has_display; then
            res_info "the user session has no display — the daemon needs one (it starts at login)"
        fi
        journalctl --user -u voicetype -n 20 --no-pager 2>/dev/null | sed 's/^/      /' || true
        SERVICE_STATE="not running — see: journalctl --user -u voicetype"
        check_add "warn" "the daemon" "not running"
    fi
fi

# ============================================================ 9. shortcut
step "Shortcut"

if [ "$DO_HOTKEY" -eq 0 ]; then
    res_skip "skipped (--no-hotkey)"
    HOTKEY_STATE="not registered (--no-hotkey)"
elif [ ! -f "$ROOT/scripts/install-hotkey.sh" ]; then
    res_skip "scripts/install-hotkey.sh is missing"
    HOTKEY_STATE="not registered (install-hotkey.sh missing)"
else
    hotkey_log="$WORK_DIR/hotkey.log"
    if PIPX_BIN_DIR="$PIPX_BIN_DIR" bash "$ROOT/scripts/install-hotkey.sh" "$HOTKEY_COMBO" > "$hotkey_log" 2>&1; then
        res_ok "$HOTKEY_COMBO registered"
        HOTKEY_STATE="$HOTKEY_COMBO (KDE Plasma)"
        [ "$VERBOSE" -eq 1 ] && cat "$hotkey_log"
    else
        res_warn "the shortcut was not registered automatically"
        res_info "KDE Plasma : re-run scripts/install-hotkey.sh after installing kwriteconfig6"
        res_info "Hyprland   : bind = SUPER ALT, V, exec, voicetype-toggle (hyprland.conf)"
        res_info "Sway / i3  : bindsym \$mod+alt+v, exec, voicetype-toggle   (config)"
        res_info "GNOME      : Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts"
        HOTKEY_STATE="$HOTKEY_COMBO (not registered — see the instructions above)"
    fi
fi

# ============================================================ 10. verification
step "Verification"

verify_failures=0
if [ -x "$PIPX_BIN_DIR/voicetype" ] && "$PIPX_BIN_DIR/voicetype" --version >/dev/null 2>&1; then
    res_ok "the console script runs"
else
    res_warn "the console script does not run"
    verify_failures=$((verify_failures + 1))
fi

if [ "$DO_SERVICE" -eq 1 ] && [ -f "$UNIT_DIR/voicetype.service" ]; then
    unit_exec="$(sed -n 's#^ExecStart=##p' "$UNIT_DIR/voicetype.service" 2>/dev/null | head -n1 || true)"
    unit_bin="${unit_exec%% *}"
    if [ -x "$unit_bin" ]; then
        res_ok "the unit points at a working binary"
    else
        res_warn "the unit points at '$unit_bin' — which does not exist"
        verify_failures=$((verify_failures + 1))
    fi
fi

if [ -x "$PIPX_BIN_DIR/voicetype" ] && "$PIPX_BIN_DIR/voicetype" config check >/dev/null 2>&1; then
    res_ok "the configuration is valid"
else
    res_warn "the configuration is not valid — run: voicetype config check"
    verify_failures=$((verify_failures + 1))
fi

if [ "$DO_SERVICE" -eq 0 ]; then
    res_skip "skipped (--no-service)"
elif ! systemd_user_available; then
    res_info "no systemd user session — the daemon was not started"
elif systemctl --user is-active --quiet voicetype; then
    res_ok "the daemon is running"
else
    res_warn "the daemon is not running — start it with: systemctl --user start voicetype"
    verify_failures=$((verify_failures + 1))
fi

if [ "$VERBOSE" -eq 1 ] && [ -x "$PIPX_BIN_DIR/voicetype" ]; then
    printf '\n'
    "$PIPX_BIN_DIR/voicetype" doctor 2>&1 | sed 's/^/    /' || true
fi

# ============================================================ summary
if [ "$QUIET" -eq 1 ]; then
    [ "$verify_failures" -eq 0 ] || warn "$verify_failures check(s) need your attention"
    exit 0
fi

printf '\n'
printf '%s%s%s\n' "$C_B" "────────────────────────────────────────────────────────────────" "$C_0"
printf ' %sVoiceType is %s.%s\n' "$C_B" "$ACTION" "$C_0"
printf '%s%s%s\n' "$C_B" "────────────────────────────────────────────────────────────────" "$C_0"
printf '  extras   : %s\n' "${FINAL_EXTRAS:-none}"
printf '  config   : %s (%s)\n' "$CONFIG_FILE" "$CONFIG_STATUS"
printf '  service  : %s\n' "$SERVICE_STATE"
printf '  shortcut : %s\n' "$HOTKEY_STATE"

if [ "${#CHECKS[@]}" -gt 0 ]; then
    printf '\n  Checks\n'
    for entry in "${CHECKS[@]}"; do
        status="${entry%%|*}"
        rest="${entry#*|}"
        what="${rest%%|*}"
        detail="${rest#*|}"
        # pad the label only when there is a detail to align it with
        label="$what"
        [ -n "$detail" ] && label="$(printf '%-22s' "$what")"
        case "$status" in
            ok) printf '    %s[✓]%s %s' "$C_G" "$C_0" "$label" ;;
            *) printf '    %s[!]%s %s' "$C_Y" "$C_0" "$label" ;;
        esac
        if [ -n "$detail" ]; then
            printf ' %s→ %s%s' "$C_D" "$detail" "$C_0"
        fi
        printf '\n'
    done
fi

cat <<EOF

  Next
    1. Log out and back in if you just joined the 'input'/'audio' groups.
    2. Press $HOTKEY_COMBO, speak, press it again — the text lands in the focused app.
    3. 'voicetype doctor' shows what is still missing.
EOF

if ! printf '%s' "$ORIGINAL_PATH" | tr ':' '\n' | grep -qxF "$PIPX_BIN_DIR"; then
    if [ "$PATH_FIXED" -eq 1 ]; then
        warn "$PIPX_BIN_DIR was added to your shell profile — open a new shell to use it"
    else
        warn "$PIPX_BIN_DIR is not in your PATH — run 'pipx ensurepath' once"
    fi
fi

[ "$verify_failures" -eq 0 ] || warn "$verify_failures check(s) need your attention — see the report above"
exit 0
