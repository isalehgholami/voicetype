# Known issues

1. **`wtype` on KDE Plasma** — KWin does not implement `zwp_virtual_keyboard_v1`; use the default paste backend.
2. **`google_free` is an undocumented endpoint** — may rate-limit or change; fallback to Vosk happens after 3 consecutive failures.
3. **In-flight Google requests can't be aborted** — cancelling during "processing" returns immediately, the network call finishes in the background and its result is discarded.
4. **Hold-to-talk needs group `input`** and `pip install evdev`; the default press-to-toggle does not.
5. **Persian ZWNJ rules are heuristic** (`می `/`نمی ` prefixes, `ها`/`تر` suffixes). Add exceptions via `[postprocess.custom_replacements]`.
6. **Vosk Persian accuracy is lower than Google/Whisper**, especially for embedded English terms (the term dictionary fixes ~40 common ones).
7. **Clipboard managers** (Klipper) may keep dictated text in their history.
8. **KDE shortcut registration** (the shortcut step of `scripts/install-user.sh`) writes `kglobalshortcutsrc` directly; the format can vary between Plasma releases — fall back to System Settings → Shortcuts → Add Command.
9. **Segmented streaming sends more requests to Google** (one per sentence + a refresh every `stream_interim_seconds`). If the free endpoint rate-limits you, set `stream_interim_seconds = 0` or `streaming = false`.
10. **Streaming for Google is simulated** (cut at pauses), not a true duplex stream — Google offers no official streaming for the free endpoint. Words can differ slightly between the live preview and the final text.
11. **Translation uses unofficial endpoints** (the free web APIs of Google, Bing, Yandex and Alibaba): they can be rate-limited (HTTP 429) or blocked (HTTP 403), and the wording is not always idiomatic. The services are tried in turn, so one blocked service is not fatal; a failed translation never eats the transcript — the original text is injected and the overlay warns.
