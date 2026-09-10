"""
Section 20: performance at the target scale (~100 emails/day, 5-10 recruiters).
These are not throughput-maximization benchmarks - just a sanity check that
the pipeline handles a full day's volume, bursts, and duplicate deliveries
reliably and within generous time bounds on an in-memory SQLite DB with no
AI provider (the realistic default deployment for this single-user app).
"""
from __future__ import annotations

import time

from app.models import Application, Draft, ProcessedMessage
from app.services.pipeline import process_message
from tests.conftest import make_raw_message, make_resume
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT

PROMO_BODY = "Unsubscribe here. Huge sale this weekend only on everything in store."


def _seed_resumes(db_session):
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"], "devops_tools": ["terraform", "ci/cd"],
        },
        years=8, titles=["Senior Data Engineer"],
    )
    make_resume(db_session, "healthcare_resume.pdf", skills={"domains": ["healthcare"]}, years=3)
    make_resume(db_session, "aws_resume.pdf", skills={"cloud_platforms": ["aws"], "languages": ["python"]}, years=6)


def _job_email(i):
    return make_raw_message(
        message_id=f"perf-job-{i}", thread_id=f"perf-job-{i}-t",
        from_header="james@algebrait.com", subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )


def _promo_email(i):
    return make_raw_message(
        message_id=f"perf-promo-{i}", thread_id=f"perf-promo-{i}-t",
        from_header="deals@shopping-site.com", subject="Flash sale!", plain_body=PROMO_BODY,
    )


def test_100_sequential_messages_processed_reliably(db_session, settings, candidate_profile, fake_gmail_client):
    """Simulates a full day's volume: ~40% job emails, ~60% promotional/other,
    roughly matching the 5-10 recruiters / ~100 emails/day target profile."""
    _seed_resumes(db_session)

    start = time.monotonic()
    for i in range(100):
        raw = _job_email(i) if i % 5 < 2 else _promo_email(i)
        process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    elapsed = time.monotonic() - start

    assert elapsed < 20.0, f"100 sequential messages took too long: {elapsed:.2f}s"
    assert db_session.query(ProcessedMessage).count() == 100
    assert db_session.query(Application).count() == 40  # i % 5 < 2 -> 2 of every 5 = 40
    assert db_session.query(Draft).count() == 40
    assert len(fake_gmail_client.created_drafts) == 40


def test_burst_of_20_messages_in_quick_succession(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_resumes(db_session)
    start = time.monotonic()
    for i in range(20):
        process_message(db_session, settings, _job_email(i), fake_gmail_client, ai_provider=None)
    elapsed = time.monotonic() - start

    assert elapsed < 10.0
    assert len(fake_gmail_client.created_drafts) == 20


def test_duplicate_pubsub_notifications_in_a_burst_do_not_multiply_drafts(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """A burst where the same 5 messages are each redelivered 4x (20 total
    notifications) must still produce exactly 5 drafts."""
    _seed_resumes(db_session)
    messages = [_job_email(i) for i in range(5)]

    for _ in range(4):
        for raw in messages:
            process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert len(fake_gmail_client.created_drafts) == 5
    assert db_session.query(Application).count() == 5


def test_repeated_processing_does_not_leak_memory_growth_in_db_rows(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """A crude guard against unbounded row growth: reprocessing the same
    message many times must never create more than one ProcessedMessage row."""
    _seed_resumes(db_session)
    raw = _job_email(999)
    for _ in range(50):
        process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert db_session.query(ProcessedMessage).filter(ProcessedMessage.gmail_message_id == "perf-job-999").count() == 1
