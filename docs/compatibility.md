# Compatibility

## Distributions

`scripts/install-user.sh` detects the distribution and installs the right packages:

| Distribution | Package manager | Status |
|---|---|---|
| Arch Linux | `pacman` | primary target (needs verification on real hardware) |
| Debian / Ubuntu | `apt-get` | supported — the package names are mapped automatically |
| Fedora / RHEL | `dnf` | supported |
| openSUSE | `zypper` | supported |
| Alpine | `apk` | supported |
| Void | `xbps-install` | supported |

Without `--system-deps` the installer stays root-free and only reports what is missing
(plus the exact command for your package manager).

## Desktops

| Environment | Status | How |
|---|---|---|
| Arch + KDE Plasma 6, Wayland | primary target (needs verification on real hardware) | clipboard paste via `wl-copy` + `ydotool` |
| X11 (any WM) | supported by design | `xdotool type` or `xclip` + `xdotool key` |
| Hyprland / Sway / niri | expected to work | layer-shell overlay; bind `voicetype-toggle` in the compositor config |
| GNOME Wayland | untested; overlay needs layer-shell (Mutter lacks it → runs without overlay), `ydotool` paste works | |

The shortcut is registered automatically on KDE Plasma; the installer prints the exact binding
for the other desktops when it cannot do it itself.

Per-application results: [inject-matrix.md](inject-matrix.md). Compatibility table for other desktops is
tracked in T-4.5 and must be filled in by testing on those sessions.

Layout note: paste uses the physical `V` key (evdev 47). On non-QWERTY layouts where that key isn't `v`,
set `[injector] backend = "wtype"` or use a layout that keeps `Ctrl+V` on the QWERTY position.
