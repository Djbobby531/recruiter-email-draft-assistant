"""Section 9: job title extraction variations."""
from __future__ import annotations

import pytest

from app.services.jd_extractor import extract_job_details

TITLE_IN_SUBJECT_CASES = [
    ("Role: Senior Data Engineer Location: Remote", "", "Senior Data Engineer"),
    ("Senior Data Engineer (Databricks)", "", "Senior Data Engineer (Databricks)"),
    ("Sr. Data Engineer", "", "Senior Data Engineer"),
    ("Lead Data Engineer / Databricks", "", "Lead Data Engineer / Databricks"),
    ("Data Engineer – AWS", "", "Data Engineer – AWS"),
]


@pytest.mark.parametrize("subject,body,expected_title", TITLE_IN_SUBJECT_CASES)
def test_title_extracted_from_subject(subject, body, expected_title):
    jd = extract_job_details(text=body, subject=subject)
    assert jd.job_title == expected_title


def test_title_extracted_from_body_when_absent_from_subject():
    jd = extract_job_details(
        text="Role: Senior Data Engineer\nLocation: Remote\nSkills: Python, SQL",
        subject="Great opportunity for you",
    )
    assert jd.job_title == "Senior Data Engineer"


def test_title_extracted_from_forwarded_content():
    body = (
        "James forwarded this to you.\n\n"
        "---------- Forwarded message ---------\n"
        "From: Recruiter <recruiter@example.com>\n"
        "Role: Senior Data Engineer (Databricks)\n"
        "Location: Remote\n"
    )
    jd = extract_job_details(text=body, subject="Fwd: opportunity")
    assert jd.job_title == "Senior Data Engineer (Databricks)"


def test_multiple_title_mentions_uses_first_confident_match():
    body = (
        "Role: Senior Data Engineer (Databricks)\nLocation: Remote\n\n"
        "We previously discussed a Data Analyst role but this JD is different.\n"
    )
    jd = extract_job_details(text=body, subject="")
    assert jd.job_title == "Senior Data Engineer (Databricks)"


def test_title_with_parentheses_keeps_technology_info():
    jd = extract_job_details(text="", subject="Senior Data Engineer (Databricks)")
    assert "(Databricks)" in jd.job_title


def test_title_with_slash_keeps_technology_info():
    jd = extract_job_details(text="", subject="Lead Data Engineer / Databricks")
    assert "Databricks" in jd.job_title


def test_title_with_hyphen_keeps_technology_info():
    jd = extract_job_details(text="", subject="Data Engineer – AWS")
    assert "AWS" in jd.job_title


def test_title_with_location_appended_is_split_out():
    jd = extract_job_details(text="", subject="Senior Data Engineer - Dallas, TX")
    assert jd.job_title == "Senior Data Engineer"
    assert jd.job_location == "Dallas, TX"


def test_title_with_recruiter_formatting_noise_is_cleaned():
    jd = extract_job_details(
        text="",
        subject="Fwd: Now Hiring: Role: Senior Data Engineer (Databricks) Location: Remote",
    )
    assert jd.job_title == "Senior Data Engineer (Databricks)"
    assert "Fwd" not in jd.job_title
    assert "Now Hiring" not in jd.job_title


def test_no_title_present_returns_none_not_fabricated():
    jd = extract_job_details(text="Just checking in, no rush.", subject="Hey")
    assert jd.job_title is None


def test_hash_symbol_stripped_from_title():
    jd = extract_job_details(text="", subject="Role: Senior Data Engineer #1 Location: Remote")
    assert "#" not in jd.job_title
    assert jd.job_title == "Senior Data Engineer 1"


def test_emoji_and_decorative_symbols_stripped_from_title():
    """Real-world regression: a recruiter subject line like
    'URGENT HIRING – Azure AI Engineer 🚨 📩 someone@example.com 🔴 DALLAS, TX'
    must never leak emoji/symbol junk into the title used downstream for the
    resume header, filename, and email subject/body."""
    jd = extract_job_details(
        text="", subject="Role: 🚨 Senior Data Engineer 🔴 Location: Remote",
    )
    assert jd.job_title == "Senior Data Engineer"


class _AIWithSymbolLadenTitle:
    def extract_job_details(self, text):
        return {"job_title": "🚨 Senior #Data Engineer!", "job_location": None, "requirements": []}


def test_ai_provided_title_is_also_cleaned_of_symbols():
    """The AI-extraction fallback path bypasses the deterministic
    `_normalize_title` cleaning entirely - it must still be cleaned before
    being trusted, since AI-extracted titles are exactly as likely to carry
    forward decorative junk from a messy subject line."""
    jd = extract_job_details(
        text="No structured Role:/Location: labels here at all.",
        subject="check this out",
        ai_provider=_AIWithSymbolLadenTitle(),
    )
    assert jd.job_title == "Senior Data Engineer"
