"""
Sections 24-25: permanent end-to-end regression fixtures for the recruiter-CRM
+ interview-screening scenario. Per project policy these must never be
deleted or weakened, even after any bug they once caught is fixed.
"""
from __future__ import annotations

from app.models import Application, Draft, ProcessingStatus, Recruiter
from app.services.pipeline import process_message
from tests.conftest import make_raw_message, make_resume

SUBJECT = "Fw: Hiring for || Role: Senior Data Engineer (Databricks) Location: Irvine, CA or Los Angeles, CA (Hybrid) (Local Preferred) ||"

BODY_TEMPLATE = """James

Algebra IT LLC

E: james@algebrait.com

---------- Forwarded message ----------
From: Naveen Gangupamu <naveen.gangupamu@pamten.com>
Sent: Friday, September 4, 2026
To: James AlgebraIT <james@algebrait.com>

Role: Senior Data Engineer (Databricks)

Location: Irvine, CA or Los Angeles, CA

Responsibilities:
- Design, build, and enhance platform capabilities within Databricks.

Skills:
- Databricks
- Python
- SQL
- Terraform
- Airflow

{interview}

Thanks and Regards,
Naveen Gangupamu
Talent Acquisition Executive
PAMTEN
Phone: (737) 304-8920
"""


def _seed_databricks_resume(db_session):
    return make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "cloud_platforms": ["azure", "aws"],
            "data_engineering_tools": ["databricks", "airflow", "unity catalog", "delta lake"],
            "languages": ["python", "sql"],
            "devops_tools": ["terraform", "ci/cd"],
        },
        years=8, titles=["Senior Data Engineer"],
    )


def test_section24_positive_e2e_in_person_interview_skips_everything(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """
    Section 24 (as narrowed by explicit configuration choice: only literal
    in-person/face-to-face/F2F wording skips, "onsite" wording never does -
    see interview_classifier module docstring): "First round will be
    conducted via Zoom and final interview will be conducted in-person at the
    client office." -> IN_PERSON -> SKIPPED, with recruiter/company/role/phone
    still fully captured, and NO resume matching, email generation, or Gmail
    draft ever performed.
    """
    _seed_databricks_resume(db_session)
    body = BODY_TEMPLATE.format(
        interview="First round will be conducted via Zoom and final interview will be conducted in-person at the client office."
    )
    raw = make_raw_message(
        message_id="sec24-e2e", thread_id="sec24-e2e-thread",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=body,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW
    assert result.opportunity.interview_type == "IN_PERSON"
    assert result.opportunity.status == ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW

    # NO draft, NO application, NO resume matching ever performed
    assert result.application is None
    assert result.draft is None
    assert len(fake_gmail_client.created_drafts) == 0
    assert db_session.query(Application).count() == 0
    assert db_session.query(Draft).count() == 0
    assert result.opportunity.selected_resume_id is None
    assert result.opportunity.resume_match_score is None

    recruiter = db_session.query(Recruiter).filter(Recruiter.normalized_email == "naveen.gangupamu@pamten.com").first()
    assert recruiter is not None
    assert recruiter.display_name == "Naveen Gangupamu"
    assert recruiter.company == "PAMTEN"
    assert recruiter.recruiter_role == "Talent Acquisition Executive"
    assert recruiter.phone == "(737) 304-8920"


def test_section25_negative_e2e_remote_interview_creates_full_application(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """
    Section 25: same scenario, but "All interviews will be conducted via
    Zoom." -> REMOTE -> processing continues through resume matching, email
    generation, and Gmail draft creation exactly as before this phase.
    """
    resume = _seed_databricks_resume(db_session)
    body = BODY_TEMPLATE.format(interview="All interviews will be conducted via Zoom.")
    raw = make_raw_message(
        message_id="sec25-e2e", thread_id="sec25-e2e-thread",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=body,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.opportunity.interview_type == "REMOTE"

    draft_call = fake_gmail_client.created_drafts[0]
    assert draft_call["to_email"] == "naveen.gangupamu@pamten.com"
    assert draft_call["cc_email"] == "james@algebrait.com"
    assert draft_call["subject"] == "Application for Senior Data Engineer (Databricks) - Irvine, CA"
    assert draft_call["attachment_path"] == resume.file_path
