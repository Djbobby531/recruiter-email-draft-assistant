"""
DUP-01 .. DUP-05: duplicate processing prevention (section 7 of the hardening spec).
"""
from __future__ import annotations

from app.models import Application, Draft, ProcessingStatus
from app.services.pipeline import process_message
from tests.conftest import FlakyGmailClient, make_raw_message, make_resume
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


def _seed_resume(db_session):
    return make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"],
            "devops_tools": ["terraform", "ci/cd"],
        },
        years=8, titles=["Senior Data Engineer"],
    )


def _raw(message_id, thread_id="dup-thread"):
    return make_raw_message(
        message_id=message_id, thread_id=thread_id,
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )


def test_dup01_same_gmail_message_processed_twice_one_application_one_draft(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    raw = _raw("dup01")
    first = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    second = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert first.status == ProcessingStatus.DRAFT_CREATED
    assert second.status == ProcessingStatus.SKIPPED_DUPLICATE
    assert db_session.query(Application).count() == 1
    assert db_session.query(Draft).count() == 1
    assert len(fake_gmail_client.created_drafts) == 1


def test_dup02_duplicated_pubsub_notification_for_same_message_id(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """A Pub/Sub push can redeliver the same notification; since both deliveries
    resolve to the same Gmail message ID, this is identical to DUP-01 at the
    pipeline level - the notification transport is irrelevant to dedup logic."""
    _seed_resume(db_session)
    raw = _raw("dup02")
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)  # triple delivery

    assert db_session.query(Draft).count() == 1
    assert len(fake_gmail_client.created_drafts) == 1


def test_dup03_crash_after_draft_creation_then_retry_creates_no_second_draft(
    db_session, settings, candidate_profile
):
    """Simulates a worker crash: the Gmail draft is created successfully, but we
    force the ProcessedMessage status to stay PROCESSING (as if the process died
    before the final commit). A retry must detect the existing Draft row and
    refuse to call Gmail again."""
    _seed_resume(db_session)
    client = FlakyGmailClient(fail_times=0)
    raw = _raw("dup03")

    result = process_message(db_session, settings, raw, client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert len(client.created_drafts) == 1

    # simulate a crash that left the message record stuck at PROCESSING despite
    # the draft already having been committed
    from app.models import ProcessedMessage
    record = db_session.query(ProcessedMessage).filter(ProcessedMessage.gmail_message_id == "dup03").first()
    record.status = ProcessingStatus.PROCESSING
    db_session.add(record)
    db_session.commit()

    retry_result = process_message(db_session, settings, raw, client, ai_provider=None)
    assert retry_result.status == ProcessingStatus.SKIPPED_DUPLICATE
    assert len(client.created_drafts) == 1  # still just the one


def test_dup03b_error_status_is_retried_and_succeeds_once(db_session, settings, candidate_profile):
    """Complement to DUP-03: an ERROR (e.g. a transient Gmail timeout) must be
    retryable, and a successful retry must produce exactly one draft."""
    _seed_resume(db_session)
    client = FlakyGmailClient(fail_times=1)  # first attempt raises, second succeeds
    raw = _raw("dup03b")

    first = process_message(db_session, settings, raw, client, ai_provider=None)
    assert first.status == ProcessingStatus.ERROR

    second = process_message(db_session, settings, raw, client, ai_provider=None)
    assert second.status == ProcessingStatus.DRAFT_CREATED
    assert len(client.created_drafts) == 1
    assert db_session.query(Application).count() == 1


def test_dup04_same_job_forwarded_twice_with_different_message_ids(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """Two distinct Gmail messages (different message IDs, different threads)
    carrying the identical JD are, per section 7, treated as legitimate separate
    applications by default - the app does not fabricate a "same job" heuristic
    that could wrongly suppress a real second opportunity. This is documented
    behavior, verified here so it doesn't silently regress into over-suppression."""
    _seed_resume(db_session)
    raw1 = _raw("dup04-a", thread_id="dup04-thread-a")
    raw2 = _raw("dup04-b", thread_id="dup04-thread-b")

    result1 = process_message(db_session, settings, raw1, fake_gmail_client, ai_provider=None)
    result2 = process_message(db_session, settings, raw2, fake_gmail_client, ai_provider=None)

    assert result1.status == ProcessingStatus.DRAFT_CREATED
    assert result2.status == ProcessingStatus.DRAFT_CREATED
    assert db_session.query(Application).count() == 2
    assert len(fake_gmail_client.created_drafts) == 2


def test_dup05_same_recruiter_sends_same_jd_twice_intentionally_not_blindly_suppressed(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """Same recruiter, same JD, sent twice as genuinely separate emails (e.g. a
    monthly re-post of an evergreen req). Each is its own Gmail message/thread,
    so each gets its own application - we do not silently drop the second one."""
    _seed_resume(db_session)
    raw1 = _raw("dup05-a", thread_id="dup05-thread-a")
    raw2 = _raw("dup05-b", thread_id="dup05-thread-b")

    process_message(db_session, settings, raw1, fake_gmail_client, ai_provider=None)
    result2 = process_message(db_session, settings, raw2, fake_gmail_client, ai_provider=None)

    assert result2.status == ProcessingStatus.DRAFT_CREATED
    assert len(fake_gmail_client.created_drafts) == 2
