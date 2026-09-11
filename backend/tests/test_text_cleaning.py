from __future__ import annotations

from app.utils.text_cleaning import clean_role_text


def test_strips_hash_symbol_from_role():
    assert clean_role_text("Senior Data Engineer #1") == "Senior Data Engineer 1"


def test_strips_emoji_from_role():
    assert clean_role_text("🚨 Urgent Data Engineer 🔴") == "Urgent Data Engineer"


def test_strips_at_symbol_and_other_decorative_punctuation():
    assert clean_role_text('Data Engineer @ "Top" Client!') == "Data Engineer Top Client"


def test_keeps_meaningful_title_punctuation():
    assert clean_role_text("Senior Data Engineer - dbt") == "Senior Data Engineer - dbt"
    assert clean_role_text("Data Engineer, AWS & Azure") == "Data Engineer, AWS & Azure"
    assert clean_role_text("C++ Developer") == "C++ Developer"


def test_none_and_empty_pass_through_unchanged():
    assert clean_role_text(None) is None
    assert clean_role_text("") == ""


def test_returns_none_when_nothing_meaningful_survives():
    assert clean_role_text("#@!*") is None
