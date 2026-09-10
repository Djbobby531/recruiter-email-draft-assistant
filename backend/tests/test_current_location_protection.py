"""
Section 13: explicit regression tests for the current-location protection
guarantee. The candidate profile has no location field at all, so the
generated email cannot leak a current location - this is verified
structurally (the model/schema has no such field) and behaviorally (the
generated text never contains a location string that wasn't in the JD).
"""
from __future__ import annotations

import inspect

from app.models import CandidateProfile
from app.schemas import CandidateProfileIn
from app.services import draft_service
from app.services.pipeline import process_message
from tests.conftest import make_raw_message, make_resume
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT

FORBIDDEN_LOCATION_STRINGS = [
    "San Francisco", "New York", "Austin", "Seattle", "Chicago", "Boston",
    "Denver", "Miami", "Portland", "current location", "currently based",
    "currently located", "currently residing", "I live in", "I am based in",
    "based in California", "IP address", "your location",
]


def test_candidate_profile_model_has_no_location_field():
    columns = {c.name for c in CandidateProfile.__table__.columns}
    for forbidden_field in ("location", "current_location", "city", "current_city", "address"):
        assert forbidden_field not in columns


def test_candidate_profile_schema_has_no_location_field():
    fields = set(CandidateProfileIn.model_fields.keys())
    for forbidden_field in ("location", "current_location", "city", "current_city", "address"):
        assert forbidden_field not in fields


def test_generate_body_signature_has_no_candidate_location_parameter():
    """A structural guard: if someone later adds a `candidate_location` kwarg
    (the candidate's own current/personal location) to the email generator,
    this test fails immediately and loudly. `job_location` (the JD's job
    location, not the candidate's) is expected and fine - it's already
    surfaced in the subject line and is never candidate-identifying."""
    sig = inspect.signature(draft_service.generate_body)
    for param_name in sig.parameters:
        assert "candidate_location" not in param_name.lower()
        assert "current_location" not in param_name.lower()


def test_generated_body_never_contains_any_forbidden_location_string():
    # job_location=None here so none of the FORBIDDEN_LOCATION_STRINGS
    # (standing in for a leaked candidate personal location) could appear
    # via the legitimate, intentional job-location mention instead.
    body = draft_service.generate_body(
        job_title="Senior Data Engineer", job_location=None, recruiter_first_name="Naveen",
        top_skills=["python", "sql"], candidate_name="Diwakar Jilakara",
        candidate_experience="8+ years", candidate_work_auth="Authorized to work in the US",
        candidate_phone="555-123-4567", candidate_email="diwakar@example.com",
        candidate_linkedin="linkedin.com/in/diwakar",
    )
    for forbidden in FORBIDDEN_LOCATION_STRINGS:
        assert forbidden not in body


def test_e2e_pipeline_never_leaks_current_location_even_with_location_heavy_jd(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """End-to-end regression: even when the JD itself is packed with job
    locations, no non-JD (i.e. candidate current) location string appears."""
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"data_engineering_tools": ["databricks", "airflow"], "languages": ["python", "sql"]},
        years=8, titles=["Senior Data Engineer"],
    )
    raw = make_raw_message(
        message_id="loc-e2e", thread_id="loc-e2e-thread",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status.value == "DRAFT_CREATED"

    body = result.application.generated_body
    subject = result.application.generated_subject
    for forbidden in FORBIDDEN_LOCATION_STRINGS:
        assert forbidden not in body
        assert forbidden not in subject

    # the only location allowed anywhere is the job-required one - it's fine
    # in both the subject and the body, since it's the JD's own location,
    # never the candidate's
    assert "Irvine, CA" in subject
    assert "Irvine, CA" in body


def test_candidate_profile_upsert_ignores_unexpected_location_field(db_session):
    """Even if a caller/older client tries to send a location field, the
    Pydantic schema silently drops unknown fields rather than persisting it."""
    payload = CandidateProfileIn.model_validate({
        "name": "Diwakar", "experience": "8 years", "work_authorization": "US Citizen",
        "phone": "555", "email": "d@example.com", "linkedin": "li.com/d",
        "location": "San Francisco, CA",  # not a real field - must be ignored
    })
    assert not hasattr(payload, "location")
