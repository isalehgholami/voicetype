"""Tests for the text direction helpers (UAX #9 first-strong-character rules)."""

from voicetype.text import (
    LTR,
    RTL,
    base_direction,
    first_strong_direction,
    is_rtl,
    word_direction,
    word_directions,
)


def test_persian_and_arabic_run_from_the_right():
    assert base_direction("سلام دنیا") == RTL
    assert base_direction("مرحبا بالعالم") == RTL
    assert is_rtl("سلام") is True


def test_hebrew_runs_from_the_right():
    assert base_direction("שלום עולם") == RTL


def test_english_runs_from_the_left():
    assert base_direction("hello world") == LTR
    assert is_rtl("hello world") is False


def test_the_first_strong_character_decides_the_direction():
    # a digit or a punctuation mark before the first letter is ignored
    assert base_direction("123 سلام") == RTL
    assert base_direction("... hello") == LTR
    # the first strong character wins, not the majority
    assert base_direction("سلام hello") == RTL


def test_a_text_without_a_strong_character_falls_back_to_left_to_right():
    assert base_direction("123 456") == LTR
    assert base_direction("") == LTR
    assert first_strong_direction("123") is None
    assert first_strong_direction("سلام") == RTL


def test_a_word_without_a_strong_character_inherits_the_paragraph_direction():
    assert word_direction("سلام", LTR) == RTL
    assert word_direction("hello", RTL) == LTR
    assert word_direction("123", RTL) == RTL
    assert word_direction("2024") == LTR


def test_word_directions_maps_every_word():
    words = ["من", "با", "Python", "code", "123"]
    assert word_directions(words, RTL) == [RTL, RTL, LTR, LTR, RTL]
    assert word_directions(["hello", "world"], LTR) == [LTR, LTR]


def test_the_marks_that_force_a_direction_are_strong_characters():
    # U+200F RIGHT-TO-LEFT MARK and U+200E LEFT-TO-RIGHT MARK
    assert first_strong_direction("‏hello") == RTL
    assert first_strong_direction("‎سلام") == LTR
