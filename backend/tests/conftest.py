from __future__ import annotations

import base64
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models import CandidateProfile, Resume
from tests.fixtures import load_json, load_text


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def settings():
    return Settings(
        MY_EMAIL="diwakar@example.com",
        AI_PROVIDER="none",
        JOB_CLASSIFICATION_THRESHOLD=0.5,
        RECRUITER_EMAIL_CONFIDENCE_THRESHOLD=0.55,
    )


@pytest.fixture()
def candidate_profile(db_session):
    profile = CandidateProfile(
        name="Diwakar Jilakara",
        experience="8+ years",
        work_authorization="Authorized to work in the US",
        phone="555-123-4567",
        email="diwakar@example.com",
        linkedin="linkedin.com/in/diwakar",
    )
    db_session.add(profile)
    db_session.commit()
    return profile


def make_resume(db_session, filename: str, skills: dict, years: int = 8, titles=None, status="INDEXED"):
    resume = Resume(
        filename=filename,
        file_path=f"/tmp/{uuid.uuid4().hex}_{filename}",
        extracted_text=" ".join(sum(skills.values(), [])),
        extracted_metadata={"skills": skills, "years_of_experience": years, "job_titles": titles or []},
        indexing_status=status,
    )
    db_session.add(resume)
    db_session.commit()
    db_session.refresh(resume)
    return resume


def make_resume_from_fixture(db_session, fixture_filename: str, status: str = "INDEXED") -> Resume:
    """Load a resume metadata fixture from tests/fixtures/resumes/<fixture_filename>.json."""
    data = load_json("resumes", fixture_filename)
    resume = Resume(
        filename=data["filename"],
        file_path=f"/tmp/{uuid.uuid4().hex}_{data['filename']}",
        extracted_text=" ".join(sum(data["skills"].values(), [])),
        extracted_metadata={
            "skills": data["skills"],
            "years_of_experience": data.get("years_of_experience"),
            "job_titles": data.get("job_titles", []),
        },
        indexing_status=status,
    )
    db_session.add(resume)
    db_session.commit()
    db_session.refresh(resume)
    return resume


def load_jd(filename: str) -> str:
    return load_text("jds", filename)


def load_forwarded_email(filename: str) -> str:
    return load_text("forwarded_emails", filename)


def load_reply_email(filename: str) -> str:
    return load_text("replies", filename)


def load_recruiter_email(filename: str) -> str:
    return load_text("recruiter_emails", filename)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8")


def make_raw_message(
    message_id: str,
    thread_id: str,
    from_header: str,
    subject: str,
    plain_body: str = "",
    html_body: str | None = None,
    message_id_header: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    to_header: str = "diwakar@example.com",
) -> dict:
    headers = [
        {"name": "From", "value": from_header},
        {"name": "To", "value": to_header},
        {"name": "Subject", "value": subject},
    ]
    if message_id_header:
        headers.append({"name": "Message-ID", "value": message_id_header})
    if in_reply_to:
        headers.append({"name": "In-Reply-To", "value": in_reply_to})
    if references:
        headers.append({"name": "References", "value": references})

    parts = [{"mimeType": "text/plain", "body": {"data": _b64(plain_body)}}]
    if html_body:
        parts.append({"mimeType": "text/html", "body": {"data": _b64(html_body)}})

    return {
        "id": message_id,
        "threadId": thread_id,
        "payload": {"headers": headers, "mimeType": "multipart/mixed", "parts": parts},
    }


class FakeGmailClient:
    """Captures create_draft_with_attachment calls instead of hitting the real
    API, and simulates thread state for sent-message-detection tests."""

    def __init__(self):
        self.created_drafts: list[dict] = []
        # thread_id -> list of Gmail-shaped message dicts (see get_thread)
        self.threads: dict[str, list[dict]] = {}
        self.get_thread_error: Exception | None = None

    def create_draft_with_attachment(self, to_email, cc_email, subject, body_text, attachment_path, thread_id=None):
        # Mirrors real Gmail behavior: without an explicit thread_id, Gmail
        # assigns the message a brand new thread of its own (this is the
        # normal path now - drafts are created as standalone new emails,
        # never threaded under the original recruiter email).
        resulting_thread_id = thread_id or f"newthread-{uuid.uuid4().hex[:8]}"
        draft = {
            "id": f"draft-{uuid.uuid4().hex[:8]}",
            "to_email": to_email,
            "cc_email": cc_email,
            "subject": subject,
            "body_text": body_text,
            "attachment_path": attachment_path,
            "thread_id": thread_id,
        }
        self.created_drafts.append(draft)
        return {"id": draft["id"], "message": {"id": f"msg-{uuid.uuid4().hex[:8]}", "threadId": resulting_thread_id}}

    def get_thread(self, thread_id: str) -> dict:
        if self.get_thread_error is not None:
            raise self.get_thread_error
        return {"id": thread_id, "messages": self.threads.get(thread_id, [])}

    def simulate_message(self, thread_id: str, to: str, subject: str, labels: list[str]) -> str:
        """Appends a Gmail-shaped message (e.g. with labelIds=["SENT"]) to a
        thread, as if the user had just sent/received it. Returns the new
        message's id."""
        message_id = f"msg-{uuid.uuid4().hex[:8]}"
        message = {
            "id": message_id,
            "threadId": thread_id,
            "labelIds": labels,
            "payload": {"headers": [{"name": "To", "value": to}, {"name": "Subject", "value": subject}]},
        }
        self.threads.setdefault(thread_id, []).append(message)
        return message_id


@pytest.fixture()
def fake_gmail_client():
    return FakeGmailClient()


class FlakyGmailClient(FakeGmailClient):
    """A FakeGmailClient that can be told to raise on the next N calls, to
    simulate Gmail API timeouts/outages/HttpErrors for failure-recovery tests."""

    def __init__(self, fail_times: int = 0, exception_factory=None):
        super().__init__()
        self.fail_times = fail_times
        self.exception_factory = exception_factory or (lambda: TimeoutError("simulated Gmail API timeout"))
        self.attempt_count = 0

    def create_draft_with_attachment(self, *args, **kwargs):
        self.attempt_count += 1
        if self.attempt_count <= self.fail_times:
            raise self.exception_factory()
        return super().create_draft_with_attachment(*args, **kwargs)
