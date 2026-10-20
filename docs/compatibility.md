# Compatibility

| Environment | Status | How |
|---|---|---|
| Arch + KDE Plasma 6, Wayland | primary target (needs verification on real hardware) | clipboard paste via `wl-copy` + `ydotool` |
| X11 (any WM) | supported by design | `xdotool type` or `xclip` + `xdotool key` |
| Hyprland / Sway / niri | expected to work | layer-shell overlay; bind `voicetype-toggle` in the compositor config |
| GNOME Wayland | untested; overlay needs layer-shell (Mutter lacks it → runs without overlay), `ydotool` paste works | |

Per-application results: [inject-matrix.md](inject-matrix.md). Compatibility table for other desktops is
tracked in T-4.5 and must be filled in by testing on those sessions.

Layout note: paste uses the physical `V` key (evdev 47). On non-QWERTY layouts where that key isn't `v`,
set `[injector] backend = "wtype"` or use a layout that keeps `Ctrl+V` on the QWERTY position.
