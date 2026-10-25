# Configuration

The configuration file is `~/.config/voicetype/config.toml`. Set `VOICETYPE_CONFIG` to use a different path.

Every key is optional; anything you leave out keeps its default. The loader validates types, enumerations, and ranges, and rejects unknown keys with an error that names the offending path.

```bash
voicetype config init     # write a fully commented file with the defaults
voicetype config path     # print the path the daemon uses
voicetype config check    # validate the current file
voicetype reload          # apply the changes
```

If the file becomes invalid, `voicetype config init --repair` copies it to `config.toml.bak-<timestamp>` and writes the defaults again. The installer performs the same repair automatically, so a broken file never blocks an update.

## Applying changes

`voicetype reload` re-reads the file and rebuilds everything the configuration touches: the audio capture, the speech backend, the injector, the post-processing pipeline, the translator, the log level, the overlay, and the hold-mode hotkey listener. The overlay is only torn down when its own settings changed, and it is replaced together with the other components, so the daemon never shows two overlays at once. If a recording is in progress, the new configuration is applied once the current cycle ends.

| Change | Takes effect |
|---|---|
| Every key | `voicetype reload` |
| `[overlay] theme` pointing at a `.css` file | immediately; the file is watched and the overlay repaints on change |

The desktop shortcut is registered by the installer, not by the daemon, so changing the key combination means re-running `scripts/install-user.sh --hotkey-only --hotkey <combo>`.

## General

| Key | Default | Description |
|---|---|---|
| `log_level` | `"INFO"` | Verbosity of the daemon log: `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `notify_errors` | `true` | Send a desktop notification through `notify-send` when an error occurs. |

## `[hotkey]`

| Key | Default | Description |
|---|---|---|
| `combo` | `"super+alt+v"` | The key combination read by the `evdev` listener in hold mode. The desktop shortcut in `press` mode is registered by the installer and is not affected by this key. |
| `toggle_mode` | `"press"` | `press` toggles on each press and uses the desktop shortcut. `hold` records while the key is held and requires the `evdev` extra, the `input` group, and write access to `/dev/uinput`. |
| `escape_stops` | `true` | Whether the keys in `extra_stops` cancel the recording in hold mode. |
| `extra_stops` | `["esc"]` | Keys that cancel the recording in hold mode. |

## `[stt]`

| Key | Default | Description |
|---|---|---|
| `provider` | `"google_free"` | The speech engine: `google_free`, `vosk`, `whisper`, or `google_cloud`. See [Speech providers](../README.md#speech-providers). |
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

## `[audio]`

| Key | Default | Description |
|---|---|---|
| `device` | `""` | The microphone. An empty string uses the system default; a number selects it by index, a string by name substring. |
| `blocksize` | `1600` | Samples per callback. 1600 samples at 16 kHz is 100 ms. Range 160 to 16000. |
| `max_buffer_seconds` | `120.0` | Maximum length of a single recording. Older audio is dropped beyond this limit. Range 5 to 3600. |
| `debug_save_audio` | `false` | Write the captured audio to disk for debugging. |
| `pause_media` | `false` | Pause the running media player through `playerctl` while recording. |

## `[overlay]`

| Key | Default | Description |
|---|---|---|
| `enabled` | `true` | Whether the overlay is shown at all. The daemon works without it. |
| `position` | `"bottom"` | Vertical position: `top`, `center`, or `bottom`. |
| `anchor_horizontal` | `"center"` | Horizontal anchor: `left`, `center`, or `right`. |
| `offset_x` | `0` | Horizontal offset in pixels. Range −1920 to 1920. |
| `offset_y` | `50` | Vertical offset in pixels. Range −1080 to 1080. |
| `theme` | `"nocturne"` | A palette name (`nocturne`, `tokyo-night`, `nord`, `dracula`, `catppuccin-mocha`, `rose-pine`, `gruvbox-dark`, `tokyo-night-light`), the legacy values `dark` or `light`, or the path to a `.css` file. See [theming](theming.md). |
| `accent` | `""` | Hex colour that overrides the accent of the palette, for example `"#ff8800"`. Empty means the palette decides. |
| `opacity` | `0.86` | Opacity of the card the overlay draws on. Range 0.0 to 1.0. |
| `width` | `360` | The fixed width of the card, in pixels. The card never resizes with the text it holds. Range 120 to 1920. |
| `height` | `128` | The fixed height of the card, in pixels. Range 48 to 480. |
| `style` | `"hud"` | Deprecated and ignored: the card has one shape now. |
| `animations` | `true` | Master switch for motion. `false` makes every state change instant. |
| `show_audio_meter` | `true` | Whether the waveform reacts to the microphone level. |
| `show_transcript` | `true` | Whether the live word-by-word preview is shown on the card. |
| `show_partial_text` | `true` | Whether partial results are rendered at all. Both this and `show_transcript` must be enabled for the preview. |
| `transcript_words` | `8` | How many words the live preview keeps. Range 1 to 12. |
| `font` | `""` | The font family every text of the overlay is drawn with. Empty — the default — means the system font, the family the desktop settings ask for. A value such as `"JetBrains Mono"` picks a face instead. |
| `font_size` | `0.0` | The point size of the main text. Zero — the default — means the system's own font size, clamped into the band the card was designed for; a value sets the size in points. The status labels are drawn at 72 % of it. |
| `fade_ms` | `140` | Duration of the show and hide transition in milliseconds. Range 0 to 1000. |
| `sounds` | `false` | Play an audible cue when recording starts, stops, or fails. |

## `[injector]`

| Key | Default | Description |
|---|---|---|
| `backend` | `"auto"` | How the text reaches the application: `auto`, `paste`, `wtype`, `ydotool`, or `xdotool`. `auto` picks the best backend the session and the installed tools allow. |
| `typing_delay_ms` | `0` | Delay between keystrokes when typing directly. Range 0 to 50. |
| `strip_whitespace` | `false` | Strip leading and trailing whitespace before injecting. |
| `paste_shortcut` | `"auto"` | The shortcut used to paste: `auto`, `ctrl+v`, or `ctrl+shift+v`. `auto` looks at the class of the focused window and uses `ctrl+shift+v` for a known terminal, `ctrl+v` otherwise. The lookup needs `kdotool` on Wayland and `xdotool` on X11; without it, `ctrl+v` is always used. |
| `restore_clipboard` | `true` | Restore the previous clipboard contents after the paste. |
| `release_modifiers` | `true` | Release every modifier key before typing, so a shortcut that is still held cannot turn the paste into a combination. |

## `[postprocess]`

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

## `[network]`

| Key | Default | Description |
|---|---|---|
| `proxy` | `""` | The proxy for all outgoing traffic: `http://host:port` or `socks5h://user:pass@host:1080`. An empty string falls back to the `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` environment variables. |
| `no_proxy` | `"localhost,127.0.0.1,::1"` | Hosts that bypass the proxy. |

## `[translate]`

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Translate the transcript before injecting it. |
| `language` | `"en"` | The target language, either a code such as `en` or `zh-CN` or a name such as `English`. |
| `source` | `"auto"` | The source language. `auto` detects what you spoke. |
| `timeout_seconds` | `15.0` | Give up on a translation after this many seconds. Range 1 to 120. |
| `backend` | `"auto"` | `services` walks Google, Bing, Yandex and Alibaba until one answers; `googletrans` uses py-googletrans; `auto` takes the fallback chain when it is installed. |

## The complete file

`voicetype config init` writes the following, with every key and its default value.

```toml
# VoiceType configuration — every key is optional; these are the defaults.
log_level = "INFO"
notify_errors = true       # desktop notification on errors (notify-send)

[hotkey]
combo = "super+alt+v"      # used by install-user.sh and the optional evdev listener
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
backend = "auto"          # auto | pipewire | portaudio
blocksize = 1600
debug_save_audio = false
pause_media = false        # pause playing media (playerctl) while recording

[overlay]
enabled = true
position = "bottom"
anchor_horizontal = "center"
offset_x = 0
offset_y = 50
theme = "nocturne"         # palette name (nocturne, tokyo-night, nord, …), legacy dark/light, or a .css path
opacity = 0.86
width = 360                # the fixed width of the card
height = 128               # the fixed height of the card
show_partial_text = true
show_audio_meter = true
font = ""                   # empty = the system's own font; e.g. "JetBrains Mono"
font_size = 0.0             # 0 = the system's own size; otherwise in points
style = "hud"              # deprecated: the card has one shape now
animations = true          # master switch for the animated transitions
show_transcript = true     # live word-by-word transcript on the card
transcript_words = 8       # how many words the live preview keeps
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
backend = "auto"         # auto | services | googletrans
```

## How the microphone is captured

`[audio] backend` decides how VoiceType opens the microphone:

| Value | Behaviour |
|---|---|
| `auto` *(default)* | `pipewire` when the session has a PipeWire server, otherwise `portaudio` |
| `pipewire` | records through the audio server with `pw-cat` |
| `portaudio` | records with `sounddevice` (PortAudio) |

`pipewire` is the right choice on a PipeWire desktop, and it is the default there. PortAudio's ALSA backend opens the raw codec (`hw:0,0`) and holds it exclusively; PipeWire's own capture of the same device then fails with `EBUSY`, its input node dies, and **the microphone disappears from the whole desktop** — it only works inside VoiceType. Recording through `pw-cat` keeps the hardware with the audio server, so every other application can still record.

If the microphone is already missing from your system, bring the input device back after switching the backend:

```bash
wpctl status                                    # find the card number of "Built-in Audio"
wpctl set-profile 43 0 && wpctl set-profile 43 1   # 0 = off, 1 = back to duplex
```
