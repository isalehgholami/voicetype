"""The optional translation feature: config, language resolution and wiring."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from voicetype.config import Config, ConfigError, TranslateConfig, parse_config
from voicetype.daemon import build_components
from voicetype.dispatcher import Components, Dispatcher
from voicetype.postprocess import Pipeline
from voicetype.translate import (
    GoogleTranslator,
    ServiceTranslator,
    build_translator,
    check_translate_config,
    language_label,
    resolve_language,
    resolve_service_language,
    service_language_label,
    translate_fa_to_en,
    translate_stage,
    translate_with_fallback,
)

#: A stand-in for the tables of py-googletrans (the package is optional).
TABLES = (
    {"en": "english", "fa": "persian", "de": "german", "zh-cn": "chinese (simplified)"},
    {"english": "en", "persian": "fa", "german": "de", "chinese (simplified)": "zh-cn"},
)


# ---------------------------------------------------------------- config
def test_translation_is_off_by_default():
    cfg = Config()
    assert cfg.translate.enabled is False
    assert cfg.translate.language == "en"
    assert cfg.translate.source == "auto"


def test_translate_section_is_parsed():
    cfg = parse_config({"translate": {"enabled": True, "language": "German", "source": "fa"}})
    assert cfg.translate.enabled is True
    assert cfg.translate.language == "German"
    assert cfg.translate.source == "fa"


@pytest.mark.parametrize(
    "data,fragment",
    [
        ({"translate": {"enabled": "yes"}}, "translate.enabled"),
        ({"translate": {"language": ""}}, "translate.language"),
        ({"translate": {"language": "!!"}}, "translate.language"),
        ({"translate": {"timeout_seconds": 0}}, "translate.timeout_seconds"),
        ({"translate": {"timeout_seconds": 999}}, "translate.timeout_seconds"),
        ({"translate": {"unknown": 1}}, "translate.unknown"),
    ],
)
def test_bad_translate_options_are_rejected(data, fragment):
    with pytest.raises(ConfigError, match=fragment.replace(".", r"\.")):
        parse_config(data)


# ---------------------------------------------------------------- languages
@pytest.mark.parametrize(
    "value,expected",
    [
        ("en", "en"),
        ("EN", "en"),
        ("english", "en"),
        ("English", "en"),
        ("persian", "fa"),
        ("en-US", "en"),
        ("fa-IR", "fa"),
        ("zh-CN", "zh-cn"),
        ("  german  ", "de"),
    ],
)
def test_resolve_language(value, expected):
    assert resolve_language(value, tables=TABLES) == expected


def test_resolve_source_language_may_be_auto():
    assert resolve_language("auto", path="translate.source", allow_auto=True, tables=TABLES) == "auto"
    assert resolve_language("fa", path="translate.source", allow_auto=True, tables=TABLES) == "fa"


def test_resolve_language_rejects_the_unknown():
    with pytest.raises(ConfigError, match="translate.language"):
        resolve_language("klingon", tables=TABLES)
    with pytest.raises(ConfigError, match="translate.language"):
        resolve_language("", tables=TABLES)


def test_the_language_label_is_the_name_of_the_language():
    assert language_label("de", tables=TABLES) == "GERMAN"
    assert language_label("en", tables=TABLES) == "ENGLISH"
    assert language_label("fa", tables=TABLES) == "PERSIAN"


def test_the_language_label_falls_back_to_the_code():
    assert language_label("xx", tables=TABLES) == "XX"
    assert language_label("", tables=TABLES) == ""


def test_the_language_label_of_a_regional_code():
    assert language_label("de-DE", tables=TABLES) == "GERMAN"


def test_the_translator_carries_the_name_of_its_language():
    translator = GoogleTranslator("de", client_factory=lambda: SimpleNamespace())
    try:
        # the tables of py-googletrans are optional: without them the code
        # itself is the best label there is
        assert translator.label in ("GERMAN", "DE")
    finally:
        translator.close()


def test_build_translator_is_none_when_disabled():
    assert build_translator(TranslateConfig()) is None


def test_build_translator_reports_a_missing_package(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_googletrans(name, *args, **kwargs):
        if name.startswith("googletrans"):
            raise ImportError("no googletrans here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_googletrans)
    with pytest.raises(ConfigError, match="googletrans"):
        build_translator(TranslateConfig(enabled=True, backend="googletrans"))


# ---------------------------------------------------------------- the translator
class FakeClient:
    """Stands in for the py-googletrans client."""

    def __init__(self, text="Hello world.", error=None):
        self.text = text
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    async def translate(self, text, dest=None, src=None):
        self.calls.append((text, dest, src))
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


def test_google_translator_uses_the_configured_languages():
    client = FakeClient()
    translator = GoogleTranslator("en", source="fa", client_factory=lambda: client)
    try:
        assert translator.translate("سلام دنیا") == "Hello world."
        assert client.calls == [("سلام دنیا", "en", "fa")]
    finally:
        translator.close()


def test_google_translator_keeps_the_original_text_on_failure():
    warnings: list[str] = []
    client = FakeClient(error=RuntimeError("no network"))
    translator = GoogleTranslator("en", client_factory=lambda: client, on_warning=warnings.append)
    try:
        assert translator.translate("سلام") == "سلام"
        assert warnings and "no network" in warnings[0]
    finally:
        translator.close()


def test_google_translator_reports_a_timeout():
    warnings: list[str] = []

    class Slow:
        async def translate(self, text, dest=None, src=None):
            import asyncio

            await asyncio.sleep(30)
            return "Hello world."

    translator = GoogleTranslator("en", timeout=0.01, client_factory=lambda: Slow(), on_warning=warnings.append)
    try:
        assert translator.translate("سلام") == "سلام"
        assert warnings and "timed out" in warnings[0]
    finally:
        translator.close()


def test_google_translator_ignores_empty_text():
    client = FakeClient()
    translator = GoogleTranslator("en", client_factory=lambda: client)
    try:
        assert translator.translate("   ") == "   "
        assert client.calls == []
    finally:
        translator.close()


def test_google_translator_falls_back_when_the_answer_is_empty():
    client = FakeClient(text="   ")
    translator = GoogleTranslator("en", client_factory=lambda: client)
    try:
        assert translator.translate("سلام") == "سلام"
    finally:
        translator.close()


# ---------------------------------------------------------------- the text chain
def test_translate_stage_translates_after_the_post_processing():
    seen: list[str] = []

    class Recorder:
        def translate(self, text):
            seen.append(text)
            return "hello world"

    post = Config().postprocess
    pipeline = translate_stage(Pipeline(post), Recorder(), post)
    assert pipeline("سلام دنیا") == "Hello world."  # capitalised and punctuated again
    assert seen == ["سلام دنیا."]  # the Persian clean-up ran first


def test_translate_stage_is_a_no_op_without_a_translator():
    pipeline = Pipeline(Config().postprocess)
    assert translate_stage(pipeline, None, Config().postprocess) is pipeline


def test_translate_stage_keeps_a_spoken_command():
    class Recorder:
        def translate(self, text):  # pragma: no cover - must never be called
            raise AssertionError("a command must not be translated")

    post = dataclasses.replace(Config().postprocess, voice_commands=True)
    pipeline = translate_stage(Pipeline(post), Recorder(), post)
    assert pipeline("خط جدید") == "\n"


# ---------------------------------------------------------------- the daemon wiring
def test_build_components_wires_the_translator(monkeypatch):
    created = []

    def fake_build(cfg, **kwargs):
        translator = SimpleNamespace(translate=lambda t: "Hello", close=lambda: None)
        created.append((cfg, kwargs, translator))
        return translator

    monkeypatch.setattr("voicetype.daemon.build_translator", fake_build)
    cfg = dataclasses.replace(Config(), translate=TranslateConfig(enabled=True))
    comps = build_components(cfg)
    assert created and comps.translator is created[0][2]
    assert comps.pipeline("سلام") == "Hello."  # the sentence-final mark is added again
    assert created[0][1]["proxy"] == ""


def test_build_components_without_translation_has_no_translator():
    comps = build_components(Config())
    assert comps.translator is None
    assert comps.pipeline("سلام دنیا") == Pipeline(Config().postprocess)("سلام دنیا")


def test_the_old_translator_is_closed_on_reload():
    closed: list[str] = []

    class FakeTranslator:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            closed.append(self.name)

    backend = SimpleNamespace(name="x", close=lambda: None)
    old = Components(Config(), backend, None, lambda t: t, FakeTranslator("old"))
    new = Components(Config(), backend, None, lambda t: t, FakeTranslator("new"))
    d = Dispatcher(old, lambda *a, **k: None, build=lambda cfg: new)
    assert d.reload_config(Config()) == "ok reloaded"
    assert closed == ["old"]
    d.shutdown()
    assert closed == ["old", "new"]


# ---------------------------------------------------------------- the cli
@pytest.fixture
def fake_translator(monkeypatch):
    """A translator that works without the network or the optional package."""
    problems: list[str] = []

    class Fake:
        def __init__(self, *args, **kwargs):
            self.on_warning = kwargs.get("on_warning")

        def translate(self, text):
            if problems:
                if self.on_warning is not None:
                    self.on_warning(problems[0])
                return text
            return "Hello world."

        def close(self):
            pass

    monkeypatch.setattr("voicetype.translate.GoogleTranslator", Fake)
    monkeypatch.setattr("voicetype.translate.resolve_language", lambda value, **kw: "en")
    # the fallback chain needs its optional package — the fake stands in for it
    monkeypatch.setattr("voicetype.translate.services_available", lambda: False)
    return problems


def test_cli_translate(tmp_path, monkeypatch, capsys, fake_translator):
    from voicetype import cli

    monkeypatch.setenv("VOICETYPE_CONFIG", str(tmp_path / "c.toml"))
    (tmp_path / "c.toml").write_text('[translate]\nlanguage = "en"\n', encoding="utf-8")
    assert cli.main(["translate", "سلام دنیا"]) == 0
    assert "Hello world." in capsys.readouterr().out


def test_cli_translate_reports_a_failure(tmp_path, monkeypatch, capsys, fake_translator):
    from voicetype import cli

    fake_translator.append("translation failed: no network")
    monkeypatch.setenv("VOICETYPE_CONFIG", str(tmp_path / "c.toml"))
    (tmp_path / "c.toml").write_text('[translate]\nlanguage = "en"\n', encoding="utf-8")
    assert cli.main(["translate", "سلام دنیا"]) == 1
    assert "no network" in capsys.readouterr().err


def test_cli_config_check_rejects_an_unknown_language(tmp_path, monkeypatch, capsys):
    from voicetype import cli

    def unknown(value, **kwargs):
        raise ConfigError("config: 'translate.language' is not a known language: 'klingon'")

    monkeypatch.setattr("voicetype.translate.resolve_language", unknown)
    monkeypatch.setenv("VOICETYPE_CONFIG", str(tmp_path / "c.toml"))
    (tmp_path / "c.toml").write_text('[translate]\nenabled = true\nlanguage = "klingon"\n', encoding="utf-8")
    assert cli.main(["config", "check"]) == 2
    assert "translate.language" in capsys.readouterr().err


# ---------------------------------------------------------------- the fallback chain
def test_the_services_are_tried_in_order():
    calls: list[str] = []

    def fake(text, **kwargs):
        calls.append(kwargs["translator"])
        return "Hello world."

    assert translate_with_fallback("سلام", "fa", "en", translate=fake) == "Hello world."
    assert calls == ["google"]


def test_a_failing_service_is_skipped():
    calls: list[str] = []

    def fake(text, **kwargs):
        calls.append(kwargs["translator"])
        if kwargs["translator"] == "google":
            raise RuntimeError("Unexpected status code 429")
        return "Hello world."

    assert translate_with_fallback("سلام", "fa", "en", translate=fake) == "Hello world."
    assert calls == ["google", "bing"]


def test_every_service_failing_keeps_the_original_text():
    warnings: list[str] = []

    def fake(text, **kwargs):
        raise RuntimeError("Unexpected status code 429")

    out = translate_with_fallback("سلام", "fa", "en", translate=fake, on_warning=warnings.append)
    assert out == "سلام"
    assert warnings and "429" in warnings[0]


def test_an_empty_answer_goes_to_the_next_service():
    calls: list[str] = []

    def fake(text, **kwargs):
        calls.append(kwargs["translator"])
        if kwargs["translator"] == "google":
            return "   "
        return "Hello world."

    assert translate_with_fallback("سلام", "fa", "en", translate=fake) == "Hello world."
    assert calls == ["google", "bing"]


def test_the_languages_are_forwarded():
    seen: dict = {}

    def fake(text, **kwargs):
        seen.update(kwargs)
        return "Hello."

    translate_with_fallback("سلام", "fa", "en", translate=fake)
    assert seen["from_language"] == "fa"
    assert seen["to_language"] == "en"


def test_the_services_share_the_time_budget():
    seen: list[float] = []

    def fake(text, **kwargs):
        seen.append(kwargs["timeout"])
        raise RuntimeError("nope")

    translate_with_fallback("سلام", "fa", "en", translate=fake, timeout=5.0)
    assert len(seen) == 4  # every service is tried
    assert all(t <= 5.0 for t in seen)  # nobody gets more than the budget
    assert seen[-1] <= seen[0]  # the budget shrinks


def test_an_empty_text_is_never_translated():
    def fake(text, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("an empty text must not be translated")

    assert translate_with_fallback("", "fa", "en", translate=fake) == ""


# ---------------------------------------------------------------- the service translator
def test_the_service_translator_keeps_the_original_text_on_failure():
    warnings: list[str] = []

    def fake(text, **kwargs):
        raise RuntimeError("no network")

    translator = ServiceTranslator("en", source="fa", translate=fake, on_warning=warnings.append)
    assert translator.translate("سلام") == "سلام"
    assert warnings and "no network" in warnings[0]


def test_the_service_translator_translates():
    def fake(text, **kwargs):
        return "Hello, how are you?"

    translator = ServiceTranslator("en", source="fa", translate=fake)
    assert translator.translate("سلام") == "Hello, how are you?"


def test_the_service_translator_carries_the_name_of_its_language():
    assert ServiceTranslator("en").label == "ENGLISH"
    assert ServiceTranslator("fa").label == "PERSIAN"


def test_the_service_translator_ignores_an_empty_text():
    calls: list[str] = []

    def fake(text, **kwargs):
        calls.append(text)
        return "Hello."

    translator = ServiceTranslator("en", translate=fake)
    assert translator.translate("   ") == "   "
    assert calls == []


# ---------------------------------------------------------------- the utility
def test_translate_fa_to_en_is_empty_for_an_empty_text():
    assert translate_fa_to_en("") == ""
    assert translate_fa_to_en(None) == ""


def test_translate_fa_to_en_uses_the_fallback_chain(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def fake(text, **kwargs):
        calls.append((kwargs["translator"], kwargs["from_language"], kwargs["to_language"]))
        if kwargs["translator"] == "google":
            raise RuntimeError("Unexpected status code 429")
        return "Hello, how are you?"

    monkeypatch.setattr("voicetype.translate.services._default_translate", fake)
    assert translate_fa_to_en("سلام، حال شما چطور است؟") == "Hello, how are you?"
    assert calls[0] == ("google", "fa", "en")
    assert calls[1] == ("bing", "fa", "en")


# ---------------------------------------------------------------- the languages of the services
@pytest.mark.parametrize(
    "value,expected",
    [
        ("en", "en"),
        ("EN", "en"),
        ("english", "en"),
        ("English", "en"),
        ("persian", "fa"),
        ("fa-IR", "fa"),
        ("zh-CN", "zh"),
        ("zh-TW", "zh-Hant"),
        ("zh-Hant", "zh-Hant"),
        ("german", "de"),
    ],
)
def test_resolve_service_language(value, expected):
    assert resolve_service_language(value) == expected


def test_resolve_service_language_may_be_auto():
    assert resolve_service_language("auto", path="translate.source", allow_auto=True) == "auto"


def test_resolve_service_language_rejects_the_unknown():
    with pytest.raises(ConfigError, match="translate.language"):
        resolve_service_language("klingon")
    with pytest.raises(ConfigError, match="translate.language"):
        resolve_service_language("")


def test_the_service_language_label():
    assert service_language_label("en") == "ENGLISH"
    assert service_language_label("fa") == "PERSIAN"
    assert service_language_label("xx") == "XX"
    assert service_language_label("") == ""


# ---------------------------------------------------------------- the backend
def test_the_translate_backend_is_parsed():
    cfg = parse_config({"translate": {"enabled": True, "backend": "services"}})
    assert cfg.translate.backend == "services"


def test_an_unknown_translate_backend_is_rejected():
    with pytest.raises(ConfigError, match="translate.backend"):
        parse_config({"translate": {"backend": "magic"}})


def test_build_translator_prefers_the_fallback_chain(monkeypatch):
    monkeypatch.setattr("voicetype.translate.services_available", lambda: True)
    translator = build_translator(TranslateConfig(enabled=True))
    try:
        assert isinstance(translator, ServiceTranslator)
    finally:
        translator.close()


def test_build_translator_falls_back_to_googletrans(monkeypatch):
    monkeypatch.setattr("voicetype.translate.services_available", lambda: False)
    translator = build_translator(TranslateConfig(enabled=True))
    try:
        assert isinstance(translator, GoogleTranslator)
    finally:
        translator.close()


def test_build_translator_reports_a_missing_services_package(monkeypatch):
    monkeypatch.setattr("voicetype.translate.services_available", lambda: False)
    with pytest.raises(ConfigError, match="translators"):
        build_translator(TranslateConfig(enabled=True, backend="services"))


def test_check_translate_config_accepts_a_known_language():
    check_translate_config(TranslateConfig(enabled=True, language="persian"))


def test_check_translate_config_rejects_an_unknown_language():
    with pytest.raises(ConfigError, match="translate.language"):
        check_translate_config(TranslateConfig(enabled=True, language="klingon", backend="services"))


def test_cli_translate_uses_the_fallback_chain(tmp_path, monkeypatch, capsys):
    from voicetype import cli

    class Fake:
        def __init__(self, *args, **kwargs):
            self.on_warning = kwargs.get("on_warning")

        def translate(self, text):
            return "Hello world."

        def close(self):
            pass

    monkeypatch.setattr("voicetype.translate.services_available", lambda: True)
    monkeypatch.setattr("voicetype.translate.ServiceTranslator", Fake)
    monkeypatch.setenv("VOICETYPE_CONFIG", str(tmp_path / "c.toml"))
    (tmp_path / "c.toml").write_text('[translate]\nlanguage = "en"\n', encoding="utf-8")
    assert cli.main(["translate", "سلام دنیا"]) == 0
    assert "Hello world." in capsys.readouterr().out
