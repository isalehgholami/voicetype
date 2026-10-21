# Troubleshooting

Start with `voicetype doctor` — every `✗` has an install hint.

| Symptom | Cause / fix |
|---|---|
| `voicetype: daemon is not running` | `systemctl --user start voicetype` · logs: `journalctl --user -u voicetype -f` |
| Shortcut does nothing | `voicetype toggle` in a terminal works? Then re-run `scripts/install-user.sh --hotkey-only` or add the command in System Settings |
| Overlay doesn't appear | `doctor` → gtk4-layer-shell / python-gobject; GNOME has no layer-shell |
| Text isn't typed | `doctor` → "text injection backend", "ydotoold running"; `systemctl --user enable --now ydotool` |
| Text typed in terminals as `v` or nothing | install `kdotool` or set `[injector] paste_shortcut = "ctrl+shift+v"` |
| "Network error" every time | check connectivity, or `provider = "vosk"` |
| Stops recording too early in a noisy room | raise `[stt] silence_threshold_seconds`; threshold auto-calibrates from the first 0.6 s |
| `cannot open microphone` | `pw-record test.wav`; `[audio] device = "<name substring>"` |
| Wrong Persian spacing | `[postprocess] normalize_zwnj = false` or add custom replacements |
| The text is not translated | `[translate] enabled = true` + `voicetype reload`; `doctor` → "googletrans installed"; try `voicetype translate "سلام"` |
| `config: 'translate.language' is not a known language` | use a code (`"en"`) or a name (`"English"`) — see [translation.md](translation.md) |

Debug a recording: `[audio] debug_save_audio = true` → `~/.local/state/voicetype/last.wav`; log level: `log_level = "DEBUG"`.
