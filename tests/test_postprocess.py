import pytest

from voicetype.config import ConfigError, PostprocessConfig
from voicetype.postprocess import Pipeline
from voicetype.postprocess.commands import match_command
from voicetype.postprocess.hallucination import is_hallucination
from voicetype.postprocess.persian import ZWNJ, convert_digits, normalize_zwnj, persian_punctuation
from voicetype.postprocess.pipeline import auto_punctuate, capitalize_first
from voicetype.postprocess.replacements import apply_replacements, compile_replacements
from voicetype.postprocess.terms import TERMS, apply_terms

Z = ZWNJ

# ---- ZWNJ: 50+ sentences ------------------------------------------------------------
MI = [  # (input, expected)
    ("او می رود", f"او می{Z}رود"),
    ("این می شود", f"این می{Z}شود"),
    ("من نمی دانم", f"من نمی{Z}دانم"),
    ("آنها می خواهند بیایند", f"آنها می{Z}خواهند بیایند"),
    ("ما می توانیم", f"ما می{Z}توانیم"),
    ("تو می گویی", f"تو می{Z}گویی"),
    ("او نمی تواند", f"او نمی{Z}تواند"),
    ("می خورم", f"می{Z}خورم"),
    ("همیشه می آید", f"همیشه می{Z}آید"),
    ("نمی خواهم", f"نمی{Z}خواهم"),
    ("دیروز می گفت", f"دیروز می{Z}گفت"),
    ("چرا نمی آیی", f"چرا نمی{Z}آیی"),
    ("می کنم", f"می{Z}کنم"),
    ("می دانستم", f"می{Z}دانستم"),
    ("کسی نمی داند", f"کسی نمی{Z}داند"),
]
SUFFIX = [
    ("کتاب ها", f"کتاب{Z}ها"),
    ("خانه ها", f"خانه{Z}ها"),
    ("درخت های بزرگ", f"درخت{Z}های بزرگ"),
    ("بزرگ تر", f"بزرگ{Z}تر"),
    ("کوچک تر", f"کوچک{Z}تر"),
    ("بهترین", "بهترین"),
    ("خوب ترین", f"خوب{Z}ترین"),
    ("مدرسه ها", f"مدرسه{Z}ها"),
    ("دوست ها", f"دوست{Z}ها"),
    ("برنامه ها", f"برنامه{Z}ها"),
    ("فایل ها", f"فایل{Z}ها"),
    ("سرور ها", f"سرور{Z}ها"),
    ("کتاب هایی", f"کتاب{Z}هایی"),
    ("بزرگ تری", f"بزرگ{Z}تری"),
    ("پنجره ها", f"پنجره{Z}ها"),
]
UNCHANGED = [
    "سلام دنیا",
    "امروز هوا خوب است",
    "من به خانه رفتم",
    "میز بزرگ است",  # 'میز' must not be split
    "میوه خوشمزه",
    "او تر است",  # protected pronoun case
    "hello world",
    "من ها",  # protected
    "این یک آزمایش است",
    "ما همیشه اینجاییم",
    "تو کجایی",
    "کتاب",
    "نمیرم",  # already joined without space: untouched
    "می‌شود",  # already correct
    "کتاب‌ها",
]


@pytest.mark.parametrize("raw,expected", MI + SUFFIX)
def test_zwnj_rules(raw, expected):
    assert normalize_zwnj(raw) == expected


@pytest.mark.parametrize("text", UNCHANGED)
def test_zwnj_leaves_other_text_alone(text):
    assert normalize_zwnj(text) == text


def test_zwnj_corpus_size():
    assert len(MI) + len(SUFFIX) + len(UNCHANGED) >= 45


def test_zwnj_idempotent():
    for raw, _ in MI + SUFFIX:
        once = normalize_zwnj(raw)
        assert normalize_zwnj(once) == once


# ---- digits & punctuation -----------------------------------------------------------
def test_digits():
    assert convert_digits("۱۲۳ و ٤٥٦ و 789", "latin") == "123 و 456 و 789"
    assert convert_digits("123 و 456", "persian") == "۱۲۳ و ۴۵۶"
    assert convert_digits("۱ 2", "keep") == "۱ 2"


def test_persian_punctuation_only_for_persian_text():
    assert persian_punctuation("سلام, چطوری? خوب; بله") == "سلام، چطوری؟ خوب؛ بله"
    assert persian_punctuation("hello, how are you?") == "hello, how are you?"
    assert persian_punctuation("قیمت 3,500 تومان") == "قیمت 3,500 تومان"


# ---- english terms ------------------------------------------------------------------
def test_at_least_30_terms():
    assert len(TERMS) >= 30


@pytest.mark.parametrize("canonical,fa", [(c, p) for c, ps in TERMS.items() for p in ps])
def test_persian_transliteration_to_latin(canonical, fa):
    assert apply_terms(f"من از {fa} استفاده می‌کنم") == f"من از {canonical} استفاده می‌کنم"


@pytest.mark.parametrize("canonical", list(TERMS))
def test_latin_case_normalized(canonical):
    assert apply_terms(f"use {canonical.lower()} here") == f"use {canonical} here"
    assert apply_terms(f"use {canonical.upper()} here") == f"use {canonical} here"


def test_terms_respect_word_boundaries():
    assert apply_terms("پایتونی") == "پایتونی"  # suffix attached → not a standalone term
    assert apply_terms("GitHub و گیت‌هاب و گیت") == "GitHub و GitHub و Git"
    assert apply_terms("javascript و java") == "JavaScript و Java"
    assert apply_terms("pythonic") == "pythonic"


# ---- hallucination ------------------------------------------------------------------
@pytest.mark.parametrize("t", ["", "  ", "...", "؟", "Thanks for watching!", "thank you.", "[BLANK_AUDIO]", "a a a a a a a a"])
def test_hallucinations_detected(t):
    assert is_hallucination(t)


@pytest.mark.parametrize("t", ["سلام دنیا", "thank you for the help with Python", "yes"])
def test_real_text_not_flagged(t):
    assert not is_hallucination(t)


# ---- replacements -------------------------------------------------------------------
def test_literal_and_regex_replacements():
    rules = compile_replacements((("می شود", f"می{Z}شود"), ("re:\\bqt\\d\\b", "Qt")))
    assert apply_replacements("این می شود و qt6 بود", rules) == f"این می{Z}شود و Qt بود"


def test_literal_replacement_value_with_backslash_is_safe():
    rules = compile_replacements((("a", "\\1"),))
    assert apply_replacements("xa", rules) == "x\\1"


@pytest.mark.parametrize("bad", ["re:(a+)+$", "re:" + "a" * 201, "re:(unclosed"])
def test_unsafe_regex_rejected(bad):
    with pytest.raises(ConfigError):
        compile_replacements(((bad, "x"),))


# ---- capitalization / punctuation ---------------------------------------------------
def test_capitalize_and_auto_punctuate():
    assert capitalize_first("hello world") == "Hello world"
    assert capitalize_first("سلام") == "سلام"
    assert capitalize_first("3 apples") == "3 apples"
    assert auto_punctuate("سلام") == "سلام."
    assert auto_punctuate("سلام؟") == "سلام؟"
    assert auto_punctuate("hi!") == "hi!"
    assert auto_punctuate("line\n") == "line\n"


# ---- pipeline -----------------------------------------------------------------------
def pipe(**kw):
    return Pipeline(PostprocessConfig(**kw))


def test_pipeline_end_to_end():
    out = pipe()("  من از پایتون و qt استفاده می کنم  ")
    assert out == f"من از Python و Qt استفاده می{Z}کنم."


def test_every_stage_can_be_disabled():
    p = pipe(
        capitalize_first=False,
        auto_punctuate=False,
        normalize_zwnj=False,
        persian_punctuation=False,
        english_terms=False,
        filter_hallucinations=False,
    )
    assert p.stage_names == ["strip"]
    assert p(" می رود python ") == "می رود python"


def test_pipeline_drops_hallucination():
    assert pipe()("Thanks for watching") == ""
    assert pipe(filter_hallucinations=False)("thanks for watching") == "Thanks for watching."


def test_pipeline_digits_and_punctuation():
    assert pipe(digits="latin", auto_punctuate=False)("قیمت ۱۲۳ تومان, خوب?") == "قیمت 123 تومان، خوب؟"


def test_pipeline_custom_replacements():
    p = pipe(custom_replacements=(("نکته:", "نکته‌ی:"),), auto_punctuate=False)
    assert p("نکته: مهم") == "نکته‌ی: مهم"


def test_voice_commands_only_when_enabled_and_whole_utterance():
    assert match_command("خط جدید") == "\n"
    assert match_command("پاک کن") == ""
    assert match_command("Question Mark") == "?"
    assert match_command("این یک نقطه است") is None
    on, off = pipe(voice_commands=True), pipe()
    assert on("خط جدید") == "\n"
    assert on("نقطه") == "."
    assert on("پاک کن") == ""
    assert on("یک نقطه روی نقشه") != "."
    assert off("نقطه") == "نقطه."
    assert len([c for c in ("خط جدید", "نقطه", "ویرگول", "علامت سوال", "پاک کن") if match_command(c) is not None]) >= 4
