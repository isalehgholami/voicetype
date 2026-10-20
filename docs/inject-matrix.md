# Injection matrix (T-0.3 / T-1.5.6) — fill in on your machine

Run `./spikes/inject_matrix.sh`, focus each application's text field during the countdown, then mark the result.
Test text mixes RTL and LTR: `سلام دنیا، امروز با Python و Qt کار کردم.`

Legend: ✅ works · ⚠️ works with caveat · ❌ fails · — not tested

| Application | paste Ctrl+V (default) | paste Ctrl+Shift+V | wtype | ydotool type | Notes |
|---|---|---|---|---|---|
| KWrite | — | — | — | — | |
| Kate | — | — | — | — | |
| Firefox (address bar) | — | — | — | — | |
| Firefox (textarea) | — | — | — | — | |
| Chromium / Chrome | — | — | — | — | |
| Telegram Desktop | — | — | — | — | |
| Konsole | — | — | — | — | expected: Ctrl+Shift+V |
| JetBrains IDE | — | — | — | — | |
| LibreOffice Writer | — | — | — | — | |
| Dolphin (rename) | — | — | — | — | |

Expected (from analysis, **unverified**): `wtype` fails on KWin (no virtual-keyboard protocol); paste works everywhere
except terminals, which need `Ctrl+Shift+V` (auto-detected when `kdotool` is installed, else set
`[injector] paste_shortcut`).

Decision to confirm after testing: default backend `paste` with `wtype` as fallback (docs/adr/0002).
