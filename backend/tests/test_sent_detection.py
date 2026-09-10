"""
Sections 3/17; ST-01..ST-09: Gmail sent-message detection. The critical rule
under test everywhere here is that a Gmail DRAFT existing is never, by
itself, evidence of a sent application - only an actual SENT-labeled message
matching thread + recipient + subject can flip DRAFT -> SENT.
"""
from __future__ import annotations

import pytest
from googleapiclient.errors import HttpError

from app.models import Application, AppStatus, Draft, EventType, ProcessedMessage, ProcessingStatus
from app.services.sent_detection import find_sent_message_id, run_sent_sync_cycle, sync_application_sent_status
from tests.conftest import FakeGmailClient


class _FakeHttpResp:
    def __init__(self, status):
        self.status = status
        self.reason = "Rate Limit Exceeded"


def _make_application_with_draft(db_session, thread_id="thread-1", status=AppStatus.DRAFT):
    message = ProcessedMessage(
        gmail_message_id=f"m-{thread_id}", thread_id=thread_id, from_email="james@algebrait.com",
        status=ProcessingStatus.DRAFT_CREATED,
    )
    db_session.add(message)
    db_session.commit()
    application = Application(
        source_message_id=message.id, thread_id=thread_id,
        recruiter_name="Naveen", recruiter_email="naveen@pamten.com", status=status,
    )
    db_session.add(application)
    db_session.commit()
    draft = Draft(
        gmail_draft_id="draft-abc", application_id=application.id,
        to_email="naveen@pamten.com", cc_email="james@algebrait.com",
        subject="Application – Senior Data Engineer – Irvine, CA",
        attached_resume_filename="resume.pdf", status="CREATED",
    )
    db_session.add(draft)
    db_session.commit()
    db_session.refresh(application)
    db_session.refresh(draft)
    return application, draft


# ST-01 / ST-02 -------------------------------------------------------------


def test_st01_draft_created_status_is_draft(db_session):
    application, _ = _make_application_with_draft(db_session)
    assert application.status == AppStatus.DRAFT


def test_st02_no_sent_message_yet_status_remains_draft(db_session):
    application, draft = _make_application_with_draft(db_session)
    client = FakeGmailClient()  # empty thread - nothing sent yet
    updated = sync_application_sent_status(db_session, client, application, draft)
    assert updated is False
    assert application.status == AppStatus.DRAFT


# ST-03 -----------------------------------------------------------------


def test_st03_user_sends_draft_flips_status_to_sent(db_session):
    application, draft = _make_application_with_draft(db_session)
    client = FakeGmailClient()
    client.simulate_message(application.thread_id, to=draft.to_email, subject=draft.subject, labels=["SENT"])

    updated = sync_application_sent_status(db_session, client, application, draft)
    assert updated is True
    assert application.status == AppStatus.SENT
    assert application.sent_message_id is not None
    assert application.sent_at is not None


# ST-04 -----------------------------------------------------------------


def test_st04_sent_detected_after_several_sync_cycles(db_session):
    application, draft = _make_application_with_draft(db_session)
    client = FakeGmailClient()

    # first two cycles: nothing sent yet
    assert run_sent_sync_cycle(db_session, client) == 0
    assert run_sent_sync_cycle(db_session, client) == 0
    db_session.refresh(application)
    assert application.status == AppStatus.DRAFT

    # user finally sends it
    client.simulate_message(application.thread_id, to=draft.to_email, subject=draft.subject, labels=["SENT"])
    assert run_sent_sync_cycle(db_session, client) == 1
    db_session.refresh(application)
    assert application.status == AppStatus.SENT


# ST-05 -----------------------------------------------------------------


def test_st05_sync_runs_twice_only_one_email_sent_event(db_session):
    application, draft = _make_application_with_draft(db_session)
    client = FakeGmailClient()
    client.simulate_message(application.thread_id, to=draft.to_email, subject=draft.subject, labels=["SENT"])

    run_sent_sync_cycle(db_session, client)
    run_sent_sync_cycle(db_session, client)  # idempotent second run

    from app.models import ApplicationEvent
    sent_events = (
        db_session.query(ApplicationEvent)
        .filter(ApplicationEvent.application_id == application.id, ApplicationEvent.event_type == EventType.EMAIL_SENT)
        .all()
    )
    assert len(sent_events) == 1


# ST-06 -----------------------------------------------------------------


def test_st06_draft_deleted_without_sending_never_marks_sent(db_session):
    application, draft = _make_application_with_draft(db_session)
    db_session.delete(draft)
    db_session.commit()
    db_session.refresh(application)

    updated = run_sent_sync_cycle(db_session, FakeGmailClient())
    assert updated == 0
    assert application.status == AppStatus.DRAFT


# ST-07 -----------------------------------------------------------------


def test_st07_unrelated_sent_email_in_thread_does_not_mark_application_sent(db_session):
    application, draft = _make_application_with_draft(db_session)
    client = FakeGmailClient()
    # user sent something else in the same thread, to a different recipient
    client.simulate_message(application.thread_id, to="someone-else@example.com", subject="Unrelated", labels=["SENT"])

    updated = sync_application_sent_status(db_session, client, application, draft)
    assert updated is False
    assert application.status == AppStatus.DRAFT


def test_find_sent_message_id_requires_matching_recipient_and_subject():
    messages = [
        {"id": "m1", "labelIds": ["SENT"], "payload": {"headers": [
            {"name": "To", "value": "wrong@example.com"}, {"name": "Subject", "value": "Application – X"},
        ]}},
        {"id": "m2", "labelIds": ["INBOX"], "payload": {"headers": [
            {"name": "To", "value": "naveen@pamten.com"}, {"name": "Subject", "value": "Application – X"},
        ]}},
        {"id": "m3", "labelIds": ["SENT"], "payload": {"headers": [
            {"name": "To", "value": "naveen@pamten.com"}, {"name": "Subject", "value": "Application – X"},
        ]}},
    ]
    assert find_sent_message_id(messages, "naveen@pamten.com", "Application – X") == "m3"


def test_find_sent_message_id_ignores_re_fwd_subject_noise():
    messages = [
        {"id": "m1", "labelIds": ["SENT"], "payload": {"headers": [
            {"name": "To", "value": "naveen@pamten.com"}, {"name": "Subject", "value": "Re: Application – X"},
        ]}},
    ]
    assert find_sent_message_id(messages, "naveen@pamten.com", "Application – X") == "m1"


# ST-08 -----------------------------------------------------------------


def test_st08_reply_in_same_thread_correctly_associated(db_session):
    application, draft = _make_application_with_draft(db_session)
    client = FakeGmailClient()
    client.simulate_message(application.thread_id, to=draft.to_email, subject=draft.subject, labels=["SENT"])
    client.simulate_message(application.thread_id, to="diwakar@example.com", subject=f"Re: {draft.subject}", labels=["INBOX"])

    updated = sync_application_sent_status(db_session, client, application, draft)
    assert updated is True
    assert application.sent_message_id is not None


# ST-09 -----------------------------------------------------------------


def test_st09_gmail_unavailable_retains_current_state(db_session):
    application, draft = _make_application_with_draft(db_session)
    client = FakeGmailClient()
    client.get_thread_error = TimeoutError("simulated Gmail API timeout")

    updated = sync_application_sent_status(db_session, client, application, draft)
    assert updated is False
    assert application.status == AppStatus.DRAFT


def test_sync_is_a_noop_for_applications_not_in_draft_status(db_session):
    application, draft = _make_application_with_draft(db_session, status=AppStatus.SENT)
    client = FakeGmailClient()
    client.simulate_message(application.thread_id, to=draft.to_email, subject=draft.subject, labels=["SENT"])
    updated = sync_application_sent_status(db_session, client, application, draft)
    assert updated is False


def test_run_sent_sync_cycle_only_scans_draft_status_applications(db_session):
    sent_app, sent_draft = _make_application_with_draft(db_session, thread_id="t-sent", status=AppStatus.SENT)
    draft_app, draft_draft = _make_application_with_draft(db_session, thread_id="t-draft", status=AppStatus.DRAFT)
    client = FakeGmailClient()
    client.simulate_message("t-sent", to=sent_draft.to_email, subject=sent_draft.subject, labels=["SENT"])
    client.simulate_message("t-draft", to=draft_draft.to_email, subject=draft_draft.subject, labels=["SENT"])

    updated = run_sent_sync_cycle(db_session, client)
    assert updated == 1  # only the DRAFT one gets processed
    db_session.refresh(draft_app)
    assert draft_app.status == AppStatus.SENT


# --- Gmail rate-limit handling ---------------------------------------------


def test_rate_limit_stops_the_cycle_early_instead_of_hammering_remaining_applications(db_session):
    """A large backlog of DRAFT applications (e.g. after reconnecting a
    stale account) means one lookup per application - if Gmail starts
    rate-limiting these, the cycle must stop immediately rather than firing
    off a burst of requests that are all doomed to fail too."""
    for i in range(5):
        _make_application_with_draft(db_session, thread_id=f"t-ratelimit-{i}")

    client = FakeGmailClient()
    client.get_thread_error = HttpError(_FakeHttpResp(403), b'{"error": {"message": "rateLimitExceeded"}}')

    updated = run_sent_sync_cycle(db_session, client)
    assert updated == 0
    # every application must still be DRAFT, untouched, ready to retry next cycle
    assert db_session.query(Application).filter(Application.status == AppStatus.DRAFT).count() == 5


def test_rate_limit_error_propagates_out_of_sync_application_sent_status(db_session):
    application, draft = _make_application_with_draft(db_session)
    client = FakeGmailClient()
    client.get_thread_error = HttpError(_FakeHttpResp(429), b'{"error": {"message": "rate limited"}}')

    with pytest.raises(HttpError):
        sync_application_sent_status(db_session, client, application, draft)


def test_a_transient_gmail_error_still_does_not_stop_the_cycle(db_session):
    """A genuine transient one-off failure (e.g. a 5xx) must still be
    swallowed and simply retried next cycle - only rate-limit-shaped errors
    stop the whole batch early, and only a 404 (permanently gone) skips
    future retries."""
    _make_application_with_draft(db_session, thread_id="t-error-a")
    _make_application_with_draft(db_session, thread_id="t-error-b")

    client = FakeGmailClient()
    client.get_thread_error = HttpError(_FakeHttpResp(500), b'{"error": {"message": "internal error"}}')

    updated = run_sent_sync_cycle(db_session, client)
    assert updated == 0
    assert db_session.query(Application).filter(Application.status == AppStatus.DRAFT).count() == 2


# --- Permanently-missing Gmail thread (e.g. user deleted the draft) --------


def test_thread_not_found_marks_the_draft_missing_and_does_not_raise(db_session):
    application, draft = _make_application_with_draft(db_session)
    client = FakeGmailClient()
    client.get_thread_error = HttpError(_FakeHttpResp(404), b'{"error": {"message": "not found"}}')

    updated = sync_application_sent_status(db_session, client, application, draft)
    assert updated is False
    assert application.status == AppStatus.DRAFT  # tracker status is untouched
    db_session.refresh(draft)
    assert draft.status == "MISSING_IN_GMAIL"


def test_thread_not_found_is_only_ever_fetched_once_across_cycles(db_session):
    """Once a thread is confirmed gone, later poll cycles must skip it
    entirely rather than re-fetching (and re-warning about) the same
    permanently-doomed thread forever."""
    application, draft = _make_application_with_draft(db_session)

    class CountingNotFoundClient(FakeGmailClient):
        def __init__(self):
            super().__init__()
            self.get_thread_calls = 0

        def get_thread(self, thread_id):
            self.get_thread_calls += 1
            raise HttpError(_FakeHttpResp(404), b'{"error": {"message": "not found"}}')

    client = CountingNotFoundClient()

    assert run_sent_sync_cycle(db_session, client) == 0
    assert client.get_thread_calls == 1
    db_session.refresh(draft)
    assert draft.status == "MISSING_IN_GMAIL"

    # a second cycle must not call get_thread again for this application
    assert run_sent_sync_cycle(db_session, client) == 0
    assert client.get_thread_calls == 1


def test_thread_not_found_does_not_stop_the_rest_of_the_cycle(db_session):
    """A permanently-missing thread for one application must not prevent
    other applications in the same cycle from being checked."""
    app_missing, draft_missing = _make_application_with_draft(db_session, thread_id="t-gone")
    app_ok, draft_ok = _make_application_with_draft(db_session, thread_id="t-ok")

    class SelectivelyMissingClient(FakeGmailClient):
        def get_thread(self, thread_id):
            if thread_id == "t-gone":
                raise HttpError(_FakeHttpResp(404), b'{"error": {"message": "not found"}}')
            return super().get_thread(thread_id)

    client = SelectivelyMissingClient()
    client.simulate_message("t-ok", to=draft_ok.to_email, subject=draft_ok.subject, labels=["SENT"])

    updated = run_sent_sync_cycle(db_session, client)
    assert updated == 1
    db_session.refresh(app_ok)
    assert app_ok.status == AppStatus.SENT
    db_session.refresh(draft_missing)
    assert draft_missing.status == "MISSING_IN_GMAIL"
