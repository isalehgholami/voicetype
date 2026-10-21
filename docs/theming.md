# Theming

The overlay is painted with a **palette** — a fixed set of colours (background, border, text, accent, one colour per state). Pick a built-in palette by name, or point `overlay.theme` at your own CSS file. The palette paints the card itself: its body, its hairline border, its texts, the status dot and the waveform.

```toml
[overlay]
theme = "nocturne"         # a palette name, or the path to a .css file
```

## Built-in palettes

| Name | Looks like |
|---|---|
| `nocturne` | **Default.** Near-black violet (#191720), soft white text, a violet accent and a mint "live" colour. The palette the floating HUD was designed for. |
| `tokyo-night` | Deep navy-black, cool blue text, cornflower-blue accent. The hyprwhspr look. |
| `tokyo-night-light` | The same palette inverted — near-white background, dark blue text, a brighter blue accent. |
| `catppuccin-mocha` | Warm mauve-black with pastel accents: pink while recording, amber when paused, green while processing. |
| `rose-pine` | Muted blue-grey with a dusty teal accent and a lavender secondary accent. Low contrast, calm. |
| `nord` | Cold, blue-grey background with frost-blue and ice-blue accents; red recording, green processing. |
| `gruvbox-dark` | Warm retro brown-olive background with an olive-green accent and a sky-blue secondary accent. |
| `dracula` | Purple-tinted black with a vivid violet accent, cyan secondary, hot-pink recording. |

Names are case-insensitive. An unknown name is rejected by the config loader with a clear error; the palette resolver itself falls back to `nocturne`, so a typo never takes the overlay down.

## Legacy aliases

The two values that existed before the palette system still work:

| Old value | Resolves to |
|---|---|
| `dark` | `tokyo-night` |
| `light` | `tokyo-night-light` |

Existing `config.toml` files keep working unchanged.

## Custom themes

A theme is a CSS file that uses GTK's `@define-color` syntax. VoiceType reads the declarations from the file, ignores every key it does not know, and applies what it recognises on top of the default palette — so you only have to list the colours you want to change.

```css
@define-color background-color #12141c;
@define-color accent-color     #ffb86c;
```

**Two rules matter.** The value must start with `#`, and every declaration must end with `;`. A declaration that is missing either is silently skipped.

```css
@define-color accent-color ff8800;      /* skipped: no leading '#' */
@define-color border-color #2a2e42      /* skipped: no trailing ';' */
```

Colours may be written as `#rgb`, `#rgba`, `#rrggbb` or `#rrggbbaa` (the alpha byte is used as-is).

### Supported keys

These are the only keys VoiceType reads. Everything else in the file is ignored, so a desktop theme file can be reused verbatim.

| CSS key | Controls |
|---|---|
| `background-color` | The card's body. |
| `border-color` | The card's hairline border. |
| `text-color` | The card's main text — the transcript and the messages. |
| `text-dim-color` | The dimmed text: the source line under a translation, and the language chip. |
| `accent-color` | The waveform's bars — the value `overlay.accent` overrides. |
| `accent-alt-color` | The palette's second accent — read, but nothing is painted with it yet. |
| `recording-color` | The live state's colour: the dot and the label while listening. |
| `paused-color` | The notice state's colour. |
| `processing-color` | The working states' colour: transcribing and translating. |
| `error-color` | The error state's colour. |
| `success-color` | The finished state's colour. |
| `bar-color-left` | Left-hand end of the bar gradient. |
| `bar-color-right` | Right-hand end of the bar gradient. |
| `shadow-color` | The card's drop shadow. |

Two of the fourteen slots are carried by the palette but not painted on their own:
`accent-alt-color` and — unless the card is drawn with the flat bar gradient — the two
`bar-color-*` slots. The waveform of the HUD is painted with the palette's accent.
All the slots are still accepted, so a theme file can name them without an error.

### Where the palette is actually visible

The card is painted from the palette:

| Part of the card | Painted from the palette |
|---|---|
| the body | `background-color` (its alpha is scaled by `overlay.opacity`) |
| the hairline border | `border-color` |
| the status dot and the status label | `recording-color` while listening, `processing-color` while working, `success-color` when the result is there, `error-color` on a failure |
| the main text | `text-color` |
| the source line and the language chip | `text-dim-color` |
| the waveform | `accent-color` |

The state colours are the only place the five state colours appear, and they appear on
the small dot next to the status label — never as a glow, never as a background.

### Where to put the file

Anywhere you like — VoiceType only needs a path. A common choice:

```
~/.config/voicetype/theme.css
```

Point `overlay.theme` at it with an **absolute path**; a relative path is resolved against the daemon's working directory, which is not predictable. `~` is expanded for you:

```toml
[overlay]
theme = "~/.config/voicetype/theme.css"
```

The file must exist and must end in `.css` — `voicetype config check` says so plainly:

```text
config: 'overlay.theme' theme file not found: /home/you/.config/voicetype/theme.css
```

A file that exists but contains no usable declaration is not an error: the overlay simply falls back to `nocturne`.

### Reloading

`ThemeWatcher` polls the theme file's modification time once a second (1 Hz) and fires a callback when the file changes. A poll is used instead of an inotify watch on purpose: theme engines commonly replace the file with an atomic rename, and a file-watch event does not survive that.

The watcher is only installed when `overlay.theme` points at an existing `.css` file — a palette name cannot change under your hands, so there is nothing to watch. When the file changes, the overlay re-reads it, re-applies the `overlay.accent` override and the `overlay.opacity` factor, pushes the new palette into the visualization and repaints. There is no need to restart the daemon.

Changing `overlay.theme` *itself* — or any other `[overlay]` key — in `config.toml` is picked up by `voicetype reload`: the overlay is rebuilt with the new settings and the old window is torn down. The overlay is only rebuilt when its own settings changed, so a reload that touches other keys leaves it alone.

## Fonts

The overlay draws every text — the status labels, the error and success messages and the live transcript — with **one** font. By default that is the **system's own font**: the family the desktop settings ask for, and their size clamped into the band the card was designed for — the main text between 14 and 16 pixels, the status label around a third smaller.

```toml
[overlay]
font = ""          # empty = the system's own font
font_size = 0.0    # 0 = the system's own size, clamped
```

Both keys are independent: you can pick a family and keep the system's size, or set a size and keep the system's family.

```toml
[overlay]
font = "JetBrains Mono"   # a family, or a comma-separated list of fallbacks
font_size = 16.0          # in points
```

A family list is handed to Pango as it is, so `"monospace, DejaVu Sans Mono, Liberation Mono"` picks the first face that is installed. A family that is not installed falls back to the system's font.

The size is the point size of the **main text**. The status label is drawn at 72 % of it and the source line under a translation at 88 %, so the hierarchy holds whatever the size is. The card has a fixed size, so a large font does not grow it: the text rolls through the card's box instead, and the box shows fewer lines of a bigger font.

Font changes are applied by `voicetype reload`, like every other `[overlay]` key.

## The card's size

The card is **fixed**: `overlay.width` and `overlay.height` are its width and its height, and they do not depend on what the card holds.

```toml
[overlay]
width = 360     # the card's width, in pixels
height = 128    # the card's height, in pixels
```

The text lives in a fixed box inside the card. Text that does not fit scrolls through that box, and a text that is replaced rolls out of it while its successor rolls in — so the overlay never resizes while you talk. Make the card taller if you want more of a long sentence on screen; make it narrower for a smaller card.

## Accent override

To change one colour without writing a theme file, use `overlay.accent`:

```toml
[overlay]
accent = "#ff8800"        # #rgb, #rgba, #rrggbb or #rrggbbaa
```

This replaces the palette's `accent` only — `bar-color-left`, `bar-color-right` and `accent-alt` keep their palette values. An empty value keeps the palette accent. A value the config loader cannot parse is rejected by `voicetype config check`; if one slips through, the overlay ignores it with a warning in the log.

The accent is the colour of the waveform's bars, so an override changes what is on the screen right away.

## Example

A complete, copy-pasteable theme file — this is the built-in `nocturne` palette spelled out as CSS:

```css
/* ~/.config/voicetype/theme.css
 *
 * VoiceType overlay theme.
 * Only the keys listed below are read; anything else is ignored.
 * Every value must start with '#' and every line must end with ';'.
 */

@define-color background-color #191720f7;  /* the card's body */
@define-color border-color     #ffffff12;  /* the card's hairline */
@define-color text-color       #f3f0f9;   /* the main text */
@define-color text-dim-color   #a9a3b5;   /* the source line and the chip */
@define-color accent-color     #a697ff;   /* the waveform */
@define-color accent-alt-color #7c6be0;   /* second accent (not painted yet) */
@define-color recording-color  #a5e88c;   /* listening */
@define-color paused-color     #e0b87f;   /* notices */
@define-color processing-color #a697ff;   /* transcribing and translating */
@define-color error-color      #e58c8c;   /* failures */
@define-color success-color    #a5e88c;   /* the result is there */
@define-color bar-color-left   #a697ff;   /* gradient, left */
@define-color bar-color-right  #a697ff;   /* gradient, right */
@define-color shadow-color     #00000057;  /* the card's shadow */
```

With it in place:

```toml
[overlay]
theme = "~/.config/voicetype/theme.css"
accent = "#ffb86c"
```
