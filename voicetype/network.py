"""Outbound network settings: HTTP(S) and SOCKS proxy for every service VoiceType talks to
(Google speech, Google Cloud, model downloads).

Priority: ``[network] proxy`` in config  >  ``VOICETYPE_PROXY``  >  the standard ``HTTP(S)_PROXY`` /
``ALL_PROXY`` environment variables (handled by urllib itself).

* ``http://`` / ``https://`` proxies use urllib's own ProxyHandler.
* ``socks4://`` ``socks4a://`` ``socks5://`` ``socks5h://`` need ``pip install PySocks`` (extra ``[socks]``).
  SOCKS is wired into a urllib *opener* only — never by monkey-patching ``socket`` — so local Unix
  sockets (the daemon control channel) are unaffected.
"""

from __future__ import annotations

import dataclasses
import http.client
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .log import get_logger

log = get_logger("network")

ENV_PROXY = "VOICETYPE_PROXY"
HTTP_SCHEMES = ("http", "https")
SOCKS_SCHEMES = ("socks4", "socks4a", "socks5", "socks5h")
DEFAULT_PORTS = {"http": 8080, "https": 443, "socks4": 1080, "socks4a": 1080, "socks5": 1080, "socks5h": 1080}
CHECK_URL = "https://www.google.com/generate_204"


class ProxyError(ValueError):
    """Invalid or unusable proxy setting (message is user-facing)."""


@dataclass(frozen=True)
class ProxySpec:
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def is_socks(self) -> bool:
        return self.scheme in SOCKS_SCHEMES

    @property
    def url(self) -> str:
        auth = ""
        if self.username is not None:
            auth = urllib.parse.quote(self.username, safe="")
            if self.password is not None:
                auth += ":" + urllib.parse.quote(self.password, safe="")
            auth += "@"
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{auth}{host}:{self.port}"

    def redacted(self) -> str:
        """Safe to log: never includes the password."""
        user = f"{self.username}:***@" if self.username is not None else ""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{user}{host}:{self.port}"


def parse_proxy(text: str) -> ProxySpec:
    """``http://host:port``, ``socks5://user:pass@host:1080`` … A bare ``host:port`` means HTTP."""
    raw = text.strip()
    if not raw:
        raise ProxyError("proxy is empty")
    if "://" not in raw:
        raw = "http://" + raw
    u = urllib.parse.urlsplit(raw)
    scheme = u.scheme.lower()
    if scheme not in HTTP_SCHEMES + SOCKS_SCHEMES:
        raise ProxyError(
            f"unsupported proxy scheme '{scheme}' (use http, https, socks4, socks4a, socks5 or socks5h)"
        )
    if not u.hostname:
        raise ProxyError(f"proxy '{text}' has no host")
    try:
        port = u.port or DEFAULT_PORTS[scheme]
    except ValueError as e:
        raise ProxyError(f"proxy '{text}' has an invalid port") from e
    if u.path not in ("", "/") or u.query or u.fragment:
        raise ProxyError(f"proxy '{text}' must look like scheme://host:port")
    return ProxySpec(
        scheme,
        u.hostname,
        port,
        urllib.parse.unquote(u.username) if u.username is not None else None,
        urllib.parse.unquote(u.password) if u.password is not None else None,
    )


def split_no_proxy(text: str) -> list[str]:
    return [p.strip().lower() for p in text.split(",") if p.strip()]


def bypass(host: str, patterns: list[str]) -> bool:
    """no_proxy matching: exact host, ``.example.com`` / ``example.com`` suffix, or ``*``."""
    h = host.lower().strip("[]")
    for p in patterns:
        if p == "*":
            return True
        p = p.lstrip("*")
        if h == p.lstrip(".") or (p.startswith(".") and h.endswith(p)) or h.endswith("." + p.lstrip(".")):
            return True
    return False


# ------------------------------------------------------------------ SOCKS (urllib handlers)
def _load_socks():
    try:
        import socks  # PySocks
    except ImportError as e:
        raise ProxyError("SOCKS proxy needs PySocks: pip install PySocks   (or: pip install 'voicetype[socks]')") from e
    return socks


_SOCKS_TYPES = {"socks4": "SOCKS4", "socks4a": "SOCKS4", "socks5": "SOCKS5", "socks5h": "SOCKS5"}


def _socks_connect(spec: ProxySpec, host: str, port: int, timeout):
    socks = _load_socks()
    kwargs = {
        "proxy_type": getattr(socks, _SOCKS_TYPES[spec.scheme]),
        "proxy_addr": spec.host,
        "proxy_port": spec.port,
        "proxy_username": spec.username,
        "proxy_password": spec.password,
        # socks4a / socks5h resolve DNS on the proxy (needed when DNS itself is blocked)
        "proxy_rdns": spec.scheme in ("socks4a", "socks5h"),
    }
    if timeout is not None and timeout is not getattr(socks, "_GLOBAL_DEFAULT_TIMEOUT", object()):
        kwargs["timeout"] = timeout
    return socks.create_connection((host, port), **kwargs)


def make_socks_handlers(spec: ProxySpec, no_proxy: list[str]) -> list[urllib.request.BaseHandler]:
    _load_socks()  # fail early with a helpful message

    class _HTTPConn(http.client.HTTPConnection):
        def connect(self):
            if bypass(self.host, no_proxy):
                return super().connect()
            self.sock = _socks_connect(spec, self.host, self.port, self.timeout)

    class _HTTPSConn(http.client.HTTPSConnection):
        def connect(self):
            if bypass(self.host, no_proxy):
                return super().connect()
            sock = _socks_connect(spec, self.host, self.port, self.timeout)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)

    class _HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(_HTTPConn, req)

    class _HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_HTTPSConn, req, context=ssl.create_default_context())

    return [_HTTPHandler(), _HTTPSHandler()]


# ------------------------------------------------------------------ configuration
_PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def effective_proxy(config_proxy: str = "") -> ProxySpec | None:
    """Config value, else ``VOICETYPE_PROXY``; None means "use the standard environment variables"."""
    text = config_proxy.strip() or os.environ.get(ENV_PROXY, "").strip()
    return parse_proxy(text) if text else None


def httpx_proxy_url(spec: ProxySpec | None) -> str:
    """The proxy as a URL httpx understands (``socks5h`` → ``socks5``).

    httpx has no ``socks5h`` scheme, so the remote-DNS variant is downgraded;
    everything else is passed through unchanged.
    """
    if spec is None:
        return ""
    if spec.scheme == "socks5h":
        spec = dataclasses.replace(spec, scheme="socks5")
    return spec.url


def proxy_url(config_proxy: str = "") -> str:
    """The active proxy as a URL (``""`` when none is configured).

    The URL keeps the scheme as it was written: ``requests`` (the library the
    free translation services use) understands ``socks5h`` itself.
    """
    spec = effective_proxy(config_proxy)
    return spec.url if spec else ""


def configure(config_proxy: str = "", no_proxy: str = "") -> ProxySpec | None:
    """Install the process-wide urllib opener (and export env vars for other HTTP libraries).
    Returns the active explicit proxy, or None when only the environment applies.
    Raises :class:`ProxyError`; on error nothing is changed."""
    spec = effective_proxy(config_proxy)
    patterns = split_no_proxy(no_proxy)
    if spec is None:
        urllib.request.install_opener(urllib.request.build_opener())  # default: honours env vars
        return None
    if spec.is_socks:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), *make_socks_handlers(spec, patterns))
    else:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": spec.url, "https": spec.url}))
    urllib.request.install_opener(opener)
    # Libraries that don't go through urllib (requests/httpx/grpc) read the standard variables.
    value = spec.url
    for key in _PROXY_ENV:
        os.environ[key] = value
    if patterns:
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = ",".join(patterns)
    log.info("outbound proxy: %s", spec.redacted())
    return spec


def check_connectivity(url: str = CHECK_URL, timeout: float = 8.0) -> tuple[bool, str]:
    """Fetch ``url`` through the configured opener. Returns (ok, human-readable detail)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.status in (200, 204), f"HTTP {resp.status} from {url}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} from {url}"
    except (urllib.error.URLError, OSError, ProxyError) as e:
        reason = getattr(e, "reason", e)
        return False, f"{type(reason).__name__}: {reason}"
