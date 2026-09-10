"""
End-to-end integration between the pipeline and the new application-tracker
fields (dashboard phase): recruiter_id, local_requirement, implementation
partner/end client, the DRAFT-only status rule, and the event timeline.
"""
from __future__ import annotations

from app.models import Application, ApplicationEvent, AppStatus, EventType, ProcessedMessage, ProcessingStatus
from app.services.pipeline import process_message
from tests.conftest import make_raw_message, make_resume

SUBJECT = "Fw: Hiring for || Role: Senior Data Engineer (Databricks) Location: Irvine, CA ||"

BODY = """James

Algebra IT LLC

E: james@algebrait.com

---------- Forwarded message ----------
From: Naveen Gangupamu <naveen.gangupamu@pamten.com>
Sent: Friday, September 4, 2026
To: James AlgebraIT <james@algebrait.com>

Implementation Partner: PAMTEN
End Client: ABC Financial

Role: Senior Data Engineer (Databricks)

Location: Irvine, CA

Local candidates preferred.

Skills:
- Databricks
- Python
- SQL

All interviews will be conducted via Zoom.

Thanks and Regards,
Naveen Gangupamu
Talent Acquisition Executive
PAMTEN
Phone: (737) 304-8920
"""


def test_full_pipeline_populates_application_tracker_fields(db_session, settings, candidate_profile, fake_gmail_client):
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"data_engineering_tools": ["databricks"], "languages": ["python", "sql"]},
        years=8, titles=["Senior Data Engineer"],
    )
    raw = make_raw_message(
        message_id="tracker-1", thread_id="tracker-thread-1",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    application = result.application
    assert application is not None
    assert application.recruiter_id is not None
    assert application.local_requirement == "PREFERRED"
    assert application.implementation_partner == "PAMTEN"
    assert application.end_client == "ABC Financial"

    # Critical rule: creating a Gmail draft alone must NEVER mark SENT/SUBMITTED.
    assert application.status == AppStatus.DRAFT
    assert application.sent_at is None
    assert application.sent_message_id is None
    assert application.submitted_at is None

    events = (
        db_session.query(ApplicationEvent)
        .filter(ApplicationEvent.application_id == application.id)
        .order_by(ApplicationEvent.id)
        .all()
    )
    event_types = [e.event_type for e in events]
    assert event_types == [
        EventType.EMAIL_RECEIVED, EventType.JOB_DETECTED, EventType.RESUME_SELECTED, EventType.DRAFT_CREATED,
    ]


def test_sender_only_email_falls_back_to_sender_as_recruiter_and_drafts(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """No recruiter contact email anywhere other than the sender itself no
    longer blocks for manual review - the sender becomes the recruiter
    contact as a last-resort fallback, is upserted into the recruiter
    directory, and a draft is still created (with no separate CC, since the
    sender IS the recruiter here). JD extraction still runs normally, so
    local_requirement/etc. are populated from the body as usual."""
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"data_engineering_tools": ["databricks"], "languages": ["python", "sql"]},
        years=8, titles=["Senior Data Engineer"],
    )
    body = (
        "Job Description\n\n"
        "Role: Senior Data Engineer\nLocation: Remote\nContract: W2\n\n"
        "Responsibilities:\n- Build data pipelines\n\n"
        "Required Skills:\n- Databricks\n- Python\n- SQL\n\n"
        "Years of experience: 5+\n\n"
        "No recruiter contact details of any kind are included anywhere in this message."
    )
    raw = make_raw_message(
        message_id="tracker-2", thread_id="tracker-thread-2",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject="Role: Senior Data Engineer", plain_body=body,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    application = result.application
    assert application.status == AppStatus.DRAFT
    assert application.recruiter_id is not None
    assert application.recruiter_email == "james@algebrait.com"
    assert application.cc_email is None  # no separate address left to CC
    draft_call = fake_gmail_client.created_drafts[0]
    assert draft_call["to_email"] == "james@algebrait.com"
    assert draft_call["cc_email"] is None


def test_no_resume_match_keeps_status_manual_review_not_draft(db_session, settings, candidate_profile, fake_gmail_client):
    # No resumes at all indexed -> match fails -> MANUAL_REVIEW, never DRAFT.
    raw = make_raw_message(
        message_id="tracker-3", thread_id="tracker-thread-3",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.MANUAL_REVIEW
    assert result.application.status == AppStatus.MANUAL_REVIEW


def test_application_table_query_reflects_pipeline_output(db_session, settings, candidate_profile, fake_gmail_client):
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"data_engineering_tools": ["databricks"], "languages": ["python", "sql"]},
        years=8, titles=["Senior Data Engineer"],
    )
    raw = make_raw_message(
        message_id="tracker-4", thread_id="tracker-thread-4",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    # The application's thread_id is updated to wherever Gmail actually
    # placed the new standalone draft (never the original received-email
    # thread - see the "brand new email, never a reply" rule), so look it up
    # by the message that produced it instead.
    record = db_session.query(ProcessedMessage).filter(ProcessedMessage.thread_id == "tracker-thread-4").first()
    stored = db_session.query(Application).filter(Application.source_message_id == record.id).first()
    assert stored is not None
    assert stored.status == AppStatus.DRAFT
    assert stored.thread_id != "tracker-thread-4"  # never threaded under the original email
    assert stored.implementation_partner == "PAMTEN"
