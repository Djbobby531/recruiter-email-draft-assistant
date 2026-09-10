"""
Gmail sent-message detection (sections 3, 17; ST-01..ST-09).

CRITICAL RULE: a Gmail draft existing is NEVER sufficient evidence that an
application was sent. This module only ever flips DRAFT -> SENT when it finds
an actual message carrying the Gmail SENT label, in the application's own
thread, whose recipient and subject match what the draft was created with.
Any ambiguity (Gmail unavailable, no matching message yet) leaves the
application exactly as DRAFT - "Awaiting send confirmation" in the UI.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.models import Application, AppStatus, Draft, EventType
from app.services.application_events import add_event

logger = logging.getLogger("app.sent_detection")

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fwd?)\s*:\s*)+", re.IGNORECASE)

_RATE_LIMIT_STATUSES = (403, 429)
_NOT_FOUND_STATUSES = (404,)

# Set on Draft.status once its Gmail thread comes back 404 - almost always
# because the user deleted the draft directly in Gmail. That's a permanent,
# not a transient, condition: retrying it every poll cycle forever would
# just spam identical "failed to fetch thread" warnings for something that
# will never succeed, so it's checked once, recorded, and then skipped.
DRAFT_STATUS_MISSING_IN_GMAIL = "MISSING_IN_GMAIL"


def _is_rate_limit_error(exc: Exception) -> bool:
    if not isinstance(exc, HttpError):
        return False
    status = exc.resp.status if getattr(exc, "resp", None) else None
    return status in _RATE_LIMIT_STATUSES


def _is_not_found_error(exc: Exception) -> bool:
    if not isinstance(exc, HttpError):
        return False
    status = exc.resp.status if getattr(exc, "resp", None) else None
    return status in _NOT_FOUND_STATUSES


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _normalize_subject(subject: str) -> str:
    return _SUBJECT_PREFIX_RE.sub("", subject or "").strip().lower()


def _header(message: dict, name: str) -> str:
    for h in message.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def find_sent_message_id(thread_messages: list[dict], to_email: str, subject: str) -> str | None:
    """Multi-signal match (section 17): Gmail SENT label + matching recipient
    + matching subject (Re:/Fwd: noise ignored), scoped to a single thread by
    the caller. Returns the Gmail message id of the first confident match, or
    None if nothing in this thread looks like the application's sent email -
    e.g. an unrelated message the user separately sent in the same thread
    with a different recipient never matches (ST-07)."""
    to_norm = (to_email or "").strip().lower()
    subject_norm = _normalize_subject(subject)
    if not to_norm:
        return None

    for message in thread_messages:
        if "SENT" not in message.get("labelIds", []):
            continue
        to_header = _header(message, "To").lower()
        if to_norm not in to_header:
            continue
        if subject_norm and _normalize_subject(_header(message, "Subject")) != subject_norm:
            continue
        return message.get("id")
    return None


def sync_application_sent_status(db: Session, gmail_client, application: Application, draft: Draft) -> bool:
    """Checks whether `draft` has actually been sent from Gmail. Returns True
    only if the application's status was just updated to SENT. Idempotent: a
    second call on an already-SENT application is a guaranteed no-op (ST-05),
    and any Gmail-side failure leaves the application exactly as it was,
    safe to retry next cycle (ST-09)."""
    if application.status != AppStatus.DRAFT:
        return False
    if draft is None or not draft.to_email:
        return False

    try:
        thread = gmail_client.get_thread(application.thread_id)
    except Exception as exc:
        if _is_rate_limit_error(exc):
            # Let the caller (run_sent_sync_cycle) see this and stop checking
            # further applications this cycle, instead of continuing to
            # hammer an already-rate-limited API with doomed requests.
            raise
        if _is_not_found_error(exc):
            # The thread is permanently gone (almost always: the user deleted
            # the draft directly in Gmail) - this will never succeed no
            # matter how many times it's retried, so record it once and stop
            # checking this application again, rather than logging the same
            # "failed to fetch thread" warning every single poll cycle.
            logger.warning(
                "sent-detection: thread %s no longer exists in Gmail (likely deleted by the user) - "
                "will stop checking this application",
                application.thread_id,
            )
            draft.status = DRAFT_STATUS_MISSING_IN_GMAIL
            db.add(draft)
            db.commit()
            return False
        logger.warning("sent-detection: failed to fetch thread %s (will retry next cycle)", application.thread_id)
        return False

    messages = (thread or {}).get("messages", [])
    sent_message_id = find_sent_message_id(messages, draft.to_email, draft.subject)
    if sent_message_id is None:
        return False

    now = _utcnow()
    application.status = AppStatus.SENT
    application.sent_message_id = sent_message_id
    application.sent_at = now
    application.updated_at = now
    db.add(application)
    add_event(db, application.id, EventType.EMAIL_SENT, {"sent_message_id": sent_message_id}, commit=False)
    db.commit()
    db.refresh(application)
    return True


def run_sent_sync_cycle(db: Session, gmail_client) -> int:
    """Scans every application still awaiting send confirmation and checks
    Gmail for a matching sent message. Returns the number newly marked SENT.
    Never re-scans the whole mailbox - only the specific thread each
    tracked draft belongs to. If Gmail starts rate-limiting these lookups
    (a real risk once many drafts have accumulated - each is a separate API
    call), this stops checking further applications for the rest of THIS
    cycle rather than blasting through the remaining ones with requests that
    are doomed to fail too - the untouched ones simply get checked on the
    next poll cycle instead."""
    updated = 0
    candidates = db.query(Application).filter(Application.status == AppStatus.DRAFT).all()
    for application in candidates:
        draft = application.draft
        if draft is None or draft.status == DRAFT_STATUS_MISSING_IN_GMAIL:
            continue
        try:
            if sync_application_sent_status(db, gmail_client, application, draft):
                updated += 1
        except Exception as exc:
            if _is_rate_limit_error(exc):
                logger.warning(
                    "sent-detection: Gmail rate limit hit after checking %d application(s) this "
                    "cycle - stopping early, remaining applications will be checked next cycle",
                    updated,
                )
                break
            logger.exception("sent-detection failed for application %s", application.id)
    return updated
