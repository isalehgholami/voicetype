"""ConfigManager: frozen dataclasses, TOML loading, validation, hot reload."""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

ENV_CONFIG = "VOICETYPE_CONFIG"

#: The font the overlay draws its text with.  The default is empty: the
#: overlay then follows the system's own font — the family the desktop
#: settings ask for, and their size multiplied, so the OSD is readable at a
#: glance.  A value ("JetBrains Mono", "monospace, DejaVu Sans Mono", …)
#: picks a face instead.
DEFAULT_FONT = ""

#: The point size of the overlay's main text — the live transcript.  The
#: default is zero: the overlay then follows the system's own font size.  A
#: value picks the size in points.
DEFAULT_FONT_SIZE = 0.0


class ConfigError(ValueError):
    """Raised for invalid configuration (message is user-facing)."""


def default_config_path() -> Path:
    env = os.environ.get(ENV_CONFIG)
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "voicetype" / "config.toml"


def resolve_font_family(value: str | None) -> str:
    """The font family the user configured.

    An empty value means "the system's own font": the caller resolves it
    from the desktop settings.
    """
    return str(value or "").strip()


def resolve_font_size(value: float | int | None) -> float:
    """The point size the user configured.

    Zero means "the system's own font size"; the caller resolves it from
    the desktop settings.
    """
    try:
        return max(0.0, float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_FONT_SIZE


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
    position: str = "bottom"  # top | center | bottom
    anchor_horizontal: str = "center"  # left | center | right
    offset_x: int = 0
    offset_y: int = 50
    theme: str = "nocturne"
    opacity: float = 0.86
    width: int = 360  # the fixed width of the card
    height: int = 128  # the fixed height of the card
    show_partial_text: bool = True
    show_audio_meter: bool = True
    font: str = DEFAULT_FONT
    font_size: float = DEFAULT_FONT_SIZE  # point size of the overlay's main text
    style: str = "hud"  # deprecated: the card has one shape now
    animations: bool = True  # master switch for the animated transitions
    show_transcript: bool = True  # live word-by-word transcript on the card
    transcript_words: int = 8  # how many words the live preview keeps
    accent: str = ""  # hex override for the accent colour; "" = palette default
    fade_ms: int = 140  # show/hide fade duration in milliseconds
    sounds: bool = False  # audible start/stop/error cues


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
class TranslateConfig:
    enabled: bool = False  # translate the transcript before it is injected
    language: str = "en"  # target language: a code ("en") or a name ("English")
    source: str = "auto"  # "auto" detects the language that was spoken
    timeout_seconds: float = 15.0
    backend: str = "auto"  # auto | services | googletrans


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
    translate: TranslateConfig = field(default_factory=TranslateConfig)


# ---------------------------------------------------------------- validation
PROVIDERS = ("google_free", "google_cloud", "vosk", "whisper")

#: Palettes accepted by ``overlay.theme``.  The list mirrors
#: ``voicetype.ui.theme.PALETTE_NAMES``; the core must not import the UI
#: package, so the names are repeated here and the test suite keeps the two
#: lists in sync.
THEME_NAMES = (
    "catppuccin-mocha",
    "dracula",
    "gruvbox-dark",
    "nocturne",
    "nord",
    "rose-pine",
    "tokyo-night",
    "tokyo-night-light",
)

#: Pre-1.0 ``overlay.theme`` values, still accepted for existing config files.
LEGACY_THEME_NAMES = ("dark", "light")

#: ``overlay.accent`` is either empty (the palette decides) or a hex colour.
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

#: A language is a code ("en", "zh-CN") or a name ("English", "chinese").  The
#: exact spelling is checked against py-googletrans when the translator is built.
_LANGUAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,31}$")

_ENUMS: dict[str, tuple[str, ...]] = {
    "hotkey.toggle_mode": ("press", "hold"),
    "stt.provider": PROVIDERS,
    "stt.whisper.device": ("cpu", "cuda"),
    "overlay.position": ("top", "center", "bottom"),
    "overlay.anchor_horizontal": ("left", "center", "right"),
    "overlay.theme": LEGACY_THEME_NAMES + THEME_NAMES,
    # ``overlay.style`` chose between the old visualizations.  The floating
    # HUD has one shape now, so the key is accepted but ignored.
    "overlay.style": ("hud", "pill", "waveform", "vu_meter"),
    "injector.backend": ("auto", "paste", "wtype", "ydotool", "xdotool"),
    "injector.paste_shortcut": ("auto", "ctrl+v", "ctrl+shift+v"),
    "postprocess.digits": ("keep", "latin", "persian"),
    "translate.backend": ("auto", "services", "googletrans"),
    "log_level": ("DEBUG", "INFO", "WARNING", "ERROR"),
}
#: Palette names are matched case-insensitively, like the theme resolver.
_THEME_NAMES_LOWER = {name.lower() for name in _ENUMS["overlay.theme"]}
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
    "overlay.fade_ms": (0, 1000),
    "overlay.transcript_words": (1, 12),
    "overlay.font_size": (0.0, 72.0),
    "injector.typing_delay_ms": (0, 50),
    "translate.timeout_seconds": (1.0, 120.0),
}


def _fail(path: str, msg: str) -> ConfigError:
    return ConfigError(f"config: '{path}' {msg}")


def _is_theme_path(value: str) -> bool:
    """True when an ``overlay.theme`` value points at a file instead of naming a palette."""
    return value.lower().endswith(".css") or "/" in value or "~" in value


def _check_theme_path(path: str, value: str) -> None:
    """A theme file must exist: a missing file is a config error, not a silent fallback."""
    candidate = Path(value).expanduser()
    if not candidate.is_file():
        raise _fail(path, f"theme file not found: {candidate}")
    if candidate.suffix.lower() != ".css":
        raise _fail(path, f"must be a .css file (got {value!r})")


def _check_accent(path: str, value: str) -> None:
    """``overlay.accent`` is empty (the palette decides) or a hex colour."""
    if not value:
        return
    if not _HEX_COLOR_RE.match(value):
        raise _fail(path, f"must be empty or a hex colour like '#7aa2f7' (got {value!r})")


def _check_language(path: str, value: str) -> None:
    """A language is a code ("en") or a name ("English"); the exact spelling is
    verified later, when py-googletrans is available."""
    text = value.strip()
    if not text:
        raise _fail(path, "must not be empty")
    if not _LANGUAGE_RE.match(text):
        raise _fail(path, f"must be a language code or name like 'en' or 'English' (got {value!r})")


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
        if path == "overlay.theme":
            # a theme is either a palette name (case-insensitive, like the
            # resolver in voicetype.ui.theme) or a path to a CSS file
            if _is_theme_path(value):
                _check_theme_path(path, value)
            elif value.strip().lower() not in _THEME_NAMES_LOWER:
                raise _fail(path, f"must be one of {', '.join(_ENUMS[path])} (got {value!r})")
            kwargs[name] = value
            continue
        if path == "overlay.accent":
            _check_accent(path, value)
        if path in ("translate.language", "translate.source"):
            _check_language(path, value)
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
combo = "super+alt+v"      # used by install-user.sh and the optional evdev listener
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
position = "bottom"
anchor_horizontal = "center"
offset_x = 0
offset_y = 50
theme = "nocturne"         # palette name (nocturne, tokyo-night, nord, …), legacy dark/light, or a .css path
opacity = 0.86
width = 360                # the fixed width of the card
height = 128               # the fixed height of the card
show_partial_text = true
show_audio_meter = true
font = ""                   # empty = the system's own font; e.g. "JetBrains Mono"
font_size = 0.0             # 0 = the system's own size; otherwise in points
style = "hud"              # deprecated: the card has one shape now
animations = true          # master switch for the animated transitions
show_transcript = true     # live word-by-word transcript on the card
transcript_words = 8       # how many words the live preview keeps
accent = ""                # hex accent override, e.g. "#7aa2f7"; "" = palette default
fade_ms = 140              # show/hide fade duration in milliseconds
sounds = false             # audible start/stop/error cues

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

[translate]
enabled = false          # translate the transcript into `language` before injecting it
language = "en"          # target language: a code ("en") or a name ("English")
source = "auto"          # "auto" detects the language you spoke
timeout_seconds = 15.0   # give up on a translation after this many seconds
backend = "auto"         # auto | services | googletrans
"""


def ensure_default_config(path: Path | None = None) -> tuple[Path, bool]:
    """Create a commented sample config if none exists. Returns (path, created)."""
    path = path or default_config_path()
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SAMPLE_CONFIG, encoding="utf-8")
    return path, True


class ConfigRepair(NamedTuple):
    """Result of :func:`repair_config`."""

    path: Path
    status: str  # "created" | "repaired" | "ok"
    backup: Path | None = None


def config_backup_path(path: Path) -> Path:
    """A free backup name next to ``path`` (``config.toml.bak-20260101-120000``)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    n = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    return candidate


def repair_config(path: Path | None = None) -> ConfigRepair:
    """Make sure a *usable* config file exists.

    * missing file  → written from :data:`SAMPLE_CONFIG` (``status="created"``)
    * invalid file  → copied to ``config.toml.bak-<timestamp>`` and replaced by the
      sample (``status="repaired"``) — the user's settings are never thrown away
    * valid file    → left untouched (``status="ok"``)
    """
    path = path or default_config_path()
    if not path.exists():
        ensure_default_config(path)
        return ConfigRepair(path, "created")
    try:
        load_config(path)
    except ConfigError:
        backup = config_backup_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        path.write_text(SAMPLE_CONFIG, encoding="utf-8")
        return ConfigRepair(path, "repaired", backup)
    return ConfigRepair(path, "ok")


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
