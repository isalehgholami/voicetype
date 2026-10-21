#!/usr/bin/env bash
# Registers a KDE Plasma global shortcut that runs `voicetype-toggle` (docs/adr/0004).
# Idempotent. Usage: scripts/install-hotkey.sh [Meta+Alt+V]
#
# Plasma 6 notes (verified on Plasma 6.7 / Wayland):
#   * The shortcut registry lives *inside* kwin_wayland (it owns org.kde.kglobalaccel),
#     not in the kglobalacceld daemon — so the registry is re-read at session start.
#     Restarting kglobalacceld does nothing; the user has to log out/in once.
#   * The command-shortcut desktop file must be in ~/.local/share/applications/ so that
#     KService::serviceByStorageId() resolves it (System Settings → Shortcuts reads it
#     from there too). A copy in ~/.local/share/kglobalaccel/ is used as a fallback;
#     that copy must be executable, otherwise KIO refuses to launch it with
#     "You are not authorized to execute this file".
#   * A key can only be owned by one action. Alt+Space is KRunner's default shortcut,
#     so it has to be freed there first or the older action always wins.
set -euo pipefail

COMBO="${1:-Meta+Alt+V}"
TOGGLE="$(command -v voicetype-toggle || true)"
if [ -z "$TOGGLE" ]; then
    # ~/.local/bin is often not in the PATH of a desktop session — look it up directly
    for dir in "${PIPX_BIN_DIR:-}" "$HOME/.local/bin"; do
        if [ -n "$dir" ] && [ -x "$dir/voicetype-toggle" ]; then
            TOGGLE="$dir/voicetype-toggle"
            break
        fi
    done
fi
if [ -z "$TOGGLE" ]; then
    TOGGLE="voicetype-toggle"
fi

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
KGA="$DATA/kglobalaccel"
DESKTOP_NAME="voicetype-toggle.desktop"
KWRITE="$(command -v kwriteconfig6 || command -v kwriteconfig5 || true)"

if [ -z "$KWRITE" ]; then
    echo "kwriteconfig6/5 not found — not running KDE? Bind '$TOGGLE' in your compositor config instead." >&2
    exit 1
fi

mkdir -p "$APPS" "$KGA"
# write it atomically: a half-written desktop file would not be registered
tmp="$APPS/.$DESKTOP_NAME.$$"
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<EOF
[Desktop Entry]
Type=Application
Name=VoiceType Toggle
Comment=Start/stop voice dictation
Exec="$TOGGLE"
Icon=audio-input-microphone
Terminal=false
Categories=Utility;
NoDisplay=false
StartupNotify=false
X-KDE-GlobalAccel-CommandShortcut=true
EOF
chmod 644 "$tmp"
mv -f "$tmp" "$APPS/$DESKTOP_NAME"
trap - EXIT
# Fallback copy for the registry; KIO only launches it when the file is executable.
cp "$APPS/$DESKTOP_NAME" "$KGA/$DESKTOP_NAME"
chmod 755 "$KGA/$DESKTOP_NAME"

"$KWRITE" --file kglobalshortcutsrc --group services --group "$DESKTOP_NAME" --key _launch "$COMBO"

if [ ! -x "$TOGGLE" ] && [ "$TOGGLE" != "voicetype-toggle" ]; then
    echo "warning: '$TOGGLE' does not exist — install VoiceType before using the shortcut" >&2
fi

kbuildsycoca6 >/dev/null 2>&1 || true

cat <<EOF

Registered '$COMBO' → $TOGGLE
  desktop entry : $APPS/$DESKTOP_NAME
  config        : ~/.config/kglobalshortcutsrc  [$DESKTOP_NAME] _launch=$COMBO

Next:
  1. Log out and back in once — Plasma 6 loads the shortcut registry at session start.
  2. If the shortcut does not fire, check for a conflict: the same key may still be
     owned by another action (Alt+Space is KRunner's default). Free it in
     System Settings → Keyboard → Shortcuts → Applications → KRunner.
  3. Verify with: voicetype status   (press the combo, it must switch to "listening")
EOF
