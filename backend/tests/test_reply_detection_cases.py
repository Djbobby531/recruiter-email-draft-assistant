"""
RD-01 .. RD-08: reply detection (section 6 of the hardening spec). This is the
highest-priority safety rule in the whole app, so it gets the most thorough
coverage - including the negative case (RD-07/RD-08) where something that
merely *looks* like a reply must still be processed as a new job.
"""
from __future__ import annotations

from app.models import ProcessingStatus
from app.services.pipeline import process_message
from tests.conftest import load_reply_email, make_raw_message, make_resume
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


def _seed_databricks_resume(db_session):
    return make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"],
            "devops_tools": ["terraform", "ci/cd"],
            "governance_cert": ["data governance"],
        },
        years=8, titles=["Senior Data Engineer"],
    )


def _apply_once(db_session, settings, fake_gmail_client, message_id="orig", thread_id="thread-1"):
    raw = make_raw_message(
        message_id=message_id, thread_id=thread_id,
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
        message_id_header="<orig-msg-id@mail.gmail.com>",
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    return result


def test_rd01_in_reply_to_references_my_sent_message(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_databricks_resume(db_session)
    _apply_once(db_session, settings, fake_gmail_client, message_id="rd01-orig", thread_id="rd01-thread")

    reply = make_raw_message(
        message_id="rd01-reply", thread_id="rd01-thread-DIFFERENT",  # even a different thread id
        from_header="naveen.gangupamu@pamten.com", subject="Re: " + SAMPLE_SUBJECT,
        plain_body="Can we schedule a call?",
        in_reply_to="<orig-msg-id@mail.gmail.com>",
    )
    result = process_message(db_session, settings, reply, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.SKIPPED_REPLY
    assert len(fake_gmail_client.created_drafts) == 1


def test_rd02_references_header_contains_my_sent_message_id(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_databricks_resume(db_session)
    _apply_once(db_session, settings, fake_gmail_client, message_id="rd02-orig", thread_id="rd02-thread")

    reply = make_raw_message(
        message_id="rd02-reply", thread_id="rd02-thread-DIFFERENT",
        from_header="naveen.gangupamu@pamten.com", subject="Re: " + SAMPLE_SUBJECT,
        plain_body="Following up on this.",
        references="<some-other-id@mail.gmail.com> <orig-msg-id@mail.gmail.com>",
    )
    result = process_message(db_session, settings, reply, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.SKIPPED_REPLY


def test_rd03_same_gmail_thread_contains_previous_application(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_databricks_resume(db_session)
    _apply_once(db_session, settings, fake_gmail_client, message_id="rd03-orig", thread_id="rd03-thread")

    reply = make_raw_message(
        message_id="rd03-reply", thread_id="rd03-thread",  # same thread, no headers at all
        from_header="naveen.gangupamu@pamten.com", subject="something totally different",
        plain_body="hello",
    )
    result = process_message(db_session, settings, reply, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.SKIPPED_REPLY


def test_rd04_subject_starts_with_re_and_thread_has_application(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_databricks_resume(db_session)
    _apply_once(db_session, settings, fake_gmail_client, message_id="rd04-orig", thread_id="rd04-thread")

    reply = make_raw_message(
        message_id="rd04-reply", thread_id="rd04-thread",
        from_header="naveen.gangupamu@pamten.com", subject="Re: " + SAMPLE_SUBJECT,
        plain_body=load_reply_email("simple_reply.txt"),
    )
    result = process_message(db_session, settings, reply, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.SKIPPED_REPLY


def test_rd05_recruiter_replies_without_re_prefix_same_thread(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_databricks_resume(db_session)
    _apply_once(db_session, settings, fake_gmail_client, message_id="rd05-orig", thread_id="rd05-thread")

    reply = make_raw_message(
        message_id="rd05-reply", thread_id="rd05-thread",
        from_header="naveen.gangupamu@pamten.com", subject="quick question",
        plain_body="Are you available Thursday afternoon?",
    )
    result = process_message(db_session, settings, reply, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.SKIPPED_REPLY


def test_rd06_recruiter_changes_subject_but_references_my_message(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_databricks_resume(db_session)
    _apply_once(db_session, settings, fake_gmail_client, message_id="rd06-orig", thread_id="rd06-thread")

    reply = make_raw_message(
        message_id="rd06-reply", thread_id="rd06-thread-DIFFERENT",
        from_header="naveen.gangupamu@pamten.com", subject=load_reply_email("reply_changed_subject.txt").splitlines()[0] or "Scheduling",
        plain_body=load_reply_email("reply_changed_subject.txt"),
        in_reply_to="<orig-msg-id-rd06@mail.gmail.com>",
        references="<orig-msg-id-rd06@mail.gmail.com>",
    )
    # this test uses its own dedicated Message-ID for the original since _apply_once
    # hardcodes "<orig-msg-id@mail.gmail.com>" - reseed with a matching id
    raw_orig = make_raw_message(
        message_id="rd06-orig-2", thread_id="rd06-thread-2",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
        message_id_header="<orig-msg-id-rd06@mail.gmail.com>",
    )
    process_message(db_session, settings, raw_orig, fake_gmail_client, ai_provider=None)

    result = process_message(db_session, settings, reply, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.SKIPPED_REPLY


def test_rd07_new_job_email_happens_to_contain_re_in_subject_is_processed(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_databricks_resume(db_session)
    raw = make_raw_message(
        message_id="rd07", thread_id="rd07-thread-new",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject="Re: New Databricks opening - " + SAMPLE_SUBJECT,
        plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED


def test_rd08_forwarded_job_with_unrelated_quoted_thread_is_not_misclassified_as_reply(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_databricks_resume(db_session)
    body_with_unrelated_quote = (
        SAMPLE_BODY
        + "\n\n> On Mon, Aug 1, 2026, Someone Else <someone@unrelated.com> wrote:\n"
        + "> This is quoted content from a totally unrelated prior thread that has\n"
        + "> nothing to do with any application this app has ever created.\n"
    )
    raw = make_raw_message(
        message_id="rd08", thread_id="rd08-fresh-thread",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=body_with_unrelated_quote,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
