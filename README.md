<div align="center">

# 🎙️ VoiceType

**Voice typing for Linux — speak Persian (and English terms), get text at your cursor.**

Press a shortcut, talk, and the transcript is typed into whatever app you're using:
browser, Telegram, terminal, IDE, LibreOffice.

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Arch%20Linux%20%C2%B7%20KDE%20Plasma%206-1793D1?logo=archlinux&logoColor=white)
![Tests](https://img.shields.io/badge/tests-380_passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)

[Features](#-features) · [Quick start](#-quick-start) · [Usage](#-usage) · [Configuration](#%EF%B8%8F-configuration) · [Providers](#%EF%B8%8F-speech-providers) · [Development](#%EF%B8%8F-development) · [Troubleshooting](#-troubleshooting) · [فارسی](README.fa.md)

</div>

---

## ✨ Features

- **Persian-first dictation** with correct half-spaces (ZWNJ), Persian punctuation and digits.
- **Mixed Persian + English** — technical terms are normalised: «پایتون» → `Python`, «گیت‌هاب» → `GitHub` (40+ terms, extensible).
- **Works in any app** — text is injected at the cursor via clipboard paste (Wayland: `wl-copy` + `ydotool`; X11: `xdotool`). Your previous clipboard is restored.
- **Floating overlay** (GTK4 + layer-shell): live audio level, status and partial text. Never steals focus, click-through.
- **Multiple speech engines** — Google (free), Vosk (offline, streaming), Whisper (offline, most accurate), Google Cloud. Automatic fallback after repeated failures.
- **Smart stop** — adaptive silence detection that calibrates to your room noise, or stop it yourself.
- **Hardened** — the control socket is `0600`, transcribed text is never executed, control characters are stripped.
- **Self-diagnosing** — `voicetype doctor` tells you what's missing and how to install it.
- **Well tested** — 380 automated tests; the core runs without a microphone, network or GUI.

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
        │              STT provider ─► Post-processing ─► Text injector
        └─────────────► (Google/Vosk/       (ZWNJ, digits,     (paste into the
                         Whisper)            English terms)     focused app)
```

## 📋 Requirements

| | |
|---|---|
| OS | Linux — **Arch + KDE Plasma 6 (Wayland)** is the primary target; X11 supported |
| Python | 3.11 or newer |
| Audio | PipeWire (or PulseAudio) and a microphone |
| Wayland injection | `wl-clipboard`, `ydotool` (+ running `ydotoold`) |
| X11 injection | `xdotool`, `xclip` |
| Overlay (optional) | `gtk4`, `gtk4-layer-shell`, `python-gobject`, `python-cairo` |

## 🚀 Quick start

### Arch Linux

```bash
git clone https://github.com/<your-username>/voicetype.git
cd voicetype

./scripts/setup-arch.sh            # installs system packages, adds you to 'input' + 'audio'
#   → log out and back in so the new groups apply

./scripts/install-user.sh          # pipx install + systemd user service + config + Super+Alt+V shortcut
systemctl --user enable --now ydotool
voicetype doctor                   # every required line should be [✓]
```

> Arch blocks `pip install --user` (PEP 668), so the script uses **pipx**. Prefer a system package?
> `cd packaging && makepkg -si` (`python-sounddevice` and `python-speechrecognition` come from the AUR — use `yay`/`paru`).
> Different shortcut: `HOTKEY=Ctrl+Alt+Space ./scripts/install-user.sh`.

Now click into any text field, press **Super + Alt + V**, speak, and press it again (or just stay silent for 3 s).

### Other distros

Install the equivalents of the packages in [`scripts/setup-arch.sh`](scripts/setup-arch.sh), then `pip install -e .` and follow the same steps from `voicetype config init`.
If your desktop isn't KDE, bind the command `voicetype-toggle` to a key in your compositor/DE settings instead of running `install-hotkey.sh`.

### Optional extras

```bash
./scripts/install-user.sh socks,vosk,whisper,hotkey   # pick any: socks · vosk · whisper · hotkey
voicetype models install fa-small                     # download the Persian Vosk model (~53 MB)
```

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
voicetype run                       run the daemon in the foreground (normally via systemd)
voicetype toggle|start|stop|cancel  control a running daemon
voicetype status                    show state and active provider
voicetype reload                    re-read the config without restarting
voicetype profile [name|next|default]   list / switch config profiles
voicetype doctor                    environment checklist with install hints
voicetype net-test [--proxy URL]    check connectivity through the configured proxy
voicetype config init|path|check    create / locate / validate the config file
voicetype models list|install <fa-small|fa> [--sha256 …]
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

Everything lives in `~/.config/voicetype/config.toml` (override with `VOICETYPE_CONFIG`). **Every key is optional** — create the fully commented defaults with `voicetype config init`, validate edits with `voicetype config check`, apply them with `voicetype reload`.

```toml
[stt]
provider = "google_free"        # google_free | vosk | whisper | google_cloud
language = "fa-IR"
silence_threshold_seconds = 3.0
fallback_provider = "vosk"      # used after 3 consecutive failures

[overlay]
position = "top"                # top | center | bottom
theme = "dark"                  # dark | light
opacity = 0.86

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
```

Overlay and hold-mode hotkey changes need a daemon restart; everything else applies on `reload`. Details: [docs/postprocess.md](docs/postprocess.md).

## 🗣️ Speech providers

| Provider | Needs | Offline | Streaming partials | Notes |
|---|---|:-:|:-:|---|
| `google_free` *(default)* | internet | ❌ | ❌ | Undocumented endpoint; **audio is sent to Google** |
| `vosk` | `vosk` + model | ✅ | ✅ | Light and private; lower accuracy on mixed text |
| `whisper` | `faster-whisper` | ✅ | ❌ | Most accurate offline; CPU or CUDA |
| `google_cloud` | service-account key | ❌ | ❌ | Paid, optional |

Compare them on your own voice: record the 50-sentence corpus and run the benchmark ([docs/benchmarks.md](docs/benchmarks.md)).

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
- No audio is stored by default; transcripts are never written to logs.
- The control socket `$XDG_RUNTIME_DIR/voicetype.sock` is mode `0600`; transcribed text is never passed to a shell or executed.

## 🛠️ Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check .          # lint
pytest                # 380 tests, no microphone / network / GUI needed
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
voicetype/        daemon, dispatcher, audio, stt/, postprocess/, injector, overlay, control, cli …
tests/            unit + integration tests (fixed WAV fixture, 50-sentence corpus)
scripts/          setup-arch.sh, install-user.sh, install-hotkey.sh, bench.py, record_corpus.py, release.sh
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

[Architecture](docs/VoiceType-Architecture.md) (read the errata first) · [Decisions (ADRs)](docs/adr/) · [Post-processing](docs/postprocess.md) · [Network & proxy](docs/network.md) · [Privacy](docs/privacy.md) · [Injection matrix](docs/inject-matrix.md) · [Benchmarks](docs/benchmarks.md)

## 🙏 Credits

Design patterns — adaptive silence detection, control socket, paste-based injection, layer-shell OSD — follow [hyprwhspr](https://github.com/goodroot/hyprwhspr). Offline recognition by [Vosk](https://github.com/alphacep/vosk-api) and [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## 📄 License

[MIT](LICENSE)
