#!/usr/bin/env bash
# T-0.1 — prepare the target machine (Arch + Plasma 6 Wayland)
set -euo pipefail

sudo pacman -S --needed pipewire pipewire-pulse wireplumber python python-pip python-pipx python-gobject python-cairo \
    gtk4 gtk4-layer-shell wtype ydotool wl-clipboard xdotool xclip portaudio libnotify

sudo usermod -aG input,audio "$USER"
echo "Groups updated; log out and back in so they take effect."

systemctl --user enable --now pipewire wireplumber || true

# ydotool daemon (needed to send the Ctrl+V paste key)
systemctl --user enable --now ydotool.service 2>/dev/null || \
    echo "No user ydotool.service found; start the daemon manually (sudo ydotoold &)."

echo "--- checks ---"
echo "groups: $(groups)"
echo "recording 3 seconds..."
timeout 3 pw-record /tmp/voicetype-test.wav && echo "pw-record OK" || echo "pw-record FAILED"
