# Known issues

1. **`wtype` on KDE Plasma** — KWin does not implement `zwp_virtual_keyboard_v1`; use the default paste backend.
2. **`google_free` is an undocumented endpoint** — may rate-limit or change; fallback to Vosk happens after 3 consecutive failures.
3. **In-flight Google requests can't be aborted** — cancelling during "Processing…" returns immediately, the network call finishes in the background and its result is discarded.
4. **Hold-to-talk needs group `input`** and `pip install evdev`; the default press-to-toggle does not.
5. **Overlay/hotkey settings need a daemon restart**; STT/injector/post-processing settings apply on `voicetype reload`.
6. **Persian ZWNJ rules are heuristic** (`می `/`نمی ` prefixes, `ها`/`تر` suffixes). Add exceptions via `[postprocess.custom_replacements]`.
7. **Vosk Persian accuracy is lower than Google/Whisper**, especially for embedded English terms (the term dictionary fixes ~40 common ones).
8. **Clipboard managers** (Klipper) may keep dictated text in their history.
9. **KDE shortcut registration** (`install-hotkey.sh`) writes `kglobalshortcutsrc` directly; the format can vary between Plasma releases — fall back to System Settings → Shortcuts → Add Command.
