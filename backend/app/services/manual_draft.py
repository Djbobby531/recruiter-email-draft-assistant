"""
Manual/ad-hoc draft creation - the Settings/Applications UI "paste an email"
box. Builds a synthetic Gmail-message-shaped payload from user-pasted text
and hands it to the exact same pipeline.process_message used for real
incoming Gmail messages, so it gets identical treatment: recruiter
selection, JD extraction, interview screening, resume matching/
customization, and (RULE J) draft-only creation - never a real send. The
only difference from a real email is where the raw dict comes from.
"""
from __future__ import annotations

import base64
import uuid


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8")


def build_synthetic_raw_message(sender_email: str, receiver_email: str, subject: str, body: str) -> dict:
    """A single-part plain-text message shaped exactly like what
    `users.messages.get(format="full")` returns, but with a locally-
    generated id/threadId - this never corresponds to a real Gmail message,
    which is fine: draft creation always makes a brand-new standalone thread
    regardless of the input's thread_id (see pipeline.py), so the resulting
    draft is fully real and functional in Gmail."""
    return {
        "id": f"manual-{uuid.uuid4().hex}",
        "threadId": f"manual-thread-{uuid.uuid4().hex}",
        "payload": {
            "headers": [
                {"name": "From", "value": sender_email},
                {"name": "To", "value": receiver_email or ""},
                {"name": "Subject", "value": subject or ""},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64(body)},
        },
    }
