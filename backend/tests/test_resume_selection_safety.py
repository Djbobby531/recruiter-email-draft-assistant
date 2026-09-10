"""
Resume selection policy: the pipeline always attaches the highest-scoring
available resume and creates a draft - it never blocks on a low match SCORE.
RESUME_MATCH_CONFIDENCE_THRESHOLD / MatchResult.confident are still computed
and stored (match_score/match_explanation stay visible on the application for
you to review after the fact), but they no longer gate automatic draft
creation. The only thing that still routes to MANUAL_REVIEW here is having
*zero* indexed resumes to choose from at all - there's genuinely nothing to
attach in that case.
"""
from __future__ import annotations

from app.config import Settings
from app.models import ProcessingStatus
from app.services.pipeline import process_message
from tests.conftest import make_raw_message, make_resume
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


def _raw(message_id):
    return make_raw_message(
        message_id=message_id, thread_id=f"thread-{message_id}",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )


def test_high_confidence_match_auto_selects_with_default_threshold(
    db_session, candidate_profile, fake_gmail_client
):
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"], "devops_tools": ["terraform", "ci/cd"],
        },
        years=8, titles=["Senior Data Engineer"],
    )
    settings = Settings(MY_EMAIL="diwakar@example.com", AI_PROVIDER="none", RESUME_MATCH_CONFIDENCE_THRESHOLD=30.0)
    result = process_message(db_session, settings, _raw("safety1"), fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED


def test_high_threshold_no_longer_blocks_automatic_draft_creation(
    db_session, candidate_profile, fake_gmail_client
):
    """RESUME_MATCH_CONFIDENCE_THRESHOLD is still recorded (match_score stays
    visible) but no longer gates whether a draft gets created - even a very
    strict configured threshold doesn't stop the best available resume from
    being attached and a draft being made."""
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"], "devops_tools": ["terraform", "ci/cd"],
        },
        years=8, titles=["Senior Data Engineer"],
    )
    settings = Settings(MY_EMAIL="diwakar@example.com", AI_PROVIDER="none", RESUME_MATCH_CONFIDENCE_THRESHOLD=99.0)
    result = process_message(db_session, settings, _raw("safety2"), fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert len(fake_gmail_client.created_drafts) == 1
    assert result.application.selected_resume_id is not None
    assert result.application.match_score is not None


def test_low_confidence_still_attaches_the_best_available_resume(db_session, candidate_profile, fake_gmail_client):
    """Even a JD in a completely unrelated field attaches the best (only)
    resume on file rather than pausing for manual review - the low
    match_score is recorded on the application/draft for you to see, but it
    no longer blocks the draft."""
    make_resume(
        db_session, "healthcare_resume.pdf",
        skills={"domains": ["healthcare"], "languages": ["java"]},
        years=3, titles=["Healthcare Analyst"],
    )
    settings = Settings(MY_EMAIL="diwakar@example.com", AI_PROVIDER="none", RESUME_MATCH_CONFIDENCE_THRESHOLD=30.0)
    result = process_message(db_session, settings, _raw("safety3"), fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application.selected_resume_id is not None
    assert len(fake_gmail_client.created_drafts) == 1


def test_no_resumes_at_all_routes_to_manual_review_not_error(db_session, candidate_profile, fake_gmail_client):
    settings = Settings(MY_EMAIL="diwakar@example.com", AI_PROVIDER="none")
    result = process_message(db_session, settings, _raw("safety4"), fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.MANUAL_REVIEW


def test_when_scores_tie_the_earliest_uploaded_resume_wins(db_session, candidate_profile, fake_gmail_client):
    """A JD with nothing to differentiate on (no matching skills/title for any
    resume) scores every resume identically - the tie deterministically
    resolves to whichever resume was uploaded first, not an arbitrary pick."""
    first = make_resume(db_session, "first_uploaded.pdf", skills={"domains": ["healthcare"]}, years=3)
    make_resume(db_session, "second_uploaded.pdf", skills={"domains": ["healthcare"]}, years=3)
    settings = Settings(MY_EMAIL="diwakar@example.com", AI_PROVIDER="none")
    result = process_message(db_session, settings, _raw("safety5"), fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application.selected_resume_id == first.id
