# Network & proxy

VoiceType talks to the internet only for: **Google speech** (default provider), **Google Cloud** (optional) and **model downloads**
(`voicetype models install`, faster-whisper). Offline providers (`vosk`, `whisper` with a downloaded model) need no network.

If you're behind a firewall or your connection is flaky, route everything through a proxy.

## Set a proxy

```toml
# ~/.config/voicetype/config.toml
[network]
proxy = "socks5h://127.0.0.1:1080"        # or http://127.0.0.1:8080
no_proxy = "localhost,127.0.0.1,::1,.lan"
```

Then `voicetype reload` and check it works:

```bash
voicetype net-test                 # fetches a tiny URL through the proxy and says OK / FAIL (+ reason)
voicetype net-test --proxy http://127.0.0.1:8080     # try a proxy without editing the config
voicetype doctor                   # shows the active proxy (password hidden)
```

| `proxy` value | Meaning | Extra needed |
|---|---|---|
| `http://host:port`, `https://host:port` | HTTP(S) proxy (CONNECT for TLS) | – |
| `socks5h://host:port` | SOCKS5, **DNS resolved by the proxy** (best when DNS is blocked) | `pip install PySocks` |
| `socks5://host:port` | SOCKS5, DNS resolved locally | PySocks |
| `socks4://`, `socks4a://` | SOCKS4 / 4a | PySocks |
| `user:pass@host:port` inside the URL | authentication (percent-encode special characters) | – |
| `host:port` (no scheme) | treated as HTTP | – |
| `""` (empty) | use the standard environment variables, if any | – |

Install SOCKS support with `scripts/install-user.sh socks` (default), `pip install 'voicetype[socks]'`, or `pacman -S python-pysocks`.

## Precedence

1. `[network] proxy` in the config
2. environment variable `VOICETYPE_PROXY`
3. the standard `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` variables

`no_proxy` entries (comma-separated: exact host, `.suffix`, or `*`) are connected to directly.
The systemd service doesn't inherit your shell environment — put the proxy in the config file instead.

## Behaviour you can rely on

- **No silent direct connection.** If a proxy is configured but unusable (e.g. SOCKS without PySocks), the daemon refuses to start and `reload` fails while keeping the old settings — your audio is never sent directly by accident.
- SOCKS is applied to VoiceType's own HTTP client only. Local Unix sockets (the control channel) are never touched.
- The proxy password is never logged; logs and `doctor` print `socks5h://user:***@host:1080`.
- The proxy is also exported as `HTTP(S)_PROXY` / `ALL_PROXY` inside the process so downloads done by third-party libraries (faster-whisper model fetch, Google Cloud client) use it too. Those libraries may need their own SOCKS extras.

## Troubleshooting

| `net-test` says | Likely cause |
|---|---|
| `FAIL ConnectionRefusedError` | nothing is listening on that proxy address/port |
| `FAIL ... PySocks` | install PySocks (see above) |
| `FAIL HTTP 407` | proxy needs credentials: `http://user:pass@host:port` |
| `FAIL timed out` | proxy reachable but the target is blocked / proxy is down upstream |
| `OK` but dictation still fails | check `journalctl --user -u voicetype -f`; Google may be rate-limiting (`fallback_provider = "vosk"` kicks in after 3 failures) |
