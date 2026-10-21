"""Command line entry points: ``voicetype <cmd>`` and ``voicetype-toggle``."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, default_config_path, ensure_default_config, load_config, repair_config
from .control import COMMANDS, send_command

CLIENT_COMMANDS = tuple(c for c in COMMANDS if c != "profile")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="voicetype", description="Voice-to-text overlay daemon (Persian-first)")
    p.add_argument("--version", action="version", version=f"voicetype {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the daemon in the foreground")
    for name in ("toggle", "start", "stop", "cancel", "status", "reload", "quit"):
        sub.add_parser(name, help=f"send '{name}' to the running daemon")
    prof = sub.add_parser("profile", help="list / switch profiles (~/.config/voicetype/profiles/*.toml)")
    prof.add_argument("name", nargs="?", help="profile name, 'next' to cycle, 'default' for the base config")
    sub.add_parser("doctor", help="check the environment")
    nt = sub.add_parser("net-test", help="test connectivity through the configured proxy")
    nt.add_argument("--url", default=None, help="URL to fetch (default: Google's generate_204 endpoint)")
    nt.add_argument("--proxy", default=None, help="try this proxy instead of the configured one")
    cfg = sub.add_parser("config", help="configuration helpers")
    cfg.add_argument("action", choices=["init", "path", "check"])
    cfg.add_argument(
        "--repair",
        action="store_true",
        help="'init': back up an invalid config and write a fresh one",
    )
    models = sub.add_parser("models", help="manage offline Vosk models")
    models.add_argument("action", choices=["list", "install"])
    models.add_argument("name", nargs="?")
    models.add_argument("--sha256", help="expected SHA-256 of the archive")
    tr = sub.add_parser("translate", help="translate a text into [translate] language")
    tr.add_argument("text", help="the text to translate")
    return p


def _client(cmd: str) -> int:
    try:
        reply = send_command(cmd)
    except ConnectionError as e:
        print(f"voicetype: {e}", file=sys.stderr)
        return 3
    print(reply)
    return 0 if reply.startswith("ok") else 1


def _config(action: str, repair: bool = False) -> int:
    if action == "path":
        print(default_config_path())
        return 0
    if action == "init":
        if not repair:
            path, created = ensure_default_config()
            print(f"{'created' if created else 'already exists'}: {path}")
            return 0
        result = repair_config()
        if result.status == "repaired":
            print(f"repaired: {result.path} (the broken config was backed up as {result.backup})")
        elif result.status == "created":
            print(f"created: {result.path}")
        else:
            print(f"already exists: {result.path}")
        return 0
    try:
        cfg = load_config()
        if cfg.translate.enabled:
            # the language is only checked when the translator is built
            from .translate import resolve_language

            resolve_language(cfg.translate.language)
            resolve_language(cfg.translate.source, path="translate.source", allow_auto=True)
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"OK: {default_config_path()}")
    return 0


def _apply_network(proxy_override: str | None = None) -> None:
    """Configure the proxy from config (CLI commands that touch the network)."""
    from . import network

    cfg = load_config()
    network.configure(proxy_override if proxy_override is not None else cfg.network.proxy, cfg.network.no_proxy)


def _net_test(args: argparse.Namespace) -> int:
    from . import network

    try:
        _apply_network(args.proxy)
        spec = network.effective_proxy(args.proxy if args.proxy is not None else load_config().network.proxy)
    except (network.ProxyError, ConfigError) as e:
        print(f"voicetype: {e}", file=sys.stderr)
        return 2
    print(f"proxy: {spec.redacted() if spec else 'none (environment variables apply)'}")
    ok, detail = network.check_connectivity(args.url or network.CHECK_URL)
    print(("OK   " if ok else "FAIL ") + detail)
    return 0 if ok else 1


def _models(args: argparse.Namespace) -> int:
    from . import models

    if args.action == "list":
        have = models.installed()
        for key, info in models.MODELS.items():
            state = f"installed ({have[key]})" if key in have else "not installed"
            print(f"{key:10s} ~{info.size_mb} MB  {state}")
        return 0
    if not args.name:
        print("usage: voicetype models install <fa-small|fa>", file=sys.stderr)
        return 2
    from . import network

    try:
        _apply_network()
    except (network.ProxyError, ConfigError) as e:
        print(f"voicetype: {e}", file=sys.stderr)
        return 2
    try:
        path = models.install(
            args.name,
            expected_sha256=args.sha256,
            progress=lambda d, t: print(f"\r{d >> 20} / {t >> 20 if t else '?'} MB", end="", file=sys.stderr),
        )
    except models.ModelError as e:
        print(f"\nvoicetype: {e}", file=sys.stderr)
        return 1
    print(f"\ninstalled: {path}\nset in config:  [stt.vosk] model_path = \"{path}\"")
    return 0


def _translate(args: argparse.Namespace) -> int:
    """``voicetype translate "سلام"`` — try the configured translation."""
    from . import network
    from .translate import GoogleTranslator, resolve_language, translate_stage

    try:
        cfg = load_config()
        network.configure(cfg.network.proxy, cfg.network.no_proxy)
        target = resolve_language(cfg.translate.language)
        source = resolve_language(cfg.translate.source, path="translate.source", allow_auto=True)
    except (ConfigError, network.ProxyError) as e:
        print(f"voicetype: {e}", file=sys.stderr)
        return 2
    problems: list[str] = []
    translator = GoogleTranslator(
        target,
        source=source,
        timeout=cfg.translate.timeout_seconds,
        proxy=network.httpx_proxy_url(network.effective_proxy(cfg.network.proxy)),
        on_warning=problems.append,
    )
    # the same finishing touches the daemon applies (capitalise, sentence mark)
    chain = translate_stage(lambda text: text.strip(), translator, cfg.postprocess)
    try:
        out = chain(args.text)
    finally:
        translator.close()
    if problems:
        print(f"voicetype: {problems[0]}", file=sys.stderr)
        return 1
    print(out)
    return 0


def _safe_streams() -> None:
    """Never crash on a non-UTF-8 terminal (C locale, ssh): replace unencodable characters."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _safe_streams()
    args = build_parser().parse_args(argv)
    if args.cmd == "run":
        from .daemon import Daemon

        try:
            return Daemon().run()
        except ConfigError as e:
            print(e, file=sys.stderr)
            return 2
        except RuntimeError as e:  # e.g. another instance running
            print(f"voicetype: {e}", file=sys.stderr)
            return 1
    if args.cmd == "profile":
        return _client("profile" + (f" {args.name}" if args.name else ""))
    if args.cmd in CLIENT_COMMANDS:
        return _client(args.cmd)
    if args.cmd == "doctor":
        from .doctor import report, run_checks

        text, code = report(run_checks())
        print(text)
        return code
    if args.cmd == "net-test":
        return _net_test(args)
    if args.cmd == "config":
        return _config(args.action, args.repair)
    if args.cmd == "models":
        return _models(args)
    if args.cmd == "translate":
        return _translate(args)
    return 2


def toggle_main() -> None:
    """``voicetype-toggle`` — the command bound to the KDE Custom Shortcut."""
    sys.exit(main(["toggle"]))


if __name__ == "__main__":
    sys.exit(main())
