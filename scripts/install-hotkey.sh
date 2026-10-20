#!/usr/bin/env bash
# Registers a KDE Plasma global shortcut that runs `voicetype-toggle` (docs/adr/0004).
# Idempotent. Usage: scripts/install-hotkey.sh [Meta+Alt+V]
#
# NOTE: the kglobalshortcutsrc layout for "command" shortcuts differs slightly between Plasma
# releases. If the shortcut doesn't appear after this script, add it by hand:
#   System Settings → Keyboard → Shortcuts → Add New → Command → `voicetype-toggle`
set -euo pipefail

COMBO="${1:-Meta+Alt+V}"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP="$APPS/voicetype-toggle.desktop"
KWRITE="$(command -v kwriteconfig6 || command -v kwriteconfig5 || true)"

if [ -z "$KWRITE" ]; then
    echo "kwriteconfig6/5 not found — not running KDE? Bind '$(command -v voicetype-toggle || echo voicetype-toggle)' in your compositor config instead." >&2
    exit 1
fi

mkdir -p "$APPS"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=VoiceType Toggle
Exec=voicetype-toggle
Icon=audio-input-microphone
Terminal=false
Categories=Utility;
StartupNotify=false
X-KDE-GlobalAccel-CommandShortcut=true
EOF

"$KWRITE" --file kglobalshortcutsrc --group services --group voicetype-toggle.desktop --key _launch "$COMBO"

# Make kglobalacceld re-read the file.
systemctl --user restart plasma-kglobalaccel.service 2>/dev/null \
    || qdbus6 org.kde.kglobalaccel /kglobalaccel org.kde.KGlobalAccel.reconfigure 2>/dev/null \
    || true

echo "Registered '$COMBO' → voicetype-toggle. If it does not work, log out/in once or add it in System Settings."
