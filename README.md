<div align="center">

# VoiceType

Voice typing for Linux that understands Persian.<br>
Press a shortcut, talk, and the text shows up wherever your cursor is.

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Linux%20(Wayland%20%2F%20X11)-1793D1?logo=linux&logoColor=white)
![Tests](https://img.shields.io/badge/tests-800%2B-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)

[Install](#install) · [Using it](#using-it) · [Translation](#translation) · [Configuration](#configuration) · [Privacy](#privacy) · [Hacking on it](#hacking-on-it) · [فارسی](README.fa.md)

</div>

---

VoiceType is a small daemon. You press a key, say something, press the key again, and the transcript is pasted into whatever has focus: a browser, a terminal, your editor, Telegram, LibreOffice. It was built for Persian first, English works too, and mixing the two in one sentence is fine.

## Why we built it

Typing all day on Linux wears you out, and it got worse once we started working with coding agents, where half of the job is explaining things in long prompts. We wanted to hold a thought, say it out loud in Persian, and have the machine get it right.

When we talk about code, English words end up in the middle of Persian sentences. So VoiceType turns «پایتون» into `Python` and «گیت‌هاب» into `GitHub` on its own. And when the prompt needs to be in English anyway, one line in the config makes it translate what you said before it types.

The two of us wrote it together.

## What you get

- Persian text that looks right. Half-spaces (ZWNJ) land where they belong, punctuation is Persian, and you pick which digits you want.
- English terms spelled the way developers spell them. The list has 44 terms today and you can add your own.
- Optional translation into English or another language, off by default. See [Translation](#translation).
- Typing into any app. The text goes in by clipboard paste, and your old clipboard comes back afterwards.
- A small floating card that shows what it is hearing. It never takes focus and clicks pass through it.
- A choice of speech engines, online or fully offline, with an automatic fallback when one keeps failing.
- It stops by itself when you go quiet, and it adapts to how noisy your room is.
- `voicetype doctor`, which tells you what is missing and the exact command to fix it.

## How it works

```
 Super+Alt+V ──► voicetype-toggle ──► daemon (Unix socket)
                                         │
       ┌────────────────────────────────┼─────────────────────────┐
       ▼                                ▼                         ▼
  AudioCapture                     Dispatcher                  Overlay
 16 kHz mono mic          IDLE → LISTENING → TRANSCRIBING      (GTK4 layer-shell)
       │                       → TRANSLATING → INJECTING → IDLE
       ▼                                │
 Silence detector                       ▼
       │              STT provider ─► Post-processing ─► Translation ─► Text injector
       └─────────────► (Google/Vosk/       (ZWNJ, digits,     (optional)    (paste into the
                        Whisper)            English terms)                   focused app)
```

The shortcut only sends `toggle` over a socket in `$XDG_RUNTIME_DIR`. The daemon does the rest: it records, sends audio to the engine you picked, cleans up the transcript, optionally translates it, and pastes it.

## Install

```bash
git clone https://github.com/isalehgholami/voicetype.git
cd voicetype
chmod +x scripts/*.sh              # archives sometimes drop the executable bit

./scripts/install-user.sh --system-deps
voicetype doctor                   # every required line should say [✓]
```

The installer finds your distribution (Arch, Debian and Ubuntu, Fedora, openSUSE, Alpine, Void), installs what is missing, sets up VoiceType in a `pipx` environment, starts the daemon, and registers the `Super+Alt+V` shortcut on KDE Plasma, GNOME, Hyprland, Sway or i3. Without `--system-deps` it never uses `sudo`; it prints the command for you to run instead.

Log out and back in once after the first run so the `input` and `audio` groups apply. To update later, run `git pull && ./scripts/install-user.sh`.

Arch with KDE Plasma 6 on Wayland is what we develop and test on. The other setups are covered by the installer, but they have had less time on real machines, so tell us if something breaks.

Manual install, the Arch package, installer options and uninstalling are in [docs/installation.md](docs/installation.md).

## Using it

Click into any text field, press `Super+Alt+V`, speak, and press it again. Or just stop talking: after 3 seconds of silence it finishes on its own.

| To do this | Run this |
|---|---|
| Start or stop dictating | `Super+Alt+V` or `voicetype toggle` |
| Throw the recording away | `voicetype cancel` |
| See what the daemon is doing | `voicetype status` |
| Check your setup | `voicetype doctor` |
| Reload the config | `voicetype reload` |
| Test the proxy | `voicetype net-test` |

`voicetype --help` lists every command.

If you want different settings for different jobs, drop a partial config into `~/.config/voicetype/profiles/<name>.toml` and switch with `voicetype profile <name>`. For example, a coding profile that turns off sentence punctuation:

```toml
# ~/.config/voicetype/profiles/code.toml
[postprocess]
auto_punctuate = false
capitalize_first = false
```

## Speech engines

| Engine | Needs | Offline | Notes |
|---|---|:-:|---|
| `google_free` (default) | internet | no | Undocumented endpoint, and your audio goes to Google. |
| `vosk` | `vosk` and a model | yes | Light and private. Less accurate with mixed Persian and English. |
| `whisper` | `faster-whisper` | yes | The most accurate offline option. Runs on CPU or CUDA. |
| `google_cloud` | a service-account key | no | Paid. |

Pick one with `provider` under `[stt]`. For offline use:

```bash
./scripts/install-user.sh vosk
voicetype models install fa-small          # about 53 MB
```

While you talk, the text appears on the overlay. Vosk streams natively. For the other engines we cut your audio at pauses and send each sentence while you are still speaking, so when you stop only the last short piece is left to transcribe. The price is more requests to the engine. If the free endpoint starts rate-limiting you, set `stream_interim_seconds = 0`, or `streaming = false` to send everything at the end.

To compare engines on your own voice, record the 50-sentence corpus and run the benchmark in [docs/benchmarks.md](docs/benchmarks.md).

## Translation

Say it in Persian, get it typed in English. It is off by default.

```bash
./scripts/install-user.sh translate
```

```toml
[translate]
enabled = true
language = "en"      # "en", "English", "de", "zh-CN", ...
```

```bash
voicetype translate "سلام دنیا"    # Hello world.
```

The Persian clean-up runs first, then the translation, and the result is typed at your cursor. If no service answers, the original text is typed instead and the overlay shows a warning, so a failed translation never costs you your words.

Translation uses the free web endpoints of Google, Bing, Yandex and Alibaba, tried one after another until one answers. There is no API key and no account. Those endpoints are unofficial and sometimes rate-limit or block you, which is why there are four of them in the chain. More in [docs/translation.md](docs/translation.md).

## Configuration

Everything lives in `~/.config/voicetype/config.toml`, and every key is optional. A short taste:

```toml
[stt]
provider = "google_free"      # google_free | vosk | whisper | google_cloud
language = "fa-IR"
silence_threshold_seconds = 3.0

[overlay]
position = "bottom"
theme = "nocturne"            # a built-in palette, or the path to your own .css

[postprocess]
digits = "keep"               # keep | latin | persian

[postprocess.custom_replacements]
"می شود" = "می‌شود"
"re:\\bqt\\d\\b" = "Qt"       # keys starting with re: are regular expressions
```

```bash
voicetype config init     # write the full, commented file with all defaults
voicetype config check    # validate your edits
voicetype reload          # apply them without restarting
```

The complete list of keys, with defaults and ranges, is in [docs/configuration.md](docs/configuration.md). Themes are covered in [docs/theming.md](docs/theming.md), and the Persian text rules in [docs/postprocess.md](docs/postprocess.md).

Behind a firewall? Set `[network] proxy = "socks5h://127.0.0.1:1080"` (HTTP proxies work too), then run `voicetype net-test`. If the proxy fails, VoiceType refuses to connect directly, so your audio never leaves outside the route you chose. Details in [docs/network.md](docs/network.md).

## Privacy

- The default engine, `google_free`, uploads your microphone audio to Google, and the daemon says so in its log at start-up. For a fully offline setup use `vosk` or `whisper`.
- With translation on, the text of what you said goes to whichever service answers.
- Audio is not saved unless you turn on `debug_save_audio`. Transcripts never go into the log.
- The control socket is mode `0600`, transcripts are never handed to a shell, and control characters are stripped before pasting.

More in [docs/privacy.md](docs/privacy.md).

## Things to know

- Streaming with Google is simulated. We cut at pauses because the free endpoint has no real streaming, so a word in the live preview can differ from the final text.
- The ZWNJ rules are heuristics. When one gets a word wrong, add a line to `custom_replacements`.
- Klipper and other clipboard managers may keep what you dictated in their history.
- Hold-to-talk needs the `input` group and the `evdev` extra. The default press-to-toggle needs neither.
- On KDE the installer writes the shortcut straight into `kglobalshortcutsrc`, and that format can change between Plasma releases. If it ever fails, add `voicetype-toggle` by hand under System Settings, Shortcuts.
- Recording goes through `pw-cat` on PipeWire desktops. PortAudio holds the raw sound card exclusively and can make the microphone vanish from the whole desktop. If that ever happened to you, [docs/configuration.md](docs/configuration.md#how-the-microphone-is-captured) has the fix.

The full list is in [docs/known-issues.md](docs/known-issues.md).

## When something goes wrong

Start with `voicetype doctor`. Every failing line comes with the command that fixes it.

| Symptom | Try this |
|---|---|
| `daemon is not running` | `systemctl --user start voicetype`, then read `journalctl --user -u voicetype -f` |
| The shortcut does nothing | Check that `voicetype toggle` works in a terminal. On Plasma, log out and in once, then run `scripts/install-user.sh --hotkey-only`. |
| Nothing gets typed | `systemctl --user enable --now ydotool`, then look at the text injection line in `doctor` |
| Paste is wrong in a terminal | Install `kdotool`, or set `[injector] paste_shortcut = "ctrl+shift+v"` |
| No overlay | Install `gtk4-layer-shell` and `python-gobject`. GNOME has no layer-shell, so it runs without the overlay there. |
| It stops too early or too late | Change `silence_threshold_seconds` under `[stt]` |

More in [docs/troubleshooting.md](docs/troubleshooting.md).

## Hacking on it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check .
pytest              # no microphone, network or display needed
```

The core (config, dispatcher, engines, post-processing, injector logic) runs on any OS, and GTK, `evdev` and PortAudio are imported lazily. Tests that use a Unix socket are skipped on Windows, so run the suite on Linux for the full set. CI runs lint and tests on Python 3.11 to 3.13 and builds the Arch package in a container.

Adding a speech engine does not touch the dispatcher:

```python
from voicetype.stt import registry

registry.register("mine", lambda opts: MyBackend(opts))   # then: provider = "mine"
```

```
voicetype/    daemon, dispatcher, audio, stt/, postprocess/, translate/, injector, overlay, control, cli
tests/        unit and integration tests, a fixed WAV and a 50-sentence corpus
scripts/      install-user.sh (installer and shortcut), bench.py, record_corpus.py, release.sh
packaging/    PKGBUILD, systemd user unit, desktop files, AUR package
spikes/       throwaway hardware experiments (injection matrix, overlay, hotkey, engine comparison)
docs/         architecture, ADRs, privacy, compatibility, troubleshooting, benchmarks
```

The architecture doc is the place to start if you want to understand the design. Read its errata first, since a few early assumptions turned out wrong. The decisions we made along the way are in [docs/adr](docs/adr/).

## Thanks

The adaptive silence detection, the control socket, paste-based injection and the layer-shell overlay follow the design of [hyprwhspr](https://github.com/goodroot/hyprwhspr). Offline recognition comes from [Vosk](https://github.com/alphacep/vosk-api) and [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## License

[MIT](LICENSE)
