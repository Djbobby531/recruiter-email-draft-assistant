"""Section 10: job location extraction - never inferred, never fabricated."""
from __future__ import annotations

from app.services.jd_extractor import extract_job_details


def test_location_in_subject():
    jd = extract_job_details(text="", subject="Senior Data Engineer – Irvine, CA")
    assert jd.job_location == "Irvine, CA"


def test_location_in_body_labeled():
    jd = extract_job_details(text="Location: Irvine, CA", subject="Senior Data Engineer")
    assert jd.job_location == "Irvine, CA"


def test_multiple_locations_normalized_with_slash():
    jd = extract_job_details(text="Location: Irvine, CA or Los Angeles, CA", subject="")
    assert jd.job_location == "Irvine, CA / Los Angeles, CA"


def test_remote_location():
    jd = extract_job_details(text="Location: Remote", subject="Senior Data Engineer")
    assert jd.job_location == "Remote"


def test_hybrid_location_with_city():
    jd = extract_job_details(text="Location: Hybrid – Dallas, TX", subject="Senior Data Engineer")
    assert "Dallas, TX" in jd.job_location


def test_remote_nationwide_location():
    jd = extract_job_details(text="Location: Remote – US", subject="Senior Data Engineer")
    assert jd.job_location == "Remote – US"


def test_no_location_returns_none_not_fabricated():
    jd = extract_job_details(
        text="Role: Senior Data Engineer\nRequired Skills: Python, SQL, Databricks",
        subject="Senior Data Engineer",
    )
    assert jd.job_location is None


def test_location_never_contains_qualifier_noise():
    jd = extract_job_details(
        text="Location: Irvine, CA or Los Angeles, CA (Hybrid) (Local Preferred)",
        subject="",
    )
    assert "(Hybrid)" not in jd.job_location
    assert "(Local Preferred)" not in jd.job_location
    assert jd.job_location == "Irvine, CA / Los Angeles, CA"


def test_location_is_never_a_fabricated_value_not_present_in_source():
    jd = extract_job_details(
        text="Role: Senior Data Engineer\nRequired Skills: Python, SQL",
        subject="Senior Data Engineer opportunity",
    )
    # no location anywhere in the source - must not invent one, e.g. defaulting
    # to some placeholder like "Remote" or "USA"
    assert jd.job_location is None


def test_regional_preference_sentence_extracts_just_the_named_region():
    """A run-on 'Location:' sentence describing a remote role with a soft
    regional preference must surface the actually-useful place name for the
    subject line, not the whole sentence - this is the exact real-world case
    that originally produced 'Application – X – Onshore United States
    Remote, anyone from Pennsylvania area will be highly preferred.'"""
    jd = extract_job_details(
        text=(
            "Role: Senior Azure Databrick Engineer\n"
            "Location: Onshore United States Remote, anyone from Pennsylvania "
            "area will be highly preferred.\n"
        ),
        subject="",
    )
    assert jd.job_location == "Pennsylvania"


def test_regional_preference_variant_without_area_word():
    jd = extract_job_details(
        text="Location: Remote, candidates from Ohio will be highly preferred.",
        subject="Senior Data Engineer",
    )
    assert jd.job_location == "Ohio"


def test_regional_preference_override_does_not_affect_plain_locations():
    """The override must only ever kick in for an actual '...from X ...
    preferred' sentence - it must never alter a normal, clean location."""
    jd = extract_job_details(text="Location: Irvine, CA or Los Angeles, CA", subject="")
    assert jd.job_location == "Irvine, CA / Los Angeles, CA"
