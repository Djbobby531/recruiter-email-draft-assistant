"""
Section 17: Gmail draft creation - mocked Gmail API, TO/CC/Subject/Body/
Attachment verification, success/failure/timeout/retry, and the hard
guarantee that no code path can ever call Gmail's send API.
"""
from __future__ import annotations

import inspect

from app.models import ProcessingStatus
from app.services.gmail_service import GmailClient
from app.services.pipeline import process_message
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


def test_draft_creation_success_captures_to_cc_subject_body_attachment(
    db_session, settings, candidate_profile, fake_gmail_client
):
    resume = _seed_resume(db_session)
    result = process_message(db_session, settings, _raw("gd1"), fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    draft_call = fake_gmail_client.created_drafts[0]
    assert draft_call["to_email"] == "naveen.gangupamu@pamten.com"
    assert draft_call["cc_email"] == "james@algebrait.com"
    assert draft_call["subject"].startswith("Application for")
    assert "I'm interested in the" in draft_call["body_text"]
    assert draft_call["attachment_path"] == resume.file_path


def test_draft_is_created_as_a_brand_new_standalone_email_never_a_reply(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """The generated draft must never be threaded under the original
    recruiter email (which would make Gmail show it quoted/rewritten like a
    reply) - it's a fresh, standalone message that simply starts 'Hi,'.
    Gmail is asked to create it with no thread_id at all, and the
    application record is updated to wherever Gmail actually placed it."""
    _seed_resume(db_session)
    original_thread_id = "thread-gd-standalone"
    raw = make_raw_message(
        message_id="gd-standalone", thread_id=original_thread_id,
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    draft_call = fake_gmail_client.created_drafts[0]
    assert draft_call["thread_id"] is None  # never passed - Gmail assigns a brand new thread
    assert draft_call["body_text"].startswith("Hi,")

    # the application now points at the NEW thread Gmail created, never the
    # original received email's thread
    assert result.application.thread_id != original_thread_id


def test_draft_creation_failure_marks_error_not_draft_created(db_session, settings, candidate_profile):
    _seed_resume(db_session)
    client = FlakyGmailClient(fail_times=99, exception_factory=lambda: RuntimeError("Gmail API 500"))
    result = process_message(db_session, settings, _raw("gd2"), client, ai_provider=None)

    assert result.status == ProcessingStatus.ERROR
    assert result.draft is None
    assert len(client.created_drafts) == 0


def test_draft_creation_timeout_marks_error_and_is_retryable(db_session, settings, candidate_profile):
    _seed_resume(db_session)
    client = FlakyGmailClient(fail_times=1)  # TimeoutError on first attempt by default
    first = process_message(db_session, settings, _raw("gd3"), client, ai_provider=None)
    assert first.status == ProcessingStatus.ERROR

    second = process_message(db_session, settings, _raw("gd3"), client, ai_provider=None)
    assert second.status == ProcessingStatus.DRAFT_CREATED
    assert len(client.created_drafts) == 1


def test_duplicate_retry_after_successful_creation_does_not_call_gmail_again(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    process_message(db_session, settings, _raw("gd4"), fake_gmail_client, ai_provider=None)
    process_message(db_session, settings, _raw("gd4"), fake_gmail_client, ai_provider=None)
    process_message(db_session, settings, _raw("gd4"), fake_gmail_client, ai_provider=None)

    assert len(fake_gmail_client.created_drafts) == 1


# --- The no-automatic-send guarantee (RULE J) ---

def test_gmail_client_class_has_no_send_method_at_all():
    """Structural guarantee: GmailClient exposes draft creation but has no
    method resembling send_message/send_email/send() anywhere on the class."""
    method_names = [name for name, _ in inspect.getmembers(GmailClient, predicate=inspect.isfunction)]
    for name in method_names:
        assert "send" not in name.lower(), f"GmailClient must never expose a send-like method, found: {name}"


def test_gmail_client_requested_scopes_never_include_gmail_send():
    from app.config import Settings
    s = Settings()
    for scope in s.gmail_scopes_list:
        assert "gmail.send" not in scope, "OAuth scopes must never request gmail.send"
        assert scope in (
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
        )


def test_pipeline_never_calls_anything_named_send_on_the_gmail_client(
    db_session, settings, candidate_profile
):
    """A gmail_client stub that raises if ANY attribute containing 'send' is
    ever accessed - proves the pipeline's only interaction with Gmail is
    create_draft_with_attachment."""
    _seed_resume(db_session)

    class SendGuardClient:
        def create_draft_with_attachment(self, to_email, cc_email, subject, body_text, attachment_path, thread_id=None):
            return {"id": "draft-guarded"}

        def __getattr__(self, item):
            if "send" in item.lower():
                raise AssertionError(f"pipeline attempted to access a send-like attribute: {item}")
            raise AttributeError(item)

    result = process_message(db_session, settings, _raw("gd5"), SendGuardClient(), ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
