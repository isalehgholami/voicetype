#!/usr/bin/env bash
# Per-user install from this checkout — no root, and no "externally-managed-environment" (PEP 668)
# error on Arch. Uses pipx with system site-packages so the distro's python-gobject (GTK overlay) works.
#
#   scripts/install-user.sh [extras]        extras: comma list of socks,vosk,whisper,hotkey (default: socks)
#   scripts/install-user.sh socks,vosk
set -euo pipefail
cd "$(dirname "$0")/.."

EXTRAS="${1:-socks}"
if ! command -v pipx >/dev/null; then
    echo "pipx is missing:  sudo pacman -S python-pipx" >&2
    exit 1
fi

pipx install --force --system-site-packages ".[${EXTRAS}]"

# systemd user unit that points at the pipx binaries (%h = $HOME)
LIB="$HOME/.local/lib/voicetype"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$LIB" "$UNIT_DIR"
install -m755 packaging/wait-display "$LIB/wait-display"
sed -e 's#/usr/lib/voicetype/wait-display#%h/.local/lib/voicetype/wait-display#' \
    -e 's#/usr/bin/voicetype#%h/.local/bin/voicetype#g' \
    packaging/voicetype.service > "$UNIT_DIR/voicetype.service"
systemctl --user daemon-reload

"$HOME/.local/bin/voicetype" config init
./scripts/install-hotkey.sh "${HOTKEY:-Meta+Alt+V}" || echo "(shortcut not registered — see README)"

systemctl --user enable --now voicetype

cat <<'EOF'

Installed. Next:
  1. sudo pacman -S ydotool && systemctl --user enable --now ydotool   (needed for paste on Wayland)
  2. voicetype doctor          every required line should be [✓]
  3. Behind a firewall?  set  [network] proxy = "socks5h://127.0.0.1:1080"  in ~/.config/voicetype/config.toml
                         then  voicetype reload && voicetype net-test
(Make sure ~/.local/bin is in your PATH; run `pipx ensurepath` once if it isn't.)
EOF
