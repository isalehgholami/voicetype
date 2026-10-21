# Theming

The overlay is painted with a **palette** — a fixed set of colours (background, border, text, accent, one colour per state). Pick a built-in palette by name, or point `overlay.theme` at your own CSS file. How much of the palette actually reaches the screen depends on the visualization style; see [Where the palette is actually visible](#where-the-palette-is-actually-visible).

```toml
[overlay]
theme = "tokyo-night"      # a palette name, or the path to a .css file
```

## Built-in palettes

| Name | Looks like |
|---|---|
| `tokyo-night` | **Default.** Deep navy-black, cool blue text, cornflower-blue accent. The hyprwhspr look. |
| `tokyo-night-light` | The same palette inverted — near-white background, dark blue text, a brighter blue accent. |
| `catppuccin-mocha` | Warm mauve-black with pastel accents: pink while recording, amber when paused, green while processing. |
| `rose-pine` | Muted blue-grey with a dusty teal accent and a lavender secondary accent. Low contrast, calm. |
| `nord` | Cold, blue-grey background with frost-blue and ice-blue accents; red recording, green processing. |
| `gruvbox-dark` | Warm retro brown-olive background with an olive-green accent and a sky-blue secondary accent. |
| `dracula` | Purple-tinted black with a vivid violet accent, cyan secondary, hot-pink recording. |

Names are case-insensitive. An unknown name is rejected by the config loader with a clear error; the palette resolver itself falls back to `tokyo-night`, so a typo never takes the overlay down.

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
| `background-color` | The panel behind the visualization (waveform and VU meter). |
| `border-color` | The panel's border line. |
| `text-color` | The status label shown while listening. |
| `text-dim-color` | The palette's dimmed-text slot — read, but nothing is painted with it yet. |
| `accent-color` | The palette's accent — the value `overlay.accent` overrides. |
| `accent-alt-color` | The palette's second accent — read, but nothing is painted with it yet. |
| `recording-color` | The `recording` state's colour. |
| `paused-color` | The `paused` state's colour. |
| `processing-color` | The `processing` (transcribing) state's colour. |
| `error-color` | The `error` state's colour. |
| `success-color` | The `success` state's colour. |
| `bar-color-left` | Left-hand end of the bar/waveform gradient. |
| `bar-color-right` | Right-hand end of the bar/waveform gradient. |
| `shadow-color` | The palette's shadow colour — read, but the pill draws a fixed black shadow. |

Four of the fourteen slots are carried by the palette but not painted on their own:
`text-dim-color`, `accent-color`, `accent-alt-color` and `shadow-color`. The live
transcript is drawn in fixed white, the bar gradient runs from `bar-color-left` to
`bar-color-right`, and the pill's drop shadow is a fixed black. All four are still
accepted, so a theme file can name them without an error.

### Where the palette is actually visible

The default `pill` style paints **no palette colour at all** — its body, its
hairline border and its thirteen bars are fixed values — so while `style = "pill"`
a theme file changes nothing on screen. The other two styles do read the palette:

| Style | Painted from the palette |
|---|---|
| `pill` | nothing: the body is near-black, the border a fixed 7.5 % white, the bars white |
| `waveform` | `background-color`, `border-color`, the `bar-color-left` → `bar-color-right` gradient across the bars, and the pulsing state dot |
| `vu_meter` | `background-color`, `border-color`, the `bar-color-left` → `bar-color-right` gradient in the level bar |

The state dot is the only place the five state colours appear, and it is drawn by
the `waveform` style only.

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

A file that exists but contains no usable declaration is not an error: the overlay simply falls back to `tokyo-night`.

### Reloading

`ThemeWatcher` polls the theme file's modification time once a second (1 Hz) and fires a callback when the file changes. A poll is used instead of an inotify watch on purpose: theme engines commonly replace the file with an atomic rename, and a file-watch event does not survive that.

The watcher is only installed when `overlay.theme` points at an existing `.css` file — a palette name cannot change under your hands, so there is nothing to watch. When the file changes, the overlay re-reads it, re-applies the `overlay.accent` override and the `overlay.opacity` factor, pushes the new palette into the visualization and repaints. There is no need to restart the daemon.

Changing `overlay.theme` *itself* — or any other `[overlay]` key — in `config.toml` is picked up by `voicetype reload`: the overlay is rebuilt with the new settings and the old window is torn down. The overlay is only rebuilt when its own settings changed, so a reload that touches other keys leaves it alone.

## Fonts

The overlay draws every text — the status labels, the error and success messages and the live transcript — with **one** font. By default that is the **system's own font**: the family the desktop settings ask for, and their size multiplied by 1.6, because the OSD is read at a glance and a system size of nine or eleven points is too small for it.

```toml
[overlay]
font = ""          # empty = the system's own font
font_size = 0.0    # 0 = the system's own size, multiplied
```

Both keys are independent: you can pick a family and keep the system's size, or set a size and keep the system's family.

```toml
[overlay]
font = "JetBrains Mono"   # a family, or a comma-separated list of fallbacks
font_size = 16.0          # in points
```

A family list is handed to Pango as it is, so `"monospace, DejaVu Sans Mono, Liberation Mono"` picks the first face that is installed. A family that is not installed falls back to the system's font.

The size is the point size of the **main text** — the live transcript. The status labels are drawn at 70 % of it, so the two keep their hierarchy whatever the size is. A font that does not fit above the pill is shrunk until it does, and the overlay window grows with a large font instead of clipping the words.

Font changes are applied by `voicetype reload`, like every other `[overlay]` key.

## Accent override

To change one colour without writing a theme file, use `overlay.accent`:

```toml
[overlay]
accent = "#ff8800"        # #rgb, #rgba, #rrggbb or #rrggbbaa
```

This replaces the palette's `accent` only — `bar-color-left`, `bar-color-right` and `accent-alt` keep their palette values. An empty value keeps the palette accent. A value the config loader cannot parse is rejected by `voicetype config check`; if one slips through, the overlay ignores it with a warning in the log.

The accent slot itself is not painted on its own yet, so this changes the palette's accent value but not the colours on screen. For a visible change, set `bar-color-left` and `bar-color-right` in a theme file and use the `waveform` or `vu_meter` style — the `pill` style paints fixed colours.

## Example

A complete, copy-pasteable theme file — this is the built-in `tokyo-night` palette spelled out as CSS:

```css
/* ~/.config/voicetype/theme.css
 *
 * VoiceType overlay theme.
 * Only the keys listed below are read; anything else is ignored.
 * Every value must start with '#' and every line must end with ';'.
 */

@define-color background-color #09090cf5;  /* panel background */
@define-color border-color     #2a2e42b2;  /* panel border */
@define-color text-color       #c0caf5;   /* status label */
@define-color text-dim-color   #565f89;   /* dimmed text (not painted yet) */
@define-color accent-color     #7aa2f7;   /* main accent */
@define-color accent-alt-color #7dcfff;   /* second accent (not painted yet) */
@define-color recording-color  #f7768e;   /* recording state */
@define-color paused-color     #e0af68;   /* paused state */
@define-color processing-color #9ece6a;   /* processing state */
@define-color error-color      #f7768e;   /* error state */
@define-color success-color    #9ece6a;   /* success state */
@define-color bar-color-left   #7aa2f7;   /* gradient, left */
@define-color bar-color-right  #7dcfff;   /* gradient, right */
@define-color shadow-color     #00000057;  /* palette shadow (not painted yet) */
```

With it in place:

```toml
[overlay]
theme = "~/.config/voicetype/theme.css"
accent = "#ffb86c"
```
