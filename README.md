<div align="center">

# 🎙️ VoiceType

**Persian-first voice typing for Linux.** Press a shortcut, speak, and the transcript is pasted at the cursor of whichever application has focus: browser, terminal, IDE, messenger, or office suite.

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Arch%20%2B%20KDE%20Plasma%206-1793D1?logo=archlinux&logoColor=white)
![Tests](https://img.shields.io/badge/tests-733_passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)

[Features](#features) · [How it works](#how-it-works) · [Requirements](#requirements) · [Installation](#installation) · [Usage](#usage) · [Configuration](#configuration) · [Providers](#speech-providers) · [Development](#development) · [Troubleshooting](#troubleshooting) · [فارسی](README.fa.md)

</div>

---

## Features

- **Persian-first dictation.** Correct half-spaces (ZWNJ), Persian punctuation, and a configurable digit system.
- **Mixed Persian and English.** Persian transliterations of technical terms are normalised to their canonical Latin spelling: «پایتون» becomes `Python`, «گیت‌هاب» becomes `GitHub`. 44 canonical terms covering 62 Persian spellings; further mappings can be added through `[postprocess.custom_replacements]`.
- **Optional translation.** Transcripts can be translated into English or any other supported language before they are injected. Disabled by default.
- **Injection into any application.** Text is delivered by clipboard paste (Wayland: `wl-copy` with `ydotool`; X11: `xclip` with `xdotool`) or by direct keystroke injection (`wtype`, `ydotool`, `xdotool`). The previous clipboard contents are restored afterwards.
- **Floating overlay.** GTK4 with the `wlr-layer-shell` protocol, so it never takes focus and clicks pass through. Three visualisation styles (`pill`, `waveform`, `vu_meter`), animated states for recording, paused, processing, error, and success, and a live word-by-word transcript preview. Themed from a built-in palette or your own CSS file, see [theming](docs/theming.md).
- **Four speech engines.** Google (free, online), Vosk (offline, streaming), Whisper (offline, most accurate), Google Cloud. Automatic fallback to a secondary provider after repeated failures.
- **Adaptive stop.** Silence detection calibrates against the noise floor of the room instead of using a fixed threshold.
- **Predictable security posture.** The control socket is created with mode `0600`, control characters are stripped from transcripts, and transcribed text is never passed to a shell or evaluated.
- **Self-diagnosing.** `voicetype doctor` inspects the environment and prints the exact command needed to fix each missing dependency.
- **Tested.** 733 automated tests. The core runs without a microphone, network access, or a display server.

> **Status: v0.1.** The core is covered by unit and integration tests, but the parts that require a real desktop session (overlay rendering, shortcut registration, paste injection, microphone capture) can only be verified on your own machine. Run `voicetype doctor` first and read [docs/inject-matrix.md](docs/inject-matrix.md) for the results recorded so far.

## How it works

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

The daemon owns one state machine. A shortcut sends `toggle` over a Unix socket in `$XDG_RUNTIME_DIR`; the dispatcher opens the microphone, streams audio to the selected provider, post-processes the transcript, and hands the result to the injector.

## Requirements

VoiceType targets Linux with a Wayland or X11 session. Arch with KDE Plasma 6 on Wayland is the primary development target; the installer also supports Debian/Ubuntu, Fedora, openSUSE, Alpine, and Void.

| Purpose | Tools | Needed for |
|---|---|---|
| Audio server | PipeWire with WirePlumber (`pipewire`, `wireplumber`), or PulseAudio | always |
| Wayland injection | `wl-clipboard` (`wl-copy`) and `ydotool` with a running `ydotoold` | the default paste route on Wayland |
| Wayland injection (alternative) | `wtype` | typing directly instead of via the clipboard |
| X11 injection | `xdotool` and `xclip` | injection on X11 |
| Terminal detection | `kdotool` on Wayland, `xdotool` on X11 | using `Ctrl+Shift+V` instead of `Ctrl+V` in a terminal |
| Overlay | `gtk4`, `gtk4-layer-shell`, `python-gobject`, `python-cairo` | the floating overlay |
| Notifications | `libnotify` (`notify-send`) | desktop notifications on error |
| Media control | `playerctl` | pausing music while recording |
| Python | Python 3.11 or newer | the daemon |
| pipx | `python-pipx` | the isolated installation performed by the installer |

The package names above are the Arch ones. The installer maps them for every supported distribution; the Debian and Ubuntu names differ for a few of them: `libgtk-4-1`, `gir1.2-gtklayershell-0.0`, `python3-gi`, `python3-cairo`, `libnotify-bin`, `python3`, and `pipx`.

Python dependencies are declared in `pyproject.toml` and installed by the installer: `sounddevice` and `SpeechRecognition` are required, while `vosk`, `faster-whisper`, `evdev`, `PySocks`, and `googletrans` are optional extras.

Two further requirements apply to specific features:

- **Hold-mode shortcut.** `[hotkey] toggle_mode = "hold"` reads the keyboard through `evdev` and therefore needs the `input` group and write access to `/dev/uinput`. The default `press` mode uses the desktop shortcut and needs neither.
- **Offline recognition.** Vosk needs a model (`voicetype models install fa-small`); Whisper downloads its weights on first use and caches them locally.

## Installation

### Automated installation

The installer is the recommended path. It is distribution-aware, installs into a `pipx` virtual environment, and never modifies your system without `--system-deps`.

```bash
git clone https://github.com/isalehgholami/voicetype.git
cd voicetype
chmod +x scripts/*.sh              # archives may drop the executable bit

./scripts/install-user.sh --system-deps
```

The script runs these steps and reports the result of each one:

1. **Preflight.** Refuses to run as root, and verifies that Python 3.11+ and `pipx` are available. `pipx` is required because PEP 668 marks the system interpreter as externally managed, which breaks `pip install --user`.
2. **Distribution detection.** Reads `/etc/os-release` and selects `pacman`, `apt-get`, `dnf`, `zypper`, `apk`, or `xbps-install`.
3. **System packages.** Checks every dependency from the table above. With `--system-deps` the missing ones are installed through `sudo`; without it, the script stays root-free and prints the exact command for your package manager.
4. **Groups and services.** Adds your account to the `input` and `audio` groups and enables the PipeWire and ydotool user services.
5. **Microphone test.** Records three seconds with `pw-record` and reports the captured level in dBFS.
6. **Python environment.** Installs VoiceType into a `pipx` venv with the selected extras.
7. **Configuration.** Writes `~/.config/voicetype/config.toml` with a short guide at the top. An existing valid file is kept as it is; an invalid one is backed up as `config.toml.bak-<timestamp>` and replaced with the defaults.
8. **Service unit.** Writes `~/.config/systemd/user/voicetype.service`, points it at the installed binary, and enables it for the next login.
9. **Daemon.** Starts the daemon and waits until it answers on its control socket.
10. **Shortcut.** Registers `Meta+Alt+V` as a global shortcut on KDE Plasma. On other desktops it prints the binding you need to add yourself.
11. **Verification.** Prints a summary of what is working and what still needs attention.

Log out and back in after the first run so that the `input` and `audio` group memberships take effect.

#### Installer options

| Option | Effect |
|---|---|
| *(no arguments)* | Install or update, keeping the extras installed by a previous run. The default extra is `socks`. |
| `<extras>` | Comma-separated list of `socks`, `vosk`, `whisper`, `hotkey`, `translate`. Extras from previous runs are preserved unless `--reset-extras` is given. |
| `--system-deps` | Install the missing system packages with `sudo`. |
| `--reinstall` | Delete the `pipx` venv and rebuild it from scratch. |
| `--reset-extras` | Install exactly the requested extras and drop the others. |
| `--no-hotkey` | Leave the global shortcut untouched. |
| `--no-service` | Do not touch the systemd user unit. |
| `--no-mic-check` | Skip the microphone test. |
| `--hotkey <combo>` | Register a different shortcut, for example `--hotkey Ctrl+Alt+Space`. The `HOTKEY` environment variable has the same effect. |
| `-v`, `--verbose` | Show the full output of every command the script runs. |
| `-q`, `--quiet` | Report problems only. |
| `--help` | Print the usage summary. |

The `translate` extra is always installed, whether or not you ask for it, so that the configured translation language can be validated and enabling translation later needs no further work.

The installer is idempotent. Run it again after `git pull` to update the code and its dependencies, rebuild a broken venv, rewrite the unit file, and restart the daemon so the new code is live:

```bash
git pull && ./scripts/install-user.sh
```

#### Verifying the installation

```bash
voicetype doctor
```

`doctor` walks the whole environment and prints one line per check. A line marked `[✓]` is satisfied, `[✗]` is a required item that is missing, and `[!]` is an optional item that is missing or degraded. Each failing line comes with a hint describing what has to be installed or fixed.

### Manual installation

If you would rather manage the environment yourself, install into a virtual environment and start the daemon by hand:

```bash
git clone https://github.com/isalehgholami/voicetype.git
cd voicetype

python3 -m venv ~/.venvs/voicetype
source ~/.venvs/voicetype/bin/activate
pip install -e ".[socks,vosk]"          # or: pip install -e .

voicetype config init # write ~/.config/voicetype/config.toml
voicetype run                           # run the daemon in the foreground
```

Then bind `voicetype-toggle` to your preferred shortcut. On KDE Plasma, use *System Settings → Keyboard → Shortcuts → Custom Shortcuts*, with the command `voicetype-toggle`. On other desktops, bind `voicetype-toggle` in the compositor or desktop configuration.

`pipx` is a convenient alternative to a hand-made virtual environment:

```bash
pipx install "voicetype[socks,vosk]"
```

To run the daemon at login, copy `packaging/voicetype.service` into `~/.config/systemd/user/`. Adjust two paths: `ExecStart` must point at your `voicetype` binary, and `ExecStartPre` at the `wait-display` helper in `packaging/`, which delays start-up until the session has a display. Then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now voicetype
```

### Distribution packages

A `PKGBUILD` is included for Arch:

```bash
cd packaging && makepkg -si
```

`python-sounddevice` and `python-speechrecognition` are not in the Arch repositories; install them from the AUR with `yay` or `paru` first. The package installs the daemon, the systemd user unit in `/usr/lib/systemd/user/`, and a desktop entry for the toggle command. An AUR package definition is available in `packaging/aur/`.

```bash
systemctl --user enable --now voicetype
```

### Uninstalling

```bash
systemctl --user disable --now voicetype
pipx uninstall voicetype
rm ~/.config/systemd/user/voicetype.service
rm -rf ~/.config/voicetype ~/.local/lib/voicetype
rm -f ~/.local/share/applications/voicetype-toggle.desktop
rm -f ~/.local/share/kglobalaccel/voicetype-toggle.desktop
```

The last two files hold the KDE shortcut. Remove the binding in *System Settings → Keyboard → Shortcuts* as well. If you installed the Arch package, remove the unit from `/usr/lib/systemd/user/voicetype.service` and the package itself with `pacman -Rns voicetype-git`.

## Usage

### Controlling the daemon

| Action | How |
|---|---|
| Start or stop dictation | `Meta+Alt+V`, or `voicetype toggle` |
| Cancel without injecting | `voicetype cancel`, or `Esc` in hold mode |
| Stop automatically | after 3 seconds of silence, once something has been said |
| Show the current state | `voicetype status` |
| Re-read the configuration | `voicetype reload` |
| Check the environment | `voicetype doctor` |
| Test connectivity through the proxy | `voicetype net-test` |

### Command reference

| Command | Description |
|---|---|
| `voicetype run` | Run the daemon in the foreground. Normally started by systemd instead. |
| `voicetype toggle` | Start dictation, or stop it and inject the transcript. |
| `voicetype start` / `stop` | Start or stop dictation explicitly. |
| `voicetype cancel` | Discard the current recording without injecting anything. |
| `voicetype quit` | Shut the daemon down. |
| `voicetype status` | Report the daemon state (`idle`, `listening`, `transcribing`, `injecting`), the active provider, and the version. |
| `voicetype reload` | Re-read the configuration file without restarting the daemon. |
| `voicetype profile [name\|next\|default]` | List the profiles, switch to one, cycle through them, or return to the base configuration. |
| `voicetype doctor` | Run the environment checklist and print install hints. |
| `voicetype net-test [--proxy URL] [--url URL]` | Check connectivity through the configured proxy. |
| `voicetype config init\|path\|check` | Create, locate, or validate the configuration file. `init --repair` backs up a broken file and writes the defaults. |
| `voicetype models list\|install <fa-small\|fa>` | List or download the offline Vosk models. `--sha256` enforces a checksum. |
| `voicetype translate "سلام دنیا"` | Translate a text into the configured target language. |

`voicetype-toggle` is an alias for `voicetype toggle` and is the command to bind to a global shortcut.

### Profiles

A profile is a partial configuration stored in `~/.config/voicetype/profiles/<name>.toml`. Tables are merged recursively over the base configuration, so a profile only needs to contain the keys it changes.

```toml
# ~/.config/voicetype/profiles/code.toml — no sentence punctuation while coding
[postprocess]
auto_punctuate = false
capitalize_first = false
```

Switch profiles at runtime with `voicetype profile code`. `voicetype profile next` cycles through the available profiles in alphabetical order and returns to the base configuration at the end, which makes it convenient to bind to a second shortcut.

## Configuration

The configuration file is `~/.config/voicetype/config.toml`. Set `VOICETYPE_CONFIG` to use a different path.

Every key is optional; anything you leave out keeps its default. The loader validates types, enumerations, and ranges, and rejects unknown keys with an error that names the offending path.

```bash
voicetype config init     # write a fully commented file with the defaults
voicetype config path     # print the path the daemon uses
voicetype config check    # validate the current file
voicetype reload          # apply the changes
```

If the file becomes invalid, `voicetype config init --repair` copies it to `config.toml.bak-<timestamp>` and writes the defaults again. The installer performs the same repair automatically, so a broken file never blocks an update.

### Applying changes

`voicetype reload` re-reads the file and rebuilds everything the configuration touches: the audio capture, the speech backend, the injector, the post-processing pipeline, the translator, the log level, the overlay, and the hold-mode hotkey listener. The overlay is only torn down when its own settings changed, and it is replaced together with the other components, so the daemon never shows two overlays at once. If a recording is in progress, the new configuration is applied once the current cycle ends.

| Change | Takes effect |
|---|---|
| Every key | `voicetype reload` |
| `[overlay] theme` pointing at a `.css` file | immediately; the file is watched and the overlay repaints on change |

The desktop shortcut is registered by the installer, not by the daemon, so changing the key combination means re-running `scripts/install-hotkey.sh`.

### General

| Key | Default | Description |
|---|---|---|
| `log_level` | `"INFO"` | Verbosity of the daemon log: `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `notify_errors` | `true` | Send a desktop notification through `notify-send` when an error occurs. |

### `[hotkey]`

| Key | Default | Description |
|---|---|---|
| `combo` | `"super+alt+v"` | The key combination read by the `evdev` listener in hold mode. The desktop shortcut in `press` mode is registered by the installer and is not affected by this key. |
| `toggle_mode` | `"press"` | `press` toggles on each press and uses the desktop shortcut. `hold` records while the key is held and requires the `evdev` extra, the `input` group, and write access to `/dev/uinput`. |
| `escape_stops` | `true` | Whether the keys in `extra_stops` cancel the recording in hold mode. |
| `extra_stops` | `["esc"]` | Keys that cancel the recording in hold mode. |

### `[stt]`

| Key | Default | Description |
|---|---|---|
| `provider` | `"google_free"` | The speech engine: `google_free`, `vosk`, `whisper`, or `google_cloud`. See [Speech providers](#speech-providers). |
| `language` | `"fa-IR"` | BCP47 language tag sent to the provider. |
| `silence_threshold_seconds` | `3.0` | How long you must stay silent before the recording stops. Range 0.5 to 30. |
| `timeout_seconds` | `60.0` | Timeout for a single transcription request. Range 5 to 300. |
| `fallback_provider` | `"vosk"` | The provider to switch to after repeated failures. An empty string disables the fallback. |
| `fallback_after` | `3` | Number of consecutive failures that triggers the fallback. Range 1 to 20. |
| `streaming` | `true` | Show text while you speak. `false` sends one request after you stop. |
| `stream_interim_seconds` | `1.5` | How often the sentence in progress is refreshed. `0` sends text only at pauses. Range 0 to 10. |
| `stream_pause_seconds` | `0.6` | The silence that ends a sentence. Range 0.2 to 3. |
| `stream_max_segment_seconds` | `12.0` | The longest segment sent in one request. Range 3 to 30. |

The provider-specific sub-tables `[stt.vosk]`, `[stt.whisper]`, and `[stt.google_cloud]` hold their own settings:

| Key | Default | Description |
|---|---|---|
| `[stt.vosk] model_path` | `"/usr/share/vosk/vosk-model-fa-0.42"` | Directory of the Vosk model. `voicetype models install` prints the path to put here. |
| `[stt.whisper] model` | `"small"` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large`, `large-v2`, or `large-v3`. |
| `[stt.whisper] device` | `"cpu"` | `cpu` or `cuda`. |
| `[stt.google_cloud] keyfile` | `""` | Path to the service-account key. Required when the provider is `google_cloud`. |

### `[audio]`

| Key | Default | Description |
|---|---|---|
| `device` | `""` | The microphone. An empty string uses the system default; a number selects it by index, a string by name substring. |
| `blocksize` | `1600` | Samples per callback. 1600 samples at 16 kHz is 100 ms. Range 160 to 16000. |
| `max_buffer_seconds` | `120.0` | Maximum length of a single recording. Older audio is dropped beyond this limit. Range 5 to 3600. |
| `debug_save_audio` | `false` | Write the captured audio to disk for debugging. |
| `pause_media` | `false` | Pause the running media player through `playerctl` while recording. |

### `[overlay]`

| Key | Default | Description |
|---|---|---|
| `enabled` | `true` | Whether the overlay is shown at all. The daemon works without it. |
| `position` | `"top"` | Vertical position: `top`, `center`, or `bottom`. |
| `anchor_horizontal` | `"center"` | Horizontal anchor: `left`, `center`, or `right`. |
| `offset_x` | `0` | Horizontal offset in pixels. Range −1920 to 1920. |
| `offset_y` | `12` | Vertical offset in pixels. Range −1080 to 1080. |
| `theme` | `"dark"` | A palette name (`tokyo-night`, `nord`, `dracula`, `catppuccin-mocha`, `rose-pine`, `gruvbox-dark`, `tokyo-night-light`), the legacy values `dark` or `light`, or the path to a `.css` file. See [theming](docs/theming.md). |
| `accent` | `""` | Hex colour that overrides the accent of the palette, for example `"#ff8800"`. Empty means the palette decides. |
| `opacity` | `0.86` | Opacity of the card the overlay draws on. Range 0.0 to 1.0. |
| `width` | `360` | Overlay width in pixels. Range 120 to 1920. |
| `height` | `96` | Overlay height in pixels. Range 48 to 480. |
| `style` | `"pill"` | Visualisation style: `pill`, `waveform`, or `vu_meter`. The `pill` style paints fixed colours, so the palette only shows in the other two styles. |
| `animations` | `true` | Master switch for motion. `false` makes every state change instant. |
| `show_audio_meter` | `true` | Whether the visualisation reacts to the microphone level. |
| `show_transcript` | `true` | Whether the live word-by-word preview is shown above the overlay. |
| `show_partial_text` | `true` | Whether partial results are rendered at all. Both this and `show_transcript` must be enabled for the preview. |
| `transcript_words` | `4` | How many words the live preview keeps. Range 1 to 12. |
| `font` | `"monospace, DejaVu Sans Mono, ..."` | The font stack used for every text the overlay draws. |
| `fade_ms` | `140` | Duration of the show and hide transition in milliseconds. Range 0 to 1000. |
| `sounds` | `false` | Play an audible cue when recording starts, stops, or fails. |

### `[injector]`

| Key | Default | Description |
|---|---|---|
| `backend` | `"auto"` | How the text reaches the application: `auto`, `paste`, `wtype`, `ydotool`, or `xdotool`. `auto` picks the best backend the session and the installed tools allow. |
| `typing_delay_ms` | `0` | Delay between keystrokes when typing directly. Range 0 to 50. |
| `strip_whitespace` | `false` | Strip leading and trailing whitespace before injecting. |
| `paste_shortcut` | `"auto"` | The shortcut used to paste: `auto`, `ctrl+v`, or `ctrl+shift+v`. `auto` looks at the class of the focused window and uses `ctrl+shift+v` for a known terminal, `ctrl+v` otherwise. The lookup needs `kdotool` on Wayland and `xdotool` on X11; without it, `ctrl+v` is always used. |
| `restore_clipboard` | `true` | Restore the previous clipboard contents after the paste. |
| `release_modifiers` | `true` | Release every modifier key before typing, so a shortcut that is still held cannot turn the paste into a combination. |

### `[postprocess]`

The post-processing pipeline runs these stages in order: strip, hallucination filter, custom replacements, English terms, ZWNJ normalisation, digit conversion, Persian punctuation, capitalisation, and automatic punctuation.

| Key | Default | Description |
|---|---|---|
| `normalize_zwnj` | `true` | Insert the zero-width non-joiner where Persian needs a half-space, as in «می‌شود» instead of «می شود». |
| `english_terms` | `true` | Replace Persian transliterations of technical terms with their canonical Latin spelling. |
| `digits` | `"keep"` | Digit system: `keep`, `latin`, or `persian`. |
| `persian_punctuation` | `true` | Use the Persian comma and question mark. |
| `capitalize_first` | `true` | Capitalise the first letter of the transcript. |
| `auto_punctuate` | `true` | Append a full stop when the transcript ends without punctuation. |
| `filter_hallucinations` | `true` | Discard empty results and the repeated phrases that some providers emit when they detect no speech. |
| `voice_commands` | `false` | Interpret the whole utterance as a command such as «خط جدید» (new line) or «پاک کن» (clear). Only applies when the utterance is nothing but a command. |

Custom replacements are defined in `[postprocess.custom_replacements]`. Keys that start with `re:` are treated as regular expressions; everything else is a literal match.

```toml
[postprocess.custom_replacements]
"می شود" = "می‌شود"
"re:\\bqt\\d\\b" = "Qt"
```

### `[network]`

| Key | Default | Description |
|---|---|---|
| `proxy` | `""` | The proxy for all outgoing traffic: `http://host:port` or `socks5h://user:pass@host:1080`. An empty string falls back to the `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` environment variables. |
| `no_proxy` | `"localhost,127.0.0.1,::1"` | Hosts that bypass the proxy. |

### `[translate]`

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Translate the transcript before injecting it. |
| `language` | `"en"` | The target language, either a code such as `en` or `zh-CN` or a name such as `English`. |
| `source` | `"auto"` | The source language. `auto` detects what you spoke. |
| `timeout_seconds` | `15.0` | Give up on a translation after this many seconds. Range 1 to 120. |

### The complete file

`voicetype config init` writes the following, with every key and its default value.

```toml
# VoiceType configuration — every key is optional; these are the defaults.
log_level = "INFO"
notify_errors = true       # desktop notification on errors (notify-send)

[hotkey]
combo = "super+alt+v"      # used by install-hotkey.sh and the optional evdev listener
toggle_mode = "press"      # press | hold (hold needs the evdev listener + group 'input')
escape_stops = true
extra_stops = ["esc"]

[stt]
provider = "google_free"   # google_free | google_cloud | vosk | whisper
language = "fa-IR"
silence_threshold_seconds = 3.0
timeout_seconds = 60.0
fallback_provider = "vosk" # after N consecutive failures; "" disables
fallback_after = 3
streaming = true           # text appears while you speak (segments are sent at pauses)
stream_interim_seconds = 1.5   # refresh the sentence in progress; 0 = only at pauses
stream_pause_seconds = 0.6
stream_max_segment_seconds = 12.0

[stt.vosk]
model_path = "/usr/share/vosk/vosk-model-fa-0.42"

[stt.whisper]
model = "small"
device = "cpu"

[stt.google_cloud]
keyfile = ""

[audio]
device = ""
blocksize = 1600
debug_save_audio = false
pause_media = false        # pause playing media (playerctl) while recording

[overlay]
enabled = true
position = "top"
anchor_horizontal = "center"
offset_x = 0
offset_y = 12
theme = "dark"             # palette name (tokyo-night, nord, …), legacy dark/light, or a .css path
opacity = 0.86
width = 360
height = 96
show_partial_text = true
show_audio_meter = true
font = "monospace, DejaVu Sans Mono, Liberation Mono, Noto Sans Mono"
style = "pill"             # pill | waveform | vu_meter
animations = true          # master switch for the animated transitions
show_transcript = true     # live word-by-word transcript above the pill
transcript_words = 4       # how many words the live preview keeps
accent = ""                # hex accent override, e.g. "#7aa2f7"; "" = palette default
fade_ms = 140              # show/hide fade duration in milliseconds
sounds = false             # audible start/stop/error cues

[injector]
backend = "auto"           # auto | paste | wtype | ydotool | xdotool
typing_delay_ms = 0
strip_whitespace = false
paste_shortcut = "auto"
restore_clipboard = true

[network]
proxy = ""                 # http://host:port | socks5h://user:pass@host:1080  ("" = use HTTP(S)_PROXY env vars)
no_proxy = "localhost,127.0.0.1,::1"

[postprocess]
capitalize_first = true
auto_punctuate = true
normalize_zwnj = true
digits = "keep"            # keep | latin | persian
persian_punctuation = true
english_terms = true
filter_hallucinations = true
voice_commands = false

[postprocess.custom_replacements]
# "می شود" = "می‌شود"

[translate]
enabled = false          # translate the transcript into `language` before injecting it
language = "en"          # target language: a code ("en") or a name ("English")
source = "auto"          # "auto" detects the language you spoke
timeout_seconds = 15.0   # give up on a translation after this many seconds
```

## Speech providers

| Provider | Requires | Offline | Partial results | Notes |
|---|---|:-:|:-:|---|
| `google_free` *(default)* | internet | no | segmented | Undocumented endpoint. Audio is sent to Google. |
| `vosk` | `vosk` and a model | yes | native | Light and private, but less accurate on mixed Persian and English text. |
| `whisper` | `faster-whisper` | yes | segmented | Most accurate offline engine. Runs on CPU or CUDA. |
| `google_cloud` | a service-account key | no | segmented | Paid. Requires `[stt.google_cloud] keyfile`. |

Set the provider with `[stt] provider`. The daemon logs a privacy warning at start-up when `google_free` is active, because that provider uploads your microphone audio to Google.

To use Vosk offline, install the engine and download a model:

```bash
./scripts/install-user.sh vosk
voicetype models install fa-small          # ~53 MB
```

`voicetype models install fa` downloads the larger model (~1.6 GB). The command prints the path to put in `[stt.vosk] model_path`. The archive is extracted with path traversal protection, and `--sha256` verifies the download against an expected checksum.

### Live text while you speak

Vosk streams natively. For the other providers VoiceType uses segmented streaming: the audio is cut at pauses, and each sentence is sent while you are still talking. The sentence in progress is refreshed every `stream_interim_seconds`, so text appears in the overlay as you speak and only the last short segment remains when you stop. The final text therefore lands almost immediately.

```toml
[stt]
streaming = true                 # false = one request after you stop
stream_interim_seconds = 1.5     # refresh the sentence in progress; 0 = only at pauses
stream_pause_seconds = 0.6       # the silence that ends a sentence
```

The trade-off is more requests to the provider. Set `stream_interim_seconds = 0` to send text only at pauses, or `streaming = false` to fall back to a single request. Segments that fail are retried at the end, and a recording in which no speech was detected is transcribed as a whole, so streaming never loses audio.

To compare the providers on your own voice, record the 50-sentence corpus and run the benchmark. See [docs/benchmarks.md](docs/benchmarks.md).

## Translation

With translation enabled, VoiceType speaks Persian and types English, or any other supported language. It is off by default.

```bash
./scripts/install-user.sh translate
```

```toml
[translate]
enabled = true
language = "en"      # "en", "English", "de", "zh-CN", …
source = "auto"      # "auto" detects the language you spoke
```

```bash
voicetype translate "سلام دنیا"    # → Hello world.
```

The Persian clean-up runs on your words first, then the translation rewrites them in the target language, and the result is typed at the cursor. If Google cannot be reached, the original text is injected unchanged and the overlay shows a warning. A failed translation never discards your words.

`googletrans` is installed by the installer regardless of whether translation is enabled, so that turning translation on later needs no further installation and `voicetype config check` can validate the configured language. See [docs/translation.md](docs/translation.md).

## Behind a firewall

To route all VoiceType traffic (speech requests, model downloads) through an HTTP or SOCKS proxy:

```toml
[network]
proxy = "socks5h://127.0.0.1:1080"     # or  http://127.0.0.1:8080  ·  user:pass@ is supported
```

```bash
voicetype reload && voicetype net-test      # prints OK or FAIL with the reason
```

SOCKS proxies need PySocks, which the `socks` extra installs. Use `socks5h` when DNS resolution must happen on the proxy, which is the usual case behind a restrictive firewall. If the configured proxy cannot be used, VoiceType fails the connection instead of falling back to a direct one, so your audio is never sent outside the proxy you chose. See [docs/network.md](docs/network.md).

## Privacy and security

- The `google_free` provider uploads your microphone audio to Google. Use `vosk` or `whisper` for fully offline operation. See [docs/privacy.md](docs/privacy.md).
- With translation enabled, the text of your transcript is sent to Google as well.
- Audio is not stored unless you set `[audio] debug_save_audio = true`, which writes the last recording to `~/.local/state/voicetype/last.wav`. Transcripts are never written to the log.
- The control socket at `$XDG_RUNTIME_DIR/voicetype.sock` is created with mode `0600`, so only your own user can talk to the daemon.
- Transcribed text is never passed to a shell. Control characters are stripped before injection, so a transcript cannot act as an input command.
- The previous clipboard contents are restored after a paste, so nothing is left behind.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check . # lint
pytest                # 733 tests; no microphone, network, or GUI required
```

The core (configuration, dispatcher, speech backends, post-processing, injector logic) runs on any operating system. GTK, `evdev`, and PortAudio are imported lazily, so the test suite needs none of them.

The tests that use a Unix socket are skipped on Windows; run `pytest` on Linux for the full set. CI runs lint and tests on Python 3.11, 3.12, and 3.13, and builds the Arch package in a container (see [.github/workflows/ci.yml](.github/workflows/ci.yml)).

### Adding a speech provider

Register a backend with the provider registry; the dispatcher picks it up without any further changes.

```python
from voicetype.stt import registry

registry.register("mine", lambda opts: MyBackend(opts))   # then: provider = "mine"
```

### Project layout

```
voicetype/        daemon, dispatcher, audio, stt/, postprocess/, translate/, injector, overlay, control, cli
tests/            unit and integration tests (fixed WAV fixture, 50-sentence corpus)
scripts/          install-user.sh (the installer), install-hotkey.sh, bench.py, record_corpus.py, release.sh
packaging/        PKGBUILD, systemd user unit, desktop files, AUR package
spikes/           hardware experiments (injection matrix, OSD, hotkey, STT comparison)
docs/             architecture, ADRs, privacy, compatibility, troubleshooting, benchmarks
```

## Troubleshooting

Start with `voicetype doctor`. Every failing check comes with the command that fixes it.

| Symptom | Fix |
|---|---|
| `daemon is not running` | `systemctl --user start voicetype`; the log is in `journalctl --user -u voicetype -f` |
| The shortcut does nothing | Check whether `voicetype toggle` works in a terminal. On Plasma, log out and back in once after the first installation, then re-run `scripts/install-hotkey.sh` if needed. |
| The text is not typed | `systemctl --user enable --now ydotool`; check the *text injection backend* line in `doctor`. |
| The wrong paste in terminals | Install `kdotool`, or set `[injector] paste_shortcut = "ctrl+shift+v"`. |
| No overlay | Install `gtk4-layer-shell` and `python-gobject`. GNOME does not implement layer-shell, so the daemon runs without an overlay there. |
| Network errors | Set `[network] proxy` and run `voicetype net-test`. See [docs/network.md](docs/network.md). |
| It stops too early or too late | Adjust `[stt] silence_threshold_seconds`. |
| Persian text looks wrong | Check `[postprocess] normalize_zwnj`, `persian_punctuation`, and `digits`. See [docs/postprocess.md](docs/postprocess.md). |

More: [docs/troubleshooting.md](docs/troubleshooting.md) · [docs/known-issues.md](docs/known-issues.md) · [docs/compatibility.md](docs/compatibility.md)

## Documentation

[Architecture](docs/VoiceType-Architecture.md) (read the errata first) · [Decisions (ADRs)](docs/adr/) · [Theming](docs/theming.md) · [Post-processing](docs/postprocess.md) · [Translation](docs/translation.md) · [Network and proxy](docs/network.md) · [Privacy](docs/privacy.md) · [Injection matrix](docs/inject-matrix.md) · [Benchmarks](docs/benchmarks.md) · [Compatibility](docs/compatibility.md)

## Acknowledgements

The adaptive silence detection, the control socket, paste-based injection, and the layer-shell overlay follow the design of [hyprwhspr](https://github.com/goodroot/hyprwhspr). Offline recognition uses [Vosk](https://github.com/alphacep/vosk-api) and [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## License

[MIT](LICENSE)
