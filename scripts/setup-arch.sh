#!/usr/bin/env bash
# T-0.1 — آماده‌سازی ماشین هدف (Arch + Plasma 6 Wayland)
set -euo pipefail

sudo pacman -S --needed pipewire pipewire-pulse wireplumber python python-pip python-gobject python-cairo \
    gtk4 gtk4-layer-shell wtype ydotool wl-clipboard xdotool xclip portaudio libnotify

sudo usermod -aG input,audio "$USER"
echo "گروه‌ها اضافه شد؛ یک بار logout/login کنید."

systemctl --user enable --now pipewire wireplumber || true

# ydotool daemon (برای paste با Ctrl+V)
systemctl --user enable --now ydotool.service 2>/dev/null || \
    echo "ydotool.service کاربری نیست؛ ydotoold را دستی اجرا کنید (sudo ydotoold &)."

echo "--- بررسی ---"
echo "groups: $(groups)"
echo "ضبط ۳ ثانیه‌ای..."
timeout 3 pw-record /tmp/voicetype-test.wav && echo "pw-record OK" || echo "pw-record خطا"
