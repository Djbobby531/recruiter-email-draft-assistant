"""
RULE A - Reply detection.

An incoming message is a REPLY to something this app already generated/tracked if:
  - its thread_id matches an existing Application.thread_id, OR
  - its In-Reply-To header references a Message-ID we've already processed, OR
  - its References header contains a Message-ID we've already processed, OR
  - its thread_id matches any previously processed message (i.e. we've seen this
    thread before at all - the first message in a thread is never a "reply" in
    this sense, but a second message in the same thread is).

This is intentionally deterministic (no AI) since it gates a high-stakes decision.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Application, ProcessedMessage
from app.services.email_parser import ParsedEmail


def _referenced_message_ids(parsed: ParsedEmail) -> set[str]:
    ids: set[str] = set()
    if parsed.in_reply_to_header:
        ids.add(parsed.in_reply_to_header.strip())
    if parsed.references_header:
        for token in parsed.references_header.split():
            ids.add(token.strip())
    return {i for i in ids if i}


def is_reply(db: Session, parsed: ParsedEmail) -> tuple[bool, str | None]:
    """Returns (is_reply, reason)."""

    # 1. Thread already has a tracked application (we already applied in this thread)
    existing_app = (
        db.query(Application).filter(Application.thread_id == parsed.thread_id).first()
    )
    if existing_app is not None:
        return True, f"thread {parsed.thread_id} already has application #{existing_app.id}"

    # 2. Thread already has ANY previously processed message -> this is a follow-up
    #    message in a thread we've seen before (e.g. recruiter reply, our own sent copy).
    existing_msg = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.thread_id == parsed.thread_id)
        .filter(ProcessedMessage.gmail_message_id != parsed.gmail_message_id)
        .first()
    )
    if existing_msg is not None:
        return True, f"thread {parsed.thread_id} already contains processed message {existing_msg.gmail_message_id}"

    # 3. In-Reply-To / References point at a Message-ID we've processed before
    referenced = _referenced_message_ids(parsed)
    if referenced:
        prior = (
            db.query(ProcessedMessage)
            .filter(ProcessedMessage.message_id_header.in_(referenced))
            .first()
        )
        if prior is not None:
            return True, f"references previously processed message-id {prior.message_id_header}"

    return False, None
