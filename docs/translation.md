# Translation

VoiceType can translate every finished transcript into a language of your choice
before it is injected. It is **off by default** — switch it on only if you
want it.

```toml
[translate]
enabled = true
language = "en"      # a code ("en", "de", "zh-CN") or a name ("English", "german")
source = "auto"      # "auto" = detect the language you spoke
timeout_seconds = 15.0
```

## Install

Translation needs one extra package (py-googletrans):

```bash
pipx install "voicetype[translate]"          # or: pip install googletrans
./scripts/install-user.sh translate          # the installer knows the extra
```

`voicetype doctor` shows whether the package is there.

## How it works

```
speech → STT → post-processing (Persian) → translation → inject
```

- The Persian clean-up (ZWNJ, digits, English terms) runs on the text **as it was spoken**.
- The translation then rewrites it in the target language.
- `capitalize_first` and `auto_punctuate` are applied **once more** to the translated
  text, so the result is a proper sentence in the target language.

The live preview in the overlay always shows the text as you spoke it — only the
final, injected text is translated. Translating every partial would mean a
request per word and a flickering preview.

## Trying it out

```bash
voicetype translate "سلام دنیا"      # → Hello world.
```

The command uses the language from your config, even when `enabled = false` —
it is a manual request, not the daemon.

## Languages

`language` accepts everything py-googletrans knows:

| What you write | What happens |
|---|---|
| `"en"`, `"de"`, `"fa"` | the language code is used as it is |
| `"English"`, `"german"`, `"persian"` | the name is looked up (`"english"` → `en`) |
| `"en-US"`, `"fa-IR"` | the region is dropped (`fa-IR` → `fa`) |
| `"zh-CN"`, `"zh-TW"` | kept — simplified and traditional Chinese differ |

An unknown language is a config error (`config: 'translate.language' is not a known
language: …`), reported when the config is loaded — the old settings stay active.

## When it fails

A translation never eats your words: if Google cannot be reached, the original
text is injected unchanged and the overlay shows a warning. The daemon log
(`~/.local/state/voicetype/voicetype.log`) has the details.

## Privacy

Translation sends the **text** of your transcript to Google — the same company
that already receives your audio when `stt.provider = "google_free"`. With an
offline provider (`vosk`, `whisper`) the transcript stays on your machine until
you switch translation on.

## Proxy

The translation goes through the proxy from `[network]`, like every other
outgoing request:

```toml
[network]
proxy = "socks5h://127.0.0.1:1080"
```

httpx does not know the `socks5h` scheme, so it is downgraded to `socks5` for the
translation request. SOCKS proxies need the `socksio` package, which comes with
the `translate` extra (`httpx[socks]`).

## Limits

- 15 000 characters per request (the free endpoint's limit).
- The free endpoint is unofficial and can be rate-limited or blocked — the
  daemon keeps working, the text is simply injected untranslated.
