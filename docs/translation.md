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
backend = "auto"     # auto | services | googletrans
```

## Install

Translation needs the `translators` package (the fallback chain) and, as a
second choice, py-googletrans:

```bash
pipx install "voicetype[translate]"          # or: pip install translators googletrans
./scripts/install-user.sh translate          # the installer knows the extra
```

`voicetype doctor` shows whether the packages are there — and whether the
services really answer.

## The two backends

| `backend` | What it does |
|---|---|
| `services` | The [`translators`](https://github.com/UlionTse/translators) package: Google, Bing, Yandex and Alibaba are tried in turn, and the first one that answers wins. No API key, no account. |
| `googletrans` | py-googletrans: a single service (Google). |
| `auto` (default) | The fallback chain when its package is installed, py-googletrans otherwise. |

The chain matters: the free endpoints of the big providers rate-limit (HTTP 429)
and block (HTTP 403) regularly — a single service is not enough. The services
share the time budget of `timeout_seconds`, so a blocked service cannot hold the
daemon up.

| Service | Order | Notes |
|---|---|---|
| `google` | 1st | The best quality. |
| `bing` | 2nd | The usual stand-in for Google. |
| `yandex` | 3rd | Does not know Persian — it is skipped for a Persian source. |
| `alibaba` | 4th | The last resort. |

The package looks the server region up over the network **while it is imported**
— without a timeout, so it can hang the daemon. VoiceType therefore sets
`translators_default_region=EN` before the import; set it yourself (to `CN` for
the Chinese servers) if you need another region.

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

The same helper is available in the code:

```python
from voicetype.translate import translate_fa_to_en

translate_fa_to_en("سلام، حال شما چطور است؟")   # → "Hello, how are you?"
```

It returns the original text when every service fails, and an empty string for
an empty input.

## Languages

`language` accepts a code, a name and a regional code:

| What you write | What happens |
|---|---|
| `"en"`, `"de"`, `"fa"` | the language code is used as it is |
| `"English"`, `"german"`, `"persian"` | the name is looked up (`"english"` → `en`) |
| `"en-US"`, `"fa-IR"` | the region is dropped (`fa-IR` → `fa`) |
| `"zh"` | simplified Chinese |
| `"zh-TW"`, `"zh-Hant"` | traditional Chinese |

An unknown language is a config error (`config: 'translate.language' is not a known
language: …`), reported when the config is loaded — the old settings stay active.

## When it fails

A translation never eats your words: when no service answers, the original text
is injected unchanged and the overlay shows a warning. The daemon log
(`~/.local/state/voicetype/voicetype.log`) names the service and the reason
(`translation failed: google (Unexpected status code "429" …)`).

A service that fails is not fatal — the chain moves on to the next one.

## Privacy

Translation sends the **text** of your transcript to the service that answers —
Google, Bing, Yandex or Alibaba. With an offline provider (`vosk`, `whisper`)
the transcript stays on your machine until you switch translation on.

## Proxy

The translation goes through the proxy from `[network]`, like every other
outgoing request:

```toml
[network]
proxy = "socks5h://127.0.0.1:1080"
```

The fallback chain uses `requests`, which understands the `socks5h` scheme
itself. The py-googletrans backend uses httpx, which does not: there the scheme
is downgraded to `socks5`. SOCKS proxies need the `socksio` package, which
comes with the `translate` extra (`httpx[socks]`).

## Limits

- 15 000 characters per request (the free endpoint's limit).
- The free endpoints are unofficial and can be rate-limited or blocked — the
  daemon keeps working, the text is simply injected untranslated.
- The services answer in different qualities: Google is the best, the others
  are a fallback.
