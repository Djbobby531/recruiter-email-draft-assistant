"""
Two related "never draft on this" guards:

1. Only genuinely RECEIVED mail is processed - a message the mailbox owner
   sent themselves (which still surfaces via the Gmail History API) is never
   treated as something to draft an application reply to.
2. Never draft a second application to the same recruiter (by normalized
   email) on the same calendar day once one has been CONFIRMED SENT - a mere
   unsent draft never counts, only a real Gmail-confirmed send does.
"""
from __future__ import annotations

import datetime as dt

from app.models import Application, AppStatus, ProcessingStatus, Recruiter
from app.services.pipeline import process_message
from tests.conftest import make_raw_message, make_resume
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


def _seed_resume(db_session):
    return make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"], "devops_tools": ["terraform", "ci/cd"],
        },
        years=8, titles=["Senior Data Engineer"],
    )


def _raw(message_id, thread_id, from_header=None):
    return make_raw_message(
        message_id=message_id, thread_id=thread_id,
        from_header=from_header or "James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )


# --- Own-sent-email guard --------------------------------------------------


def test_message_sent_by_the_mailbox_owner_is_skipped(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_resume(db_session)
    raw = _raw("own1", "own1-t", from_header=f"Me <{settings.MY_EMAIL}>")

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.SKIPPED_OWN_SENT_EMAIL
    assert len(fake_gmail_client.created_drafts) == 0
    assert db_session.query(Application).count() == 0
    assert db_session.query(Recruiter).count() == 0  # never even upserted


def test_own_sent_email_check_is_case_insensitive(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_resume(db_session)
    raw = _raw("own2", "own2-t", from_header=f"Me <{settings.MY_EMAIL.upper()}>")

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.SKIPPED_OWN_SENT_EMAIL


def test_genuinely_received_email_from_someone_else_is_not_affected(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    raw = _raw("own3", "own3-t")  # from james@algebrait.com, not the mailbox owner
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED


# --- Same-recruiter-already-sent-today guard --------------------------------


def _mark_sent_today(db_session, application: Application) -> None:
    application.status = AppStatus.SENT
    application.sent_at = dt.datetime.now(dt.UTC)
    application.sent_message_id = "sent-msg-1"
    db_session.add(application)
    db_session.commit()


def test_second_email_same_recruiter_same_day_after_confirmed_send_is_skipped(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    first = process_message(db_session, settings, _raw("dup1a", "dup1a-t"), fake_gmail_client, ai_provider=None)
    assert first.status == ProcessingStatus.DRAFT_CREATED
    _mark_sent_today(db_session, first.application)

    second = process_message(db_session, settings, _raw("dup1b", "dup1b-t"), fake_gmail_client, ai_provider=None)

    assert second.status == ProcessingStatus.SKIPPED_RECRUITER_ALREADY_CONTACTED_TODAY
    assert len(fake_gmail_client.created_drafts) == 1  # still just the one from before
    assert second.opportunity is not None
    assert second.opportunity.status == ProcessingStatus.SKIPPED_RECRUITER_ALREADY_CONTACTED_TODAY


def test_an_unsent_draft_never_triggers_the_same_day_skip(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """The critical distinction: a DRAFT (never confirmed sent) must never
    count toward this guard - only a real Gmail-confirmed send does."""
    _seed_resume(db_session)
    first = process_message(db_session, settings, _raw("dup2a", "dup2a-t"), fake_gmail_client, ai_provider=None)
    assert first.status == ProcessingStatus.DRAFT_CREATED
    assert first.application.status == AppStatus.DRAFT  # never marked sent

    second = process_message(db_session, settings, _raw("dup2b", "dup2b-t"), fake_gmail_client, ai_provider=None)
    assert second.status == ProcessingStatus.DRAFT_CREATED
    assert len(fake_gmail_client.created_drafts) == 2


def test_a_send_from_a_previous_day_does_not_trigger_the_skip(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    first = process_message(db_session, settings, _raw("dup3a", "dup3a-t"), fake_gmail_client, ai_provider=None)
    first.application.status = AppStatus.SENT
    first.application.sent_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
    first.application.sent_message_id = "sent-msg-old"
    db_session.add(first.application)
    db_session.commit()

    second = process_message(db_session, settings, _raw("dup3b", "dup3b-t"), fake_gmail_client, ai_provider=None)
    assert second.status == ProcessingStatus.DRAFT_CREATED


def test_a_confirmed_send_to_a_different_recruiter_does_not_trigger_the_skip(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    first = process_message(db_session, settings, _raw("dup4a", "dup4a-t"), fake_gmail_client, ai_provider=None)
    _mark_sent_today(db_session, first.application)

    other_subject = "Fw: Hiring for || Role: AWS Data Engineer Location: Dallas, TX ||"
    other_body = (
        "James\n\nAlgebra IT LLC\n\nE: james@algebrait.com\n\n"
        "---------- Forwarded message ----------\n"
        "From: Priya Shah <priya.shah@otherstaffing.com>\n"
        "Role: AWS Data Engineer\n\nLocation: Dallas, TX\n\n"
        "Responsibilities:\n- Build and maintain cloud data pipelines.\n\n"
        "Skills:\n- AWS\n- Python\n- SQL\n\n"
        "Contract: W2\n\nThanks,\nPriya Shah\nTalent Acquisition Executive\nOther Staffing\n"
    )
    raw = make_raw_message(
        message_id="dup4b", thread_id="dup4b-t", from_header="James AlgebraIT <james@algebrait.com>",
        subject=other_subject, plain_body=other_body,
    )
    second = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert second.status == ProcessingStatus.DRAFT_CREATED
