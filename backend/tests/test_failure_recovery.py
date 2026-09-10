"""
Section 21: failure/recovery behavior. No corrupted application, no duplicate
draft, clear ERROR status, retry where safe, manual review where necessary.
"""
from __future__ import annotations

from app.ai.base import AIProvider
from app.models import Application, Draft, ProcessingStatus
from app.services.pipeline import process_message
from app.services.resume_service import save_and_index_resume
from tests.conftest import FlakyGmailClient, make_raw_message, make_resume
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


def _raw(message_id):
    return make_raw_message(
        message_id=message_id, thread_id=f"thread-{message_id}",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )


def _seed_resume(db_session):
    return make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"], "devops_tools": ["terraform", "ci/cd"],
        },
        years=8, titles=["Senior Data Engineer"],
    )


def test_gmail_api_unavailable_marks_error_no_corrupted_application(db_session, settings, candidate_profile):
    _seed_resume(db_session)
    client = FlakyGmailClient(fail_times=99, exception_factory=lambda: ConnectionError("Gmail API unavailable"))
    result = process_message(db_session, settings, _raw("fr1"), client, ai_provider=None)

    assert result.status == ProcessingStatus.ERROR
    assert db_session.query(Draft).count() == 0
    # the application row that DOES exist has complete, non-corrupted data
    app = db_session.query(Application).first()
    assert app.recruiter_email == "naveen.gangupamu@pamten.com"
    assert app.job_title is not None


def test_database_write_failure_during_processing_does_not_leave_a_draft_without_a_record(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """Simulates a DB error partway through processing (e.g. disk full) by
    monkeypatching commit to fail once. The pipeline's blanket exception
    handler must catch it and mark ERROR without having created a Gmail draft."""
    _seed_resume(db_session)

    original_commit = db_session.commit
    call_count = {"n": 0}

    def flaky_commit():
        call_count["n"] += 1
        # fail on the commit that would persist the fully-generated application
        # (empirically the 5th commit in the happy path for this fixture)
        if call_count["n"] == 5:
            raise OSError("simulated database write failure")
        return original_commit()

    db_session.commit = flaky_commit
    result = process_message(db_session, settings, _raw("fr2"), fake_gmail_client, ai_provider=None)
    db_session.commit = original_commit  # restore for cleanup

    assert result.status == ProcessingStatus.ERROR
    assert len(fake_gmail_client.created_drafts) == 0


def test_ai_provider_unavailable_falls_back_to_deterministic_and_does_not_error(
    db_session, settings, candidate_profile, fake_gmail_client
):
    class DownAIProvider(AIProvider):
        def classify_job_email(self, subject, body):
            raise ConnectionError("AI provider unreachable")

        def extract_job_details(self, text):
            raise ConnectionError("AI provider unreachable")

        def polish_email_body(self, draft_body, constraints):
            raise ConnectionError("AI provider unreachable")

        def classify_interview_requirement(self, text):
            raise ConnectionError("AI provider unreachable")

        def evaluate_and_customize_resume(self, resume_text, jd_title, jd_text, candidate_existing_skills):
            raise ConnectionError("AI provider unreachable")

    _seed_resume(db_session)
    result = process_message(db_session, settings, _raw("fr3"), fake_gmail_client, ai_provider=DownAIProvider())
    # deterministic classification/extraction already have enough signal for
    # this sample email, so the AI outage should not block the whole pipeline
    assert result.status == ProcessingStatus.DRAFT_CREATED


def test_ai_returns_invalid_json_does_not_create_a_bad_draft(db_session, settings, candidate_profile, fake_gmail_client):
    class BadJSONAIProvider(AIProvider):
        def classify_job_email(self, subject, body):
            raise ValueError("invalid JSON from model")

        def extract_job_details(self, text):
            raise ValueError("invalid JSON from model")

        def polish_email_body(self, draft_body, constraints):
            raise ValueError("invalid JSON from model")

        def classify_interview_requirement(self, text):
            raise ValueError("invalid JSON from model")

        def evaluate_and_customize_resume(self, resume_text, jd_title, jd_text, candidate_existing_skills):
            raise ValueError("invalid JSON from model")

    _seed_resume(db_session)
    result = process_message(db_session, settings, _raw("fr4"), fake_gmail_client, ai_provider=BadJSONAIProvider())
    # falls back to deterministic extraction/classification/template body
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application.generated_body is not None


def test_pdf_extraction_failure_at_upload_marks_resume_failed_not_crash(db_session, settings, tmp_path):
    corrupted_pdf_bytes = b"this is not a real pdf file at all"
    resume = save_and_index_resume(db_session, str(tmp_path), "corrupted.pdf", corrupted_pdf_bytes)
    assert resume.indexing_status in ("FAILED", "INDEXED_EMPTY_TEXT")


def test_gmail_draft_creation_failure_then_manual_retry_succeeds_exactly_once(
    db_session, settings, candidate_profile
):
    _seed_resume(db_session)
    client = FlakyGmailClient(fail_times=2)  # fails twice, succeeds on 3rd attempt
    r1 = process_message(db_session, settings, _raw("fr5"), client, ai_provider=None)
    assert r1.status == ProcessingStatus.ERROR
    r2 = process_message(db_session, settings, _raw("fr5"), client, ai_provider=None)
    assert r2.status == ProcessingStatus.ERROR
    r3 = process_message(db_session, settings, _raw("fr5"), client, ai_provider=None)
    assert r3.status == ProcessingStatus.DRAFT_CREATED
    assert len(client.created_drafts) == 1


def test_network_timeout_on_draft_creation_is_recoverable(db_session, settings, candidate_profile):
    _seed_resume(db_session)
    client = FlakyGmailClient(fail_times=1, exception_factory=lambda: TimeoutError("read timed out"))
    r1 = process_message(db_session, settings, _raw("fr6"), client, ai_provider=None)
    assert r1.status == ProcessingStatus.ERROR
    assert r1.processed_message.error_message is not None
    r2 = process_message(db_session, settings, _raw("fr6"), client, ai_provider=None)
    assert r2.status == ProcessingStatus.DRAFT_CREATED


def test_oauth_token_expired_surfaces_as_a_clear_gmail_connection_error(db_session, settings, candidate_profile):
    """The poller's job is to surface an expired/revoked OAuth token as a clear,
    actionable error rather than crash the whole polling loop or silently drop
    messages. Simulated here via a Gmail client stub that raises the same
    exception shape google-auth raises for an invalid_grant/expired token."""
    _seed_resume(db_session)

    class ExpiredTokenClient:
        def create_draft_with_attachment(self, *a, **kw):
            raise RuntimeError("invalid_grant: Token has been expired or revoked.")

    result = process_message(db_session, settings, _raw("fr7"), ExpiredTokenClient(), ai_provider=None)
    assert result.status == ProcessingStatus.ERROR
    assert "expired" in result.reason.lower() or "invalid_grant" in result.reason.lower()


def test_error_status_never_blocks_a_legitimate_later_retry(db_session, settings, candidate_profile):
    """Regression guard for the bug found during hardening: ERROR must not be
    treated as a terminal duplicate-skip status."""
    _seed_resume(db_session)
    client = FlakyGmailClient(fail_times=1)
    process_message(db_session, settings, _raw("fr8"), client, ai_provider=None)  # -> ERROR
    result = process_message(db_session, settings, _raw("fr8"), client, ai_provider=None)  # retry
    assert result.status == ProcessingStatus.DRAFT_CREATED
