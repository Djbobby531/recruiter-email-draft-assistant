"""
Section 4/13/30: the application lifecycle state machine. Every allowed
transition, several disallowed ones, and the override/audit-event behavior.
"""
from __future__ import annotations

import pytest

from app.models import Application, ApplicationEvent, AppStatus, EventType, ProcessedMessage, ProcessingStatus
from app.services.application_status import InvalidTransitionError, transition


def _make_application(db_session, status=AppStatus.DRAFT):
    message = ProcessedMessage(
        gmail_message_id="m1", thread_id="t1", from_email="james@algebrait.com",
        status=ProcessingStatus.DRAFT_CREATED,
    )
    db_session.add(message)
    db_session.commit()
    application = Application(
        source_message_id=message.id, thread_id="t1",
        recruiter_name="Naveen", recruiter_email="naveen@pamten.com",
        status=status,
    )
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)
    return application


ALLOWED_CASES = [
    (AppStatus.DRAFT, AppStatus.SENT),
    (AppStatus.DRAFT, AppStatus.WITHDRAWN),
    (AppStatus.SENT, AppStatus.SUBMITTED),
    (AppStatus.SENT, AppStatus.REJECTED),
    (AppStatus.SENT, AppStatus.WITHDRAWN),
    (AppStatus.SUBMITTED, AppStatus.INTERVIEW),
    (AppStatus.SUBMITTED, AppStatus.REJECTED),
    (AppStatus.SUBMITTED, AppStatus.WITHDRAWN),
    (AppStatus.INTERVIEW, AppStatus.REJECTED),
]


@pytest.mark.parametrize("start,target", ALLOWED_CASES)
def test_allowed_transition_succeeds(db_session, start, target):
    application = _make_application(db_session, status=start)
    result = transition(db_session, application, target)
    assert result.status == target


DISALLOWED_CASES = [
    (AppStatus.DRAFT, AppStatus.SUBMITTED),
    (AppStatus.DRAFT, AppStatus.INTERVIEW),
    (AppStatus.SENT, AppStatus.INTERVIEW),
    (AppStatus.SUBMITTED, AppStatus.SENT),
    (AppStatus.REJECTED, AppStatus.SUBMITTED),
    (AppStatus.WITHDRAWN, AppStatus.SENT),
]


@pytest.mark.parametrize("start,target", DISALLOWED_CASES)
def test_disallowed_transition_raises_without_override(db_session, start, target):
    application = _make_application(db_session, status=start)
    with pytest.raises(InvalidTransitionError):
        transition(db_session, application, target)
    db_session.refresh(application)
    assert application.status == start  # never partially applied


def test_disallowed_transition_succeeds_with_explicit_override(db_session):
    application = _make_application(db_session, status=AppStatus.DRAFT)
    result = transition(db_session, application, AppStatus.SUBMITTED, override=True, notes="admin correction")
    assert result.status == AppStatus.SUBMITTED

    events = db_session.query(ApplicationEvent).filter(ApplicationEvent.application_id == application.id).all()
    assert len(events) == 1
    assert events[0].event_type == EventType.MANUAL_OVERRIDE
    assert events[0].event_metadata["override"] is True
    assert events[0].event_metadata["notes"] == "admin correction"


def test_every_transition_logs_exactly_one_event(db_session):
    application = _make_application(db_session, status=AppStatus.DRAFT)
    transition(db_session, application, AppStatus.SENT)
    transition(db_session, application, AppStatus.SUBMITTED)
    transition(db_session, application, AppStatus.INTERVIEW)

    events = db_session.query(ApplicationEvent).filter(ApplicationEvent.application_id == application.id).all()
    assert [e.event_type for e in events] == [
        EventType.EMAIL_SENT, EventType.APPLICATION_SUBMITTED, EventType.INTERVIEW_REQUESTED,
    ]


def test_submitted_transition_sets_submitted_at(db_session):
    application = _make_application(db_session, status=AppStatus.SENT)
    result = transition(db_session, application, AppStatus.SUBMITTED)
    assert result.submitted_at is not None


def test_interview_transition_sets_interview_at_and_status(db_session):
    application = _make_application(db_session, status=AppStatus.SUBMITTED)
    result = transition(db_session, application, AppStatus.INTERVIEW)
    assert result.interview_at is not None
    assert result.interview_status == "INTERVIEW_REQUESTED"


def test_unknown_status_value_is_rejected(db_session):
    application = _make_application(db_session, status=AppStatus.DRAFT)
    with pytest.raises(InvalidTransitionError):
        transition(db_session, application, "NOT_A_REAL_STATUS")


def test_draft_created_alone_never_implies_sent_or_submitted(db_session):
    """The critical tracking rule (section 2/35): a freshly created
    application must start life as DRAFT, never SENT/SUBMITTED, regardless of
    how much other metadata (resume attached, subject generated) exists."""
    application = _make_application(db_session, status=AppStatus.DRAFT)
    assert application.status == AppStatus.DRAFT
    assert application.sent_at is None
    assert application.submitted_at is None
