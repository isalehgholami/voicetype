"""ConfigManager: frozen dataclasses, TOML loading, validation, hot reload."""

from __future__ import annotations

import dataclasses
import os
import re
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENV_CONFIG = "VOICETYPE_CONFIG"


class ConfigError(ValueError):
    """Raised for invalid configuration (message is user-facing)."""


def default_config_path() -> Path:
    env = os.environ.get(ENV_CONFIG)
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "voicetype" / "config.toml"


# ---------------------------------------------------------------- dataclasses
@dataclass(frozen=True)
class HotkeyConfig:
    combo: str = "super+alt+v"
    toggle_mode: str = "press"  # press | hold
    escape_stops: bool = True
    extra_stops: tuple[str, ...] = ("esc",)


@dataclass(frozen=True)
class VoskConfig:
    model_path: str = "/usr/share/vosk/vosk-model-fa-0.42"


@dataclass(frozen=True)
class WhisperConfig:
    model: str = "small"
    device: str = "cpu"  # cpu | cuda


@dataclass(frozen=True)
class GoogleCloudConfig:
    keyfile: str = ""


@dataclass(frozen=True)
class SttConfig:
    provider: str = "google_free"
    language: str = "fa-IR"
    silence_threshold_seconds: float = 3.0
    timeout_seconds: float = 60.0
    fallback_provider: str = "vosk"  # used after fallback_after consecutive errors; "" disables
    fallback_after: int = 3
    # Streaming: native for Vosk; for batch providers (Google, Whisper) audio is cut at pauses and sent
    # while you talk, so text appears live and only the last segment is left at the end.
    streaming: bool = True
    stream_interim_seconds: float = 1.5  # refresh the in-progress sentence; 0 = only at pauses
    stream_pause_seconds: float = 0.6  # silence that ends a segment
    stream_max_segment_seconds: float = 12.0
    vosk: VoskConfig = field(default_factory=VoskConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    google_cloud: GoogleCloudConfig = field(default_factory=GoogleCloudConfig)


@dataclass(frozen=True)
class AudioConfig:
    device: str = ""  # "" = system default; name substring or index
    blocksize: int = 1600  # samples @16 kHz = 100 ms
    max_buffer_seconds: float = 120.0
    debug_save_audio: bool = False
    pause_media: bool = False  # pause playing media (playerctl) while recording


@dataclass(frozen=True)
class OverlayConfig:
    enabled: bool = True
    position: str = "top"  # top | center | bottom
    anchor_horizontal: str = "center"  # left | center | right
    offset_x: int = 0
    offset_y: int = 12
    theme: str = "dark"
    opacity: float = 0.86
    width: int = 360
    height: int = 96
    show_partial_text: bool = True
    show_audio_meter: bool = True
    font: str = "Vazirmatn, Noto Sans Arabic, Sans"


@dataclass(frozen=True)
class InjectorConfig:
    backend: str = "auto"  # auto | paste | wtype | ydotool | xdotool
    typing_delay_ms: int = 0
    strip_whitespace: bool = False
    paste_shortcut: str = "auto"  # auto | ctrl+v | ctrl+shift+v
    restore_clipboard: bool = True
    release_modifiers: bool = True


@dataclass(frozen=True)
class PostprocessConfig:
    capitalize_first: bool = True
    auto_punctuate: bool = True
    normalize_zwnj: bool = True
    digits: str = "keep"  # keep | latin | persian
    persian_punctuation: bool = True
    english_terms: bool = True
    filter_hallucinations: bool = True
    voice_commands: bool = False
    custom_replacements: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NetworkConfig:
    proxy: str = ""  # http://host:port | socks5h://user:pass@host:1080 ; "" = use env vars
    no_proxy: str = "localhost,127.0.0.1,::1"


@dataclass(frozen=True)
class Config:
    log_level: str = "INFO"
    notify_errors: bool = True  # desktop notification (notify-send) on errors
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    injector: InjectorConfig = field(default_factory=InjectorConfig)
    postprocess: PostprocessConfig = field(default_factory=PostprocessConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)


# ---------------------------------------------------------------- validation
PROVIDERS = ("google_free", "google_cloud", "vosk", "whisper")
_ENUMS: dict[str, tuple[str, ...]] = {
    "hotkey.toggle_mode": ("press", "hold"),
    "stt.provider": PROVIDERS,
    "stt.whisper.device": ("cpu", "cuda"),
    "overlay.position": ("top", "center", "bottom"),
    "overlay.anchor_horizontal": ("left", "center", "right"),
    "overlay.theme": ("light", "dark"),
    "injector.backend": ("auto", "paste", "wtype", "ydotool", "xdotool"),
    "injector.paste_shortcut": ("auto", "ctrl+v", "ctrl+shift+v"),
    "postprocess.digits": ("keep", "latin", "persian"),
    "log_level": ("DEBUG", "INFO", "WARNING", "ERROR"),
}
_WHISPER_MODELS = ("tiny", "base", "small", "medium", "large", "large-v2", "large-v3")
_RANGES: dict[str, tuple[float, float]] = {
    "stt.silence_threshold_seconds": (0.5, 30.0),
    "stt.timeout_seconds": (5.0, 300.0),
    "stt.fallback_after": (1, 20),
    "stt.stream_interim_seconds": (0.0, 10.0),
    "stt.stream_pause_seconds": (0.2, 3.0),
    "stt.stream_max_segment_seconds": (3.0, 30.0),
    "audio.blocksize": (160, 16000),
    "audio.max_buffer_seconds": (5.0, 3600.0),
    "overlay.offset_x": (-1920, 1920),
    "overlay.offset_y": (-1080, 1080),
    "overlay.opacity": (0.0, 1.0),
    "overlay.width": (120, 1920),
    "overlay.height": (48, 480),
    "injector.typing_delay_ms": (0, 50),
}


def _fail(path: str, msg: str) -> ConfigError:
    return ConfigError(f"config: '{path}' {msg}")


def _build(cls: type, data: dict[str, Any], prefix: str):
    """Recursively build a dataclass from a dict, validating types/enums/ranges."""
    if not isinstance(data, dict):
        raise _fail(prefix.rstrip("."), "must be a table")
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(data) - set(fields)
    if unknown:
        raise _fail(prefix + sorted(unknown)[0], "is not a known option")
    kwargs: dict[str, Any] = {}
    for name, f in fields.items():
        if name not in data:
            continue
        path = prefix + name
        value = data[name]
        default = f.default if f.default is not dataclasses.MISSING else f.default_factory()  # type: ignore[misc]
        if dataclasses.is_dataclass(default):
            kwargs[name] = _build(type(default), value, path + ".")
            continue
        if name == "custom_replacements":
            if not isinstance(value, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in value.items()
            ):
                raise _fail(path, "must be a table of string → string")
            kwargs[name] = tuple(value.items())
            continue
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise _fail(path, f"must be true/false (got {value!r})")
        elif isinstance(default, int):
            if isinstance(value, bool) or not isinstance(value, int):
                raise _fail(path, f"must be an integer (got {value!r})")
        elif isinstance(default, float):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise _fail(path, f"must be a number (got {value!r})")
            value = float(value)
        elif isinstance(default, str):
            if not isinstance(value, str):
                raise _fail(path, f"must be a string (got {value!r})")
        elif isinstance(default, tuple):
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise _fail(path, "must be a list of strings")
            value = tuple(value)
        if path in _ENUMS and value not in _ENUMS[path]:
            raise _fail(path, f"must be one of {', '.join(_ENUMS[path])} (got {value!r})")
        if path in _RANGES:
            lo, hi = _RANGES[path]
            if not (lo <= value <= hi):
                raise _fail(path, f"must be between {lo} and {hi} (got {value!r})")
        kwargs[name] = value
    return cls(**kwargs)


def validate(cfg: Config) -> Config:
    """Cross-field checks that single-field validation can't express."""
    if cfg.stt.provider == "whisper" and cfg.stt.whisper.model not in _WHISPER_MODELS:
        raise _fail("stt.whisper.model", f"must be one of {', '.join(_WHISPER_MODELS)}")
    if cfg.stt.provider == "google_cloud" and not cfg.stt.google_cloud.keyfile:
        raise _fail("stt.google_cloud.keyfile", "is required when provider = \"google_cloud\"")
    fb = cfg.stt.fallback_provider
    if fb and fb not in PROVIDERS:
        raise _fail("stt.fallback_provider", f"must be empty or one of {', '.join(PROVIDERS)}")
    if not cfg.hotkey.combo.strip():
        raise _fail("hotkey.combo", "must not be empty")
    if cfg.network.proxy.strip():
        from .network import ProxyError, parse_proxy

        try:
            parse_proxy(cfg.network.proxy)
        except ProxyError as e:
            raise _fail("network.proxy", str(e)) from e
    return cfg


def parse_config(data: dict[str, Any]) -> Config:
    # Accept [stt.vosk] etc. as nested tables, and flat log_level.
    return validate(_build(Config, data, ""))


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"config: {path} is not valid TOML: {e}") from e
    except OSError as e:
        raise ConfigError(f"config: cannot read {path}: {e}") from e


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Tables merge recursively; every other value in ``override`` replaces the base value."""
    out = dict(base)
    for k, v in override.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


_PROFILE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def profiles_dir(config_path: Path | None = None) -> Path:
    return (config_path or default_config_path()).parent / "profiles"


def list_profiles(config_path: Path | None = None) -> list[str]:
    d = profiles_dir(config_path)
    return sorted(p.stem for p in d.glob("*.toml")) if d.is_dir() else []


def valid_profile_name(name: str) -> bool:
    return bool(_PROFILE_NAME.match(name))


def load_config(path: Path | None = None, profile: str | None = None) -> Config:
    """Load config from ``path`` (optionally overlaid with ``profiles/<profile>.toml``).
    A missing base file yields the defaults."""
    path = path or default_config_path()
    data = _read_toml(path) if path.exists() else {}
    if profile:
        if not valid_profile_name(profile):
            raise ConfigError(f"config: invalid profile name '{profile}'")
        ppath = profiles_dir(path) / f"{profile}.toml"
        if not ppath.exists():
            raise ConfigError(f"config: profile '{profile}' not found ({ppath})")
        data = deep_merge(data, _read_toml(ppath))
    return parse_config(data)


SAMPLE_CONFIG = """\
# VoiceType configuration — every key is optional; these are the defaults.
log_level = "INFO"
notify_errors = true       # desktop notification on errors (notify-send)

[hotkey]
combo = "super+alt+v"      # used by install-hotkey.sh and the optional evdev listener
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
blocksize = 1600
debug_save_audio = false
pause_media = false        # pause playing media (playerctl) while recording

[overlay]
enabled = true
position = "top"
anchor_horizontal = "center"
offset_x = 0
offset_y = 12
theme = "dark"
opacity = 0.86
width = 360
height = 96
show_partial_text = true
show_audio_meter = true

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
"""


def ensure_default_config(path: Path | None = None) -> tuple[Path, bool]:
    """Create a commented sample config if none exists. Returns (path, created)."""
    path = path or default_config_path()
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SAMPLE_CONFIG, encoding="utf-8")
    return path, True


class ConfigManager:
    """Thread-safe holder of the current immutable :class:`Config`."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_path()
        self.profile: str | None = None
        self._lock = threading.Lock()
        self._config = load_config(self.path)

    def get(self) -> Config:
        with self._lock:
            return self._config

    def reload(self) -> Config:
        """Re-read the file. On error the previous config stays active and the error is raised."""
        new = load_config(self.path, self.profile)
        with self._lock:
            self._config = new
        return new

    def set_profile(self, name: str | None) -> Config:
        """Switch profile (None = base config). On error nothing changes."""
        new = load_config(self.path, name)
        with self._lock:
            self._config = new
            self.profile = name
        return new

    def next_profile(self) -> str | None:
        """Cycle: base → profile1 → profile2 → … → base."""
        names = list_profiles(self.path)
        if not names:
            raise ConfigError("config: no profiles found in " + str(profiles_dir(self.path)))
        if self.profile is None:
            return names[0]
        i = names.index(self.profile) + 1 if self.profile in names else 0
        return names[i] if i < len(names) else None
