"""
Application status lifecycle (sections 2, 4, 18, 30). Enforces the "not every
status is reachable from every other status" rule while still allowing an
explicit administrative override (logged as MANUAL_OVERRIDE) for the
exceptional case a user genuinely needs one.

CRITICAL: nothing in this module - or anywhere else - is allowed to move an
application into SENT except confirmed Gmail sent-message evidence
(services/sent_detection.py) or REJECTED/WITHDRAWN/ON_HOLD/SUBMITTED/INTERVIEW
via this validated, audited transition function.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.models import Application, AppStatus, EventType, InterviewStatusValue
from app.services.application_events import add_event

# Section 30's explicit lifecycle, plus ON_HOLD (section 4) as a normal
# waiting state reachable from/back-to the active statuses. MANUAL_REVIEW and
# SKIPPED are handled by their own dedicated endpoints (resolve-review/skip),
# not this generic transition, so they're left out of the "allowed" graph -
# any exit from them here requires an explicit override.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    AppStatus.DRAFT: {AppStatus.SENT, AppStatus.WITHDRAWN},
    AppStatus.SENT: {AppStatus.SUBMITTED, AppStatus.REJECTED, AppStatus.WITHDRAWN},
    AppStatus.SUBMITTED: {AppStatus.INTERVIEW, AppStatus.REJECTED, AppStatus.WITHDRAWN, AppStatus.ON_HOLD},
    AppStatus.INTERVIEW: {AppStatus.REJECTED, AppStatus.WITHDRAWN, AppStatus.ON_HOLD},
    AppStatus.ON_HOLD: {AppStatus.SUBMITTED, AppStatus.INTERVIEW, AppStatus.REJECTED, AppStatus.WITHDRAWN},
}

_EVENT_FOR_STATUS = {
    AppStatus.SENT: EventType.EMAIL_SENT,
    AppStatus.SUBMITTED: EventType.APPLICATION_SUBMITTED,
    AppStatus.INTERVIEW: EventType.INTERVIEW_REQUESTED,
    AppStatus.REJECTED: EventType.REJECTED,
    AppStatus.WITHDRAWN: EventType.WITHDRAWN,
    AppStatus.SKIPPED: EventType.SKIPPED,
}


class InvalidTransitionError(ValueError):
    pass


def is_allowed(current: str, new: str) -> bool:
    return new in ALLOWED_TRANSITIONS.get(current, set())


def transition(
    db: Session,
    application: Application,
    new_status: str,
    *,
    override: bool = False,
    notes: str | None = None,
) -> Application:
    if new_status not in AppStatus.ALL:
        raise InvalidTransitionError(f"unknown status: {new_status}")

    current = application.status
    allowed = is_allowed(current, new_status)
    if not allowed and not override:
        raise InvalidTransitionError(f"cannot transition {current} -> {new_status} without override=true")

    now = dt.datetime.now(dt.UTC)
    application.status = new_status
    application.updated_at = now

    if new_status == AppStatus.SUBMITTED:
        application.submitted_at = now
    elif new_status == AppStatus.INTERVIEW:
        application.interview_at = now
        if application.interview_status in (None, InterviewStatusValue.NOT_STARTED):
            application.interview_status = InterviewStatusValue.INTERVIEW_REQUESTED
    elif new_status == AppStatus.REJECTED and notes:
        application.review_reason = notes

    db.add(application)

    event_type = EventType.MANUAL_OVERRIDE if not allowed else _EVENT_FOR_STATUS.get(new_status, EventType.MANUAL_OVERRIDE)
    add_event(
        db, application.id, event_type,
        {"from_status": current, "to_status": new_status, "override": not allowed, "notes": notes},
        commit=False,
    )

    db.commit()
    db.refresh(application)
    return application
