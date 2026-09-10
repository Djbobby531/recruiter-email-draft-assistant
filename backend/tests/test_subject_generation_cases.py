"""Section 11: generated subject line tests.

Format: "Application for [Job Title] - [City, ST]" - a single, concise
location (never a joined list of every location the JD mentions), and no
trailing " - [location]" segment at all when the JD states none.
"""
from __future__ import annotations

from app.services import draft_service


def test_subject_job_and_single_location():
    assert draft_service.generate_subject("Senior Data Engineer", "Dallas, TX") == \
        "Application for Senior Data Engineer - Dallas, TX"


def test_subject_job_with_dual_location_shows_only_the_first():
    assert draft_service.generate_subject(
        "Senior Data Engineer (Databricks)", "Irvine, CA / Los Angeles, CA"
    ) == "Application for Senior Data Engineer (Databricks) - Irvine, CA"


def test_subject_no_location_omits_the_location_segment_entirely():
    subject = draft_service.generate_subject("Senior Data Engineer", None)
    assert subject == "Application for Senior Data Engineer"
    assert "-" not in subject
    assert "None" not in subject


def test_subject_handles_special_characters_in_title():
    subject = draft_service.generate_subject("Data Engineer (AWS/Azure) - C2C", "Remote")
    assert subject.startswith("Application for Data Engineer (AWS/Azure) - C2C")
    assert subject.endswith("Remote")


def test_subject_handles_long_title_without_truncation():
    long_title = "Senior Principal Staff Data Engineering Architect for Large Scale Distributed Systems"
    subject = draft_service.generate_subject(long_title, "Remote")
    assert long_title in subject


def test_subject_does_not_duplicate_location_phrase():
    subject = draft_service.generate_subject("Senior Data Engineer", "Remote")
    assert subject.count("Remote") == 1


def test_subject_from_messy_original_subject_is_regenerated_cleanly():
    # even if the *original* incoming subject was messy noise, the generated
    # subject must always be built from the clean extracted title/location -
    # never a pass-through of the raw original subject.
    messy_original = "Fw: Hiring for || Role: Senior Data Engineer (Databricks) Location: Irvine, CA ||"
    subject = draft_service.generate_subject("Senior Data Engineer (Databricks)", "Irvine, CA")
    assert subject != messy_original
    assert "||" not in subject
    assert "Fw:" not in subject
