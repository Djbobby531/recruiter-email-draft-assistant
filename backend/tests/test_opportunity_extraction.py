"""
Section 10-12 / 29: local-candidate requirement, implementation partner, and
end-client extraction. Every "must never" rule gets an explicit test.
"""
from __future__ import annotations

import pytest

from app.services.jd_extractor import (
    extract_end_client,
    extract_implementation_partner,
    extract_local_requirement,
)

# --- Local requirement --------------------------------------------------

LOCAL_CASES = [
    ("Local candidates only, no exceptions.", "YES"),
    ("Only local candidates will be considered.", "YES"),
    ("Candidates must be local to Dallas, TX.", "YES"),
    ("This role requires candidates local to the area - no relocation.", "YES"),
    ("Local candidates preferred but not required.", "PREFERRED"),
    ("We prefer local candidates for this role.", "PREFERRED"),
    ("This is a remote position, work from anywhere.", "NO"),
    ("100% remote role, no travel required.", "NO"),
    ("Location: Remote", "NO"),
    ("Role: Data Engineer\nLocation: Chicago, IL\nSkills: Python, SQL", "UNKNOWN"),
    ("", "UNKNOWN"),
]


@pytest.mark.parametrize("text,expected", LOCAL_CASES)
def test_extract_local_requirement(text, expected):
    assert extract_local_requirement(text) == expected


def test_local_requirement_never_uses_candidate_location():
    # There is no candidate-location concept passed into this function at
    # all - it can only ever read the JD/recruiter text itself.
    text = "Location: Irvine, CA\nOnsite role, local candidates preferred."
    assert extract_local_requirement(text) == "PREFERRED"


def test_local_requirement_does_not_confuse_job_location_with_local_preference():
    text = "Location: Dallas, TX\nThis is an onsite position with a great team."
    assert extract_local_requirement(text) == "UNKNOWN"


# --- Implementation partner ----------------------------------------------


def test_implementation_partner_explicit_label():
    text = "Role: Data Engineer\nImplementation Partner: PAMTEN\nLocation: Dallas, TX"
    assert extract_implementation_partner(text) == "PAMTEN"


def test_implementation_partner_vendor_label():
    text = "Vendor: Algebra IT LLC\nRole: Data Engineer"
    assert extract_implementation_partner(text) == "Algebra IT LLC"


def test_implementation_partner_falls_back_to_recruiter_company():
    text = "Role: Data Engineer\nLocation: Dallas, TX"
    assert extract_implementation_partner(text, fallback_company="PAMTEN") == "PAMTEN"


def test_implementation_partner_unknown_when_nothing_available():
    text = "Role: Data Engineer\nLocation: Dallas, TX"
    assert extract_implementation_partner(text, fallback_company=None) is None


def test_implementation_partner_ignores_non_answer_placeholder():
    text = "Implementation Partner: TBD\nRole: Data Engineer"
    assert extract_implementation_partner(text, fallback_company="Fallback Co") == "Fallback Co"


# --- End client ------------------------------------------------------------


def test_end_client_explicit_label():
    text = "Implementation Partner: PAMTEN\nEnd Client: ABC Financial\nRole: Data Engineer"
    assert extract_end_client(text) == "ABC Financial"


def test_end_client_bare_client_label():
    text = "Client: JPMorgan\nRole: Data Engineer"
    assert extract_end_client(text) == "JPMorgan"


def test_end_client_never_inferred_from_prose_mention():
    """Section 29's explicit false-positive: mentioning a company mid-sentence
    must NEVER be read as an end-client declaration."""
    text = "Our recruiter works with JPMorgan on several roles.\nRole: Data Engineer"
    assert extract_end_client(text) is None


def test_end_client_never_assumes_recruiter_company_is_the_client():
    text = "Implementation Partner: PAMTEN\nRole: Data Engineer\nLocation: Irvine, CA"
    assert extract_end_client(text) is None


def test_end_client_unknown_when_absent():
    assert extract_end_client("Role: Data Engineer\nLocation: Remote") is None


def test_end_client_ignores_non_answer_placeholder():
    text = "End Client: Confidential\nRole: Data Engineer"
    assert extract_end_client(text) is None
