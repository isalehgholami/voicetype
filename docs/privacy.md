# Privacy

- **`google_free` (default) uploads your recorded audio to Google** (the same undocumented endpoint Chrome uses).
  Don't dictate secrets with it. The daemon logs a warning at startup when this provider is active.
- **Offline providers** (`vosk`, `whisper`) make no network requests during recognition.
- **No audio is stored by default.** Audio lives in memory for one session. `[audio] debug_save_audio = true`
  writes only the *last* recording to `~/.local/state/voicetype/last.wav` for debugging — keep it off.
- **Transcripts are never logged** (only state transitions, errors, and sizes).
- **Translation is off by default.** When `[translate] enabled = true` the text of every
  transcript is sent to Google (py-googletrans) — the same company that already receives
  your audio with the `google_free` provider. With an offline provider the transcript stays
  on your machine until you switch translation on.
- The control socket is `$XDG_RUNTIME_DIR/voicetype.sock` with mode 0600; transcribed text is never executed
  as a command and control characters are stripped before injection.
- Clipboard injection temporarily replaces the clipboard and restores it afterwards (`restore_clipboard = true`).
  Clipboard managers (Klipper) may record the dictated text in their history.
