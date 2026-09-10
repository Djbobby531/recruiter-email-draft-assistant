"""
Application audit trail (section 25). Every lifecycle-relevant thing that
happens to an application - draft created, email sent, status changed - is
recorded as an immutable ApplicationEvent row, powering the application
detail page's timeline (section 16).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.models import ApplicationEvent


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def add_event(
    db: Session,
    application_id: int,
    event_type: str,
    metadata: dict | None = None,
    *,
    commit: bool = True,
) -> ApplicationEvent:
    event = ApplicationEvent(
        application_id=application_id,
        event_type=event_type,
        event_timestamp=utcnow(),
        event_metadata=metadata or {},
    )
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    return event
