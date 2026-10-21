import dataclasses

import pytest

from voicetype.config import (
    SAMPLE_CONFIG,
    Config,
    ConfigError,
    ConfigManager,
    ensure_default_config,
    load_config,
    parse_config,
    repair_config,
)


def write(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_partial_file_overrides_only_given_keys(tmp_path):
    cfg = load_config(write(tmp_path, '[stt]\nlanguage = "en-US"\n[overlay]\nopacity = 0.5\n'))
    assert cfg.stt.language == "en-US"
    assert cfg.stt.provider == "google_free"
    assert cfg.overlay.opacity == 0.5
    assert cfg.overlay.width == 360


def test_nested_tables(tmp_path):
    cfg = load_config(write(tmp_path, '[stt.vosk]\nmodel_path = "/x/y"\n[stt.whisper]\ndevice = "cuda"\n'))
    assert cfg.stt.vosk.model_path == "/x/y"
    assert cfg.stt.whisper.device == "cuda"


def test_custom_replacements(tmp_path):
    cfg = load_config(write(tmp_path, '[postprocess.custom_replacements]\n"a" = "b"\n'))
    assert cfg.postprocess.custom_replacements == (("a", "b"),)


def test_sample_config_is_valid(tmp_path):
    assert load_config(write(tmp_path, SAMPLE_CONFIG)) == Config()


@pytest.mark.parametrize(
    "text,fragment",
    [
        ('[stt]\nprovider = "nope"\n', "stt.provider"),
        ("[overlay]\nopacity = 2\n", "overlay.opacity"),
        ('[overlay]\nwidth = "wide"\n', "overlay.width"),
        ("[hotkey]\nescape_stops = 1\n", "hotkey.escape_stops"),
        ("[bogus]\nx = 1\n", "bogus"),
        ('[stt]\nunknown_key = 1\n', "stt.unknown_key"),
        ('[stt]\nprovider = "google_cloud"\n', "keyfile"),
        ('[stt]\nfallback_provider = "zzz"\n', "fallback_provider"),
        ("[postprocess.custom_replacements]\na = 1\n", "custom_replacements"),
        ('[audio]\nbackend = "jack"\n', "audio.backend"),
        ("this is = not toml [", "not valid TOML"),
    ],
)
def test_invalid_values_give_readable_errors(tmp_path, text, fragment):
    with pytest.raises(ConfigError) as ei:
        load_config(write(tmp_path, text))
    assert fragment in str(ei.value)


def test_int_accepted_for_float(tmp_path):
    cfg = load_config(write(tmp_path, "[stt]\nsilence_threshold_seconds = 5\n"))
    assert cfg.stt.silence_threshold_seconds == 5.0


def test_env_override(tmp_path, monkeypatch):
    p = write(tmp_path, '[stt]\nlanguage = "de-DE"\n')
    monkeypatch.setenv("VOICETYPE_CONFIG", str(p))
    assert ConfigManager().get().stt.language == "de-DE"


def test_ensure_default_config_creates_once(tmp_path):
    p = tmp_path / "sub" / "config.toml"
    path, created = ensure_default_config(p)
    assert created and path.exists()
    _, created2 = ensure_default_config(p)
    assert not created2


def test_repair_config_creates_a_missing_file(tmp_path):
    p = tmp_path / "config.toml"
    result = repair_config(p)
    assert result.status == "created"
    assert result.backup is None
    assert p.read_text(encoding="utf-8") == SAMPLE_CONFIG


def test_repair_config_keeps_a_valid_file(tmp_path):
    p = write(tmp_path, '[stt]\nlanguage = "en-US"\n')
    result = repair_config(p)
    assert result.status == "ok"
    assert result.backup is None
    assert p.read_text(encoding="utf-8") == '[stt]\nlanguage = "en-US"\n'


def test_repair_config_backs_up_an_invalid_file(tmp_path):
    p = write(tmp_path, "not = valid = toml [")
    result = repair_config(p)
    assert result.status == "repaired"
    assert result.backup is not None and result.backup.exists()
    assert result.backup.read_text(encoding="utf-8") == "not = valid = toml ["
    assert p.read_text(encoding="utf-8") == SAMPLE_CONFIG
    assert load_config(p) == Config()


def test_repair_config_backups_do_not_overwrite_each_other(tmp_path):
    p = write(tmp_path, "broken [")
    first = repair_config(p)
    write(tmp_path, "broken [")
    second = repair_config(p)
    assert first.backup != second.backup
    assert first.backup.exists() and second.backup.exists()


def test_reload_keeps_old_on_error(tmp_path):
    p = write(tmp_path, '[stt]\nlanguage = "en-US"\n')
    mgr = ConfigManager(p)
    assert mgr.get().stt.language == "en-US"
    p.write_text('[stt]\nprovider = "bad"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        mgr.reload()
    assert mgr.get().stt.language == "en-US"
    p.write_text('[stt]\nlanguage = "fr-FR"\n', encoding="utf-8")
    assert mgr.reload().stt.language == "fr-FR"


def test_config_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Config().log_level = "DEBUG"  # type: ignore[misc]


def test_parse_config_dict():
    assert parse_config({"stt": {"language": "en-GB"}}).stt.language == "en-GB"
