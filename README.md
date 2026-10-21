<div align="center">

# 🎙️ VoiceType

**Voice typing for Linux — speak Persian (and English terms), get text at your cursor.**

Press a shortcut, talk, and the transcript is typed into whatever app you're using:
browser, Telegram, terminal, IDE, LibreOffice.

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Arch%20Linux%20%C2%B7%20KDE%20Plasma%206-1793D1?logo=archlinux&logoColor=white)
![Tests](https://img.shields.io/badge/tests-689_passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)

[Features](#-features) · [How it works](#-how-it-works) · [Requirements](#-requirements) · [Quick start](#-quick-start) · [Usage](#-usage) · [Configuration](#%EF%B8%8F-configuration) · [Providers](#%EF%B8%8F-speech-providers) · [Development](#%EF%B8%8F-development) · [Troubleshooting](#-troubleshooting) · [Docs](#%EF%B8%8F-documentation) · [فارسی](README.fa.md)

</div>

---

## ✨ Features

- **Persian-first dictation** with correct half-spaces (ZWNJ), Persian punctuation and digits.
- **Mixed Persian + English** — technical terms are normalised: «پایتون» → `Python`, «گیت‌هاب» → `GitHub` (40+ terms, extensible).
- **Optional translation** — every transcript can be translated into English (or any other language) before it is typed. Off by default.
- **Works in any app** — text is injected at the cursor via clipboard paste (Wayland: `wl-copy` + `ydotool`; X11: `xdotool`). Your previous clipboard is restored.
- **Floating overlay** (GTK4 + layer-shell): never steals focus, click-through. Three styles (`pill`, `waveform`, `vu_meter`), animated states (recording / paused / processing / error / success) and a live word-by-word transcript preview. Themed with a built-in palette or your own CSS file ([theming](docs/theming.md)).
- **Multiple speech engines** — Google (free), Vosk (offline, streaming), Whisper (offline, most accurate), Google Cloud. Automatic fallback after repeated failures.
- **Smart stop** — adaptive silence detection that calibrates to your room noise, or stop it yourself.
- **Hardened** — the control socket is `0600`, transcribed text is never executed, control characters are stripped.
- **Self-diagnosing** — `voicetype doctor` tells you what's missing and how to install it.
- **Well tested** — 689 automated tests; the core runs without a microphone, network or GUI.

> **Status: v0.1.** The core is unit-tested, but the parts that need a real desktop (overlay, shortcut registration, paste injection, microphone) must be verified on your machine. Run `voicetype doctor` first and see [docs/inject-matrix.md](docs/inject-matrix.md).

## 🧩 How it works

```
 Super+Alt+V ──► voicetype-toggle ──► daemon (Unix socket)
                                         │
        ┌────────────────────────────────┼─────────────────────────┐
        ▼                                ▼                         ▼
   AudioCapture                     Dispatcher                  Overlay
 16 kHz mono mic          IDLE → LISTENING → TRANSCRIBING      (GTK4 layer-shell)
        │                       → INJECTING → IDLE
        ▼                                │
  Silence detector                       ▼
        │              STT provider ─► Post-processing ─► Translation ─► Text injector
        └─────────────► (Google/Vosk/       (ZWNJ, digits,     (optional)    (paste into the
                         Whisper)            English terms)                   focused app)
```

## 📋 Requirements

| | |
|---|---|
| OS | Linux — **Arch + KDE Plasma 6 (Wayland)** is the primary target; X11 supported |
| Python | 3.11 or newer |
| Audio | PipeWire (or PulseAudio) and a microphone |
| Wayland injection | `wl-clipboard`, `ydotool` (+ running `ydotoold`) — or `wtype` instead of the paste route |
| X11 injection | `xdotool`, `xclip` |
| Overlay (optional) | `gtk4`, `gtk4-layer-shell`, `python-gobject`, `python-cairo` |

## 🚀 Quick start

### Linux (any distribution)

```bash
git clone https://github.com/isalehgholami/voicetype.git
cd voicetype
chmod +x scripts/*.sh             # safety net: zip/tar downloads can drop the exec bit

./scripts/install-user.sh --system-deps   # system packages + pipx + service + config + Super+Alt+V
#   → log out and back in so the new 'input'/'audio' groups apply

voicetype doctor                   # every required line should be [✓]
```

The installer is **distribution-aware**: it detects Arch, Debian/Ubuntu, Fedora, openSUSE, Alpine and Void,
checks what is missing and — with `--system-deps` — installs only that, with `sudo`. Without `--system-deps`
it stays root-free and prints the exact command for your package manager.

It then installs VoiceType into a **pipx** venv (no root, no PEP 668 error), writes and starts the systemd
*user* unit, creates `~/.config/voicetype/config.toml` with a short guide, registers the KDE shortcut and
verifies the whole installation — every step prints what it did.

> Arch blocks `pip install --user` (PEP 668), so the script uses **pipx**. Prefer a system package?
> `cd packaging && makepkg -si` (`python-sounddevice` and `python-speechrecognition` come from the AUR — use `yay`/`paru`).
> Different shortcut: `HOTKEY=Ctrl+Alt+Space ./scripts/install-user.sh`.

The installer is idempotent — **run it again after `git pull`**:

```bash
git pull && ./scripts/install-user.sh     # updates the code + dependencies, repairs a broken install
```

It updates the installed code *and* its dependencies, rebuilds a broken venv, rewrites the systemd
user unit, restarts the daemon so the new code is live, and makes sure
`~/.config/voicetype/config.toml` exists (a broken config is backed up as `config.toml.bak-<timestamp>`
and replaced by the defaults — your settings are never lost).

Now click into any text field, press **Super + Alt + V**, speak, and press it again (or just stay silent for 3 s).

### Options

```bash
./scripts/install-user.sh socks,vosk,whisper,hotkey,translate   # pick any: socks · vosk · whisper · hotkey · translate
./scripts/install-user.sh --system-deps      # also install the missing system packages (sudo)
./scripts/install-user.sh --reinstall        # rebuild the pipx venv from scratch
./scripts/install-user.sh --reset-extras socks  # install exactly these extras (drop the others)
./scripts/install-user.sh --no-hotkey        # leave the global shortcut alone
./scripts/install-user.sh --no-service       # do not touch the systemd user unit
./scripts/install-user.sh --no-mic-check     # skip the microphone test
./scripts/install-user.sh --hotkey Ctrl+Alt+Space
./scripts/install-user.sh -v                 # show the full output of every command
./scripts/install-user.sh --help
voicetype models install fa-small             # download the Persian Vosk model (~53 MB)
```

Extras of a previous run are kept, so re-running the script never silently removes a feature you installed.

### Other desktops

The script registers the shortcut automatically on **KDE Plasma**. On every other desktop it prints the
exact binding you need (Hyprland, Sway/i3, GNOME) — or bind `voicetype-toggle` yourself.
If you prefer to install by hand: `pip install -e .` and then `voicetype config init`.

## 🎮 Usage

| Action | How |
|---|---|
| Start / stop dictation | **Super + Alt + V** (or `voicetype toggle`) |
| Cancel without typing | `voicetype cancel` — or **Esc** in hold mode |
| Auto-stop | after 3 s of silence (once you've spoken) |
| Check the daemon | `voicetype status` |
| Diagnose problems | `voicetype doctor` |
| Test internet / proxy | `voicetype net-test` |

### CLI reference

```text
voicetype run                                run the daemon in the foreground (normally via systemd)
voicetype toggle|start|stop|cancel|quit      control a running daemon
voicetype status                             show state and active provider
voicetype reload                             re-read the config without restarting
voicetype profile [name|next|default]        list / switch config profiles
voicetype doctor                             environment checklist with install hints
voicetype net-test [--proxy URL] [--url URL]  check connectivity through the configured proxy
voicetype config init|path|check             create / locate / validate the config file
voicetype config init --repair              like init, but a broken config is backed up and replaced
voicetype models list|install <fa-small|fa> [--sha256 …]  download / verify a Vosk model
voicetype translate "سلام دنیا"              translate a text into [translate] language
```

### Profiles

Drop partial configs into `~/.config/voicetype/profiles/<name>.toml`; they are merged over the base config.

```toml
# ~/.config/voicetype/profiles/code.toml  — no auto-punctuation while coding
[postprocess]
auto_punctuate = false
capitalize_first = false
```

`voicetype profile code` switches at runtime; bind `voicetype profile next` to a second shortcut to cycle.

## ⚙️ Configuration

Everything lives in `~/.config/voicetype/config.toml` (override with `VOICETYPE_CONFIG`). **Every key is optional** — create the fully commented defaults with `voicetype config init`, validate edits with `voicetype config check`, apply them with `voicetype reload`. If the file ever gets broken, `voicetype config init --repair` (and the installer) back it up as `config.toml.bak-<timestamp>` and write the defaults again.

```toml
[stt]
provider = "google_free"        # google_free | vosk | whisper | google_cloud
language = "fa-IR"
silence_threshold_seconds = 3.0
fallback_provider = "vosk"      # used after 3 consecutive failures

[overlay]
position = "top"                # top | center | bottom
style = "pill"                  # pill | waveform | vu_meter
theme = "tokyo-night"           # palette name, "dark"/"light", or the path to a .css file
accent = ""                     # e.g. "#ff8800" — overrides the palette's accent colour
opacity = 0.86                  # opacity of the card the overlay draws on (0.0–1.0)
animations = true               # master switch for motion: false = instant, no pulsing
show_audio_meter = true         # false = the visualization ignores the microphone
show_transcript = true          # live word-by-word transcript above the overlay
transcript_words = 4            # how many words stay on screen (1–12)
font = "monospace, DejaVu Sans Mono, Liberation Mono, Noto Sans Mono"  # every text in the overlay
fade_ms = 140                   # length of the show/hide transition
sounds = false                  # audible cue when recording starts, stops or fails

[injector]
backend = "auto"                # auto | paste | wtype | ydotool | xdotool

[network]
proxy = ""                      # http://host:port | socks5h://user:pass@host:1080
no_proxy = "localhost,127.0.0.1,::1"

[postprocess]
english_terms = true
normalize_zwnj = true
digits = "keep"                 # keep | latin | persian

[postprocess.custom_replacements]
"می شود" = "می‌شود"
"re:\\bqt\\d\\b" = "Qt"         # keys starting with re: are regular expressions

[translate]                     # optional — off by default
enabled = false
language = "en"                 # "en", "English", "de", "zh-CN", …
```

For palettes and custom themes: [docs/theming.md](docs/theming.md). The default `pill` style paints fixed colours, so the palette is only visible in the `waveform` and `vu_meter` styles.

Overlay and hold-mode hotkey changes need a daemon restart; everything else applies on `reload`. The one exception is the theme *file*: when `overlay.theme` points at a `.css` file, editing it repaints the overlay right away ([theming](docs/theming.md)).

## 🗣️ Speech providers

| Provider | Needs | Offline | Streaming partials | Notes |
|---|---|:-:|:-:|---|
| `google_free` *(default)* | internet | ❌ | ✅ segmented | Undocumented endpoint; **audio is sent to Google** |
| `vosk` | `vosk` + model | ✅ | ✅ native | Light and private; lower accuracy on mixed text |
| `whisper` | `faster-whisper` | ✅ | ✅ segmented | Most accurate offline; CPU or CUDA |
| `google_cloud` | service-account key | ❌ | ✅ segmented | Paid, optional |

### Live text while you speak (streaming)

Vosk streams natively. For the other providers VoiceType uses **segmented streaming**: your audio is cut at pauses and each
sentence is sent *while you are still talking*, and the sentence in progress is refreshed every ~1.5 s. Text shows up in the overlay
as you speak, and when you stop only the last short segment is left — so the final text lands almost instantly.

```toml
[stt]
streaming = true                 # false = classic batch mode (send everything at the end)
stream_interim_seconds = 1.5     # refresh the sentence in progress; 0 = only at pauses
stream_pause_seconds = 0.6       # silence that ends a sentence
```

Trade-off: more (short) requests to Google — set `stream_interim_seconds = 0` to send only at pauses, or `streaming = false` if you hit rate limits.
Failed segments are retried at the end, and a recording whose speech couldn't be detected is transcribed whole, so streaming never loses audio.

Compare them on your own voice: record the 50-sentence corpus and run the benchmark ([docs/benchmarks.md](docs/benchmarks.md)).

## 🌍 Translate what you speak (optional)

Speak Persian, get English — or any other language. The translation is **off by default**:

```bash
./scripts/install-user.sh translate        # installs py-googletrans
# (googletrans is always installed — the daemon needs it to validate the config)
```

```toml
[translate]
enabled = true
language = "en"      # "en", "English", "de", "zh-CN", … — the target language
source = "auto"      # "auto" detects what you spoke
```

```bash
voicetype translate "سلام دنیا"    # → Hello world.
```

The Persian clean-up runs on your words, the translation rewrites them in the target
language, and the result is typed at the cursor. If Google cannot be reached, your
original text is injected unchanged (with a warning in the overlay) — a translation
never eats your words. Details: [docs/translation.md](docs/translation.md).

## 🌐 Behind a firewall? Use a proxy
Internet blocked or unreliable? Send all of VoiceType's traffic (Google speech, model downloads) through an HTTP or SOCKS proxy:

```toml
[network]
proxy = "socks5h://127.0.0.1:1080"     # or  http://127.0.0.1:8080  ·  user:pass@ is supported
```

```bash
voicetype reload && voicetype net-test      # OK / FAIL with the reason
```

SOCKS needs PySocks (`./scripts/install-user.sh socks`). If the proxy is unusable VoiceType **refuses to connect directly** instead of leaking your audio. Details: [docs/network.md](docs/network.md).

## 🔒 Privacy & security

- `google_free` uploads your audio to Google. Use `vosk` or `whisper` for fully offline operation — see [docs/privacy.md](docs/privacy.md).
- Translation is off by default; when it is on, the *text* of your transcript is sent to Google too.
- No audio is stored by default; transcripts are never written to logs.
- The control socket `$XDG_RUNTIME_DIR/voicetype.sock` is mode `0600`; transcribed text is never passed to a shell or executed.

## 🛠️ Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check .          # lint
pytest                # 689 tests, no microphone / network / GUI needed
```

- The core (config, dispatcher, STT, post-processing, injector logic) runs on any OS; GTK, evdev and PortAudio are imported lazily.
- Unix-socket tests are skipped on Windows; run `pytest` on Linux for the full set.
- Add a speech provider without touching the dispatcher:
  ```python
  from voicetype.stt import registry
  registry.register("mine", lambda opts: MyBackend(opts))   # then: provider = "mine"
  ```
- CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)): lint + tests on Python 3.11–3.13 and a `makepkg` build in an Arch container.

### Project layout

```
voicetype/        daemon, dispatcher, audio, stt/, postprocess/, translate/, injector, overlay, control, cli …
tests/            unit + integration tests (fixed WAV fixture, 50-sentence corpus)
scripts/          install-user.sh (the installer), install-hotkey.sh, bench.py, record_corpus.py, release.sh
packaging/        PKGBUILD, systemd user unit, .desktop files, AUR package
spikes/           hardware experiments (injection matrix, OSD, hotkey, STT comparison)
docs/             architecture, ADRs, privacy, compatibility, troubleshooting, benchmarks
```

## 🩺 Troubleshooting

Start with **`voicetype doctor`** — every `[✗]` comes with an install hint.

| Symptom | Fix |
|---|---|
| `daemon is not running` | `systemctl --user start voicetype` · logs: `journalctl --user -u voicetype -f` |
| Shortcut does nothing | Does `voicetype toggle` work in a terminal? Re-run `install-hotkey.sh` or add the command in *System Settings → Shortcuts* |
| Text isn't typed | `systemctl --user enable --now ydotool`; check *text injection backend* in `doctor` |
| Wrong paste in terminals | install `kdotool`, or set `[injector] paste_shortcut = "ctrl+shift+v"` |
| No overlay | install `gtk4-layer-shell` + `python-gobject` (GNOME has no layer-shell) |
| Network errors / blocked | set `[network] proxy`, then `voicetype net-test` ([docs/network.md](docs/network.md)) |
| Stops too early / too late | tune `[stt] silence_threshold_seconds` |

More: [docs/troubleshooting.md](docs/troubleshooting.md) · [docs/known-issues.md](docs/known-issues.md) · [docs/compatibility.md](docs/compatibility.md)

## 🗺️ Documentation

[Architecture](docs/VoiceType-Architecture.md) (read the errata first) · [Decisions (ADRs)](docs/adr/) · [Theming](docs/theming.md) · [Post-processing](docs/postprocess.md) · [Translation](docs/translation.md) · [Network & proxy](docs/network.md) · [Privacy](docs/privacy.md) · [Injection matrix](docs/inject-matrix.md) · [Benchmarks](docs/benchmarks.md)

## 🙏 Credits

Design patterns — adaptive silence detection, control socket, paste-based injection, layer-shell OSD — follow [hyprwhspr](https://github.com/goodroot/hyprwhspr). Offline recognition by [Vosk](https://github.com/alphacep/vosk-api) and [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## 📄 License

[MIT](LICENSE)
