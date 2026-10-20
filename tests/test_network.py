import http.server
import os
import sys
import threading
import types
import urllib.request

import pytest

from voicetype import cli, doctor, network
from voicetype.config import ConfigError, parse_config
from voicetype.network import ProxyError, bypass, parse_proxy


@pytest.fixture(autouse=True)
def clean_network(monkeypatch):
    """Each test starts with a default opener and no proxy variables, and leaves none behind."""
    for k in list(os.environ):
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "voicetype_proxy"):
            monkeypatch.delenv(k, raising=False)
    urllib.request.install_opener(None)
    yield
    urllib.request.install_opener(None)


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize(
    "text,scheme,host,port",
    [
        ("http://127.0.0.1:8080", "http", "127.0.0.1", 8080),
        ("127.0.0.1:3128", "http", "127.0.0.1", 3128),  # bare host:port = HTTP
        ("https://proxy.example.com", "https", "proxy.example.com", 443),
        ("socks5://localhost:1080", "socks5", "localhost", 1080),
        ("socks5h://localhost", "socks5h", "localhost", 1080),
        ("SOCKS4A://h:9", "socks4a", "h", 9),
        ("http://[::1]:8080", "http", "::1", 8080),
    ],
)
def test_parse_proxy(text, scheme, host, port):
    p = parse_proxy(text)
    assert (p.scheme, p.host, p.port) == (scheme, host, port)


def test_parse_proxy_credentials_and_redaction():
    p = parse_proxy("socks5://us%40er:p%3Ass@10.0.0.1:1080")
    assert (p.username, p.password) == ("us@er", "p:ss")
    assert "p:ss" not in p.redacted() and "***" in p.redacted()
    assert parse_proxy(p.url) == p  # url round-trips (percent-encoded)
    assert parse_proxy("http://u@h:1").password is None


@pytest.mark.parametrize("bad", ["", "  ", "ftp://h:1", "http://", "http://h:99999", "http://h:1/path", "http://h:abc"])
def test_parse_proxy_errors(bad):
    with pytest.raises(ProxyError):
        parse_proxy(bad)


def test_bypass_rules():
    pats = network.split_no_proxy("localhost, .corp.example , 10.0.0.5")
    assert bypass("localhost", pats) and bypass("a.corp.example", pats) and bypass("10.0.0.5", pats)
    assert not bypass("evil-corp.example", pats) and not bypass("www.google.com", pats)
    assert bypass("anything", ["*"])


# ------------------------------------------------------------------ priority + env
def test_priority_config_over_env_over_standard(monkeypatch):
    assert network.effective_proxy("") is None
    monkeypatch.setenv("VOICETYPE_PROXY", "http://env:1")
    assert network.effective_proxy("").host == "env"
    assert network.effective_proxy("http://cfg:2").host == "cfg"


def test_no_proxy_leaves_standard_env_alone(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://std:9")
    assert network.configure("", "") is None
    assert os.environ["HTTPS_PROXY"] == "http://std:9"  # untouched


def test_http_proxy_exports_env_for_other_libraries():
    spec = network.configure("http://127.0.0.1:8080", "localhost,.lan")
    assert spec.scheme == "http"
    for k in ("HTTP_PROXY", "https_proxy", "ALL_PROXY"):
        assert os.environ[k] == "http://127.0.0.1:8080"
    assert os.environ["NO_PROXY"] == "localhost,.lan"


# ------------------------------------------------------------------ real traffic through a local HTTP proxy
class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_GET(self):  # noqa: N802
        _ProxyHandler.seen.append(self.path)  # a proxied request carries the absolute URL
        body = b"via-proxy"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def local_proxy():
    _ProxyHandler.seen = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_traffic_actually_goes_through_http_proxy(local_proxy):
    network.configure(local_proxy, "")
    with urllib.request.urlopen("http://speech.invalid/recognize?x=1", timeout=5) as r:
        assert r.read() == b"via-proxy"
    assert _ProxyHandler.seen == ["http://speech.invalid/recognize?x=1"]


def test_check_connectivity_through_proxy(local_proxy):
    network.configure(local_proxy, "")
    ok, detail = network.check_connectivity("http://speech.invalid/generate_204", timeout=5)
    assert ok and "HTTP 200" in detail


def test_check_connectivity_reports_failure_readably():
    network.configure("http://127.0.0.1:9", "")  # nothing listens on the discard port
    ok, detail = network.check_connectivity("http://speech.invalid/", timeout=2)
    assert not ok and detail


# ------------------------------------------------------------------ SOCKS (PySocks replaced by a fake module)
class FakeSocks(types.ModuleType):
    SOCKS4, SOCKS5 = 1, 2

    def __init__(self):
        super().__init__("socks")
        self.calls = []

    def create_connection(self, addr, **kw):
        self.calls.append((addr, kw))
        raise OSError("fake socks: connection refused")


@pytest.fixture
def fake_socks(monkeypatch):
    mod = FakeSocks()
    monkeypatch.setitem(sys.modules, "socks", mod)
    return mod


def test_socks_without_pysocks_gives_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "socks", None)  # simulates ImportError
    with pytest.raises(ProxyError, match="PySocks"):
        network.configure("socks5://127.0.0.1:1080", "")
    assert not any(os.environ.get(k) for k in ("ALL_PROXY", "HTTPS_PROXY"))  # nothing half-applied


def test_socks_connection_uses_proxy_with_remote_dns_and_auth(fake_socks):
    network.configure("socks5h://bob:secret@10.1.1.1:1081", "localhost")
    with pytest.raises(OSError, match="fake socks"):
        urllib.request.urlopen("http://speech.invalid:80/x", timeout=3)
    (addr, kw), = fake_socks.calls
    assert addr == ("speech.invalid", 80)
    assert (kw["proxy_addr"], kw["proxy_port"]) == ("10.1.1.1", 1081)
    assert (kw["proxy_username"], kw["proxy_password"]) == ("bob", "secret")
    assert kw["proxy_type"] == fake_socks.SOCKS5 and kw["proxy_rdns"] is True


def test_socks5_plain_resolves_dns_locally_and_socks4(fake_socks):
    network.configure("socks4://h:1", "")
    with pytest.raises(OSError):
        urllib.request.urlopen("http://speech.invalid/", timeout=3)
    assert fake_socks.calls[0][1]["proxy_type"] == fake_socks.SOCKS4
    assert fake_socks.calls[0][1]["proxy_rdns"] is False


def test_socks_no_proxy_hosts_connect_directly(fake_socks):
    network.configure("socks5://h:1", "127.0.0.1")
    with pytest.raises(OSError):  # direct connection to a closed local port
        urllib.request.urlopen("http://127.0.0.1:9/", timeout=2)
    assert fake_socks.calls == []


def test_socks_does_not_touch_unix_sockets(fake_socks):
    import socket

    network.configure("socks5://h:1", "")
    assert socket.socket is not getattr(fake_socks, "socksocket", object())  # no global monkey-patching


# ------------------------------------------------------------------ config / doctor / cli
def test_config_validates_proxy():
    assert parse_config({"network": {"proxy": "socks5h://127.0.0.1:1080"}}).network.proxy.startswith("socks5h")
    with pytest.raises(ConfigError, match="network.proxy"):
        parse_config({"network": {"proxy": "ftp://nope"}})
    assert parse_config({}).network.proxy == ""


def test_broken_proxy_fails_component_build_instead_of_going_direct(monkeypatch):
    from voicetype.daemon import build_components

    monkeypatch.setitem(sys.modules, "socks", None)
    with pytest.raises(ProxyError):
        build_components(parse_config({"network": {"proxy": "socks5://127.0.0.1:1080"}}))


def test_daemon_reload_with_bad_proxy_keeps_previous_config(tmp_path, monkeypatch):
    from voicetype.daemon import Daemon

    p = tmp_path / "c.toml"
    p.write_text('[overlay]\nenabled = false\n[stt]\nfallback_provider = ""\n', encoding="utf-8")
    monkeypatch.setenv("VOICETYPE_CONFIG", str(p))
    monkeypatch.setattr("voicetype.daemon.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("voicetype.daemon.ControlServer", lambda handler: object())
    monkeypatch.setitem(sys.modules, "socks", None)
    d = Daemon()
    p.write_text('[overlay]\nenabled = false\n[stt]\nfallback_provider = ""\n[network]\nproxy = "socks5://h:1"\n', encoding="utf-8")
    assert d.handle_command("reload").startswith("error") and "PySocks" in d.handle_command("reload")
    assert d.dispatcher.components.config.network.proxy == ""


def test_doctor_proxy_rows():
    def run(proxy_env=None, has=lambda n: True):
        return {c.name: c for c in doctor.run_checks(which=lambda n: None, env={}, has_module=has, groups=lambda: set(),
                                                     path_exists=lambda p: False, access=lambda p, m: False)}["proxy"]

    assert run().ok and "none" in run().detail
    os.environ["VOICETYPE_PROXY"] = "socks5://u:pw@h:1"
    try:
        row = run(has=lambda n: False)
        assert not row.ok and "PySocks" in row.detail and "pw" not in row.detail
        assert run().ok and "net-test" in run().detail
    finally:
        del os.environ["VOICETYPE_PROXY"]


def test_cli_net_test(local_proxy, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("VOICETYPE_CONFIG", str(tmp_path / "none.toml"))
    assert cli.main(["net-test", "--proxy", local_proxy, "--url", "http://speech.invalid/generate_204"]) == 0
    out = capsys.readouterr().out
    assert "proxy: http://127.0.0.1" in out and "OK" in out
    assert cli.main(["net-test", "--proxy", "ftp://bad"]) == 2
    assert cli.main(["net-test", "--proxy", "http://127.0.0.1:9", "--url", "http://speech.invalid/"]) == 1
