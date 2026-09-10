"""
Integration tests for the full pipeline, covering the 12 required test cases
from the product spec (section 21).
"""
from __future__ import annotations

from app.models import Application, ProcessingStatus
from app.services.pipeline import process_message
from tests.conftest import make_raw_message, make_resume

SAMPLE_SUBJECT = (
    "Fw: Hiring for || Role: Senior Data Engineer (Databricks) "
    "Location: Irvine, CA or Los Angeles, CA (Hybrid) (Local Preferred) ||"
)

SAMPLE_BODY = """James

Algebra IT LLC

E: james@algebrait.com

---------- Forwarded message ----------
From: Naveen Gangupamu <naveen.gangupamu@pamten.com>
Sent: Friday, September 4, 2026
To: James AlgebraIT <james@algebrait.com>

Role: Senior Data Engineer (Databricks)

Location: Irvine, CA or Los Angeles, CA (Hybrid) (Local Preferred)

Responsibilities:
- Design, build, and enhance platform capabilities within Databricks.
- Build reusable data products.
- Design and maintain data pipelines.
- Improve data quality, reliability, lineage, and monitoring.

Skills:
- Databricks
- Python
- SQL
- Terraform
- Data Governance
- Unity Catalog
- Airflow
- CI/CD
- AI agents
"""


def _make_databricks_resume(db_session):
    return make_resume(
        db_session,
        "databricks_resume.pdf",
        skills={
            "cloud_platforms": ["azure", "aws"],
            "data_engineering_tools": ["databricks", "airflow", "unity catalog", "delta lake"],
            "languages": ["python", "sql"],
            "devops_tools": ["terraform", "ci/cd"],
            "governance_cert": ["data governance", "unity catalog"],
        },
        years=8,
        titles=["Senior Data Engineer"],
    )


def _make_unrelated_resume(db_session):
    return make_resume(
        db_session,
        "healthcare_resume.pdf",
        skills={"domains": ["healthcare"], "languages": ["java"]},
        years=3,
        titles=["Healthcare Business Analyst"],
    )


def test_01_recruiter_identified_inside_body_not_sender(db_session, settings, candidate_profile, fake_gmail_client):
    _make_databricks_resume(db_session)
    raw = make_raw_message(
        message_id="m1", thread_id="t1",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application.recruiter_email == "naveen.gangupamu@pamten.com"
    assert result.application.cc_email == "james@algebrait.com"
    assert len(fake_gmail_client.created_drafts) == 1
    draft = fake_gmail_client.created_drafts[0]
    assert draft["to_email"] == "naveen.gangupamu@pamten.com"
    assert draft["cc_email"] == "james@algebrait.com"


def test_02_multiple_emails_selects_exactly_one_draft(db_session, settings, candidate_profile, fake_gmail_client):
    _make_databricks_resume(db_session)
    body_with_many_emails = SAMPLE_BODY + "\ncc: someone-else@pamten.com, another@otherco.com\n"
    raw = make_raw_message(
        message_id="m2", thread_id="t2",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=body_with_many_emails,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert len(fake_gmail_client.created_drafts) == 1
    applications = db_session.query(Application).all()
    assert len(applications) == 1


def test_03_reply_to_existing_application_no_draft(db_session, settings, candidate_profile, fake_gmail_client):
    _make_databricks_resume(db_session)
    raw1 = make_raw_message(
        message_id="m3a", thread_id="t3",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    first = process_message(db_session, settings, raw1, fake_gmail_client, ai_provider=None)
    assert first.status == ProcessingStatus.DRAFT_CREATED

    reply_raw = make_raw_message(
        message_id="m3b", thread_id="t3",
        from_header="Naveen Gangupamu <naveen.gangupamu@pamten.com>",
        subject="Re: " + SAMPLE_SUBJECT, plain_body="Hi Diwakar, can we schedule a call?",
    )
    second = process_message(db_session, settings, reply_raw, fake_gmail_client, ai_provider=None)

    assert second.status == ProcessingStatus.SKIPPED_REPLY
    assert len(fake_gmail_client.created_drafts) == 1  # still just the one from before


def test_04_no_recruiter_email_falls_back_to_sender_and_drafts(db_session, settings, candidate_profile, fake_gmail_client):
    """No other candidate email anywhere - the sender becomes the recruiter
    contact as a last-resort fallback rather than blocking for manual
    review."""
    _make_databricks_resume(db_session)
    body = (
        "Job Description\n\n"
        "Role: Senior Data Engineer\nLocation: Remote\nContract: W2\n\n"
        "Responsibilities:\n- Build data pipelines\n\n"
        "Required Skills:\n- Databricks\n- Python\n- SQL\n\n"
        "Years of experience: 5+\n\n"
        "No recruiter contact details of any kind are included anywhere in this message."
    )
    raw = make_raw_message(
        message_id="m4", thread_id="t4",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject="Role: Senior Data Engineer", plain_body=body,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert len(fake_gmail_client.created_drafts) == 1
    assert fake_gmail_client.created_drafts[0]["to_email"] == "james@algebrait.com"


def test_05_unrelated_promotion_email_ignored(db_session, settings, candidate_profile, fake_gmail_client):
    _make_databricks_resume(db_session)
    raw = make_raw_message(
        message_id="m5", thread_id="t5",
        from_header="Deals <deals@shopping-site.com>",
        subject="50% off your next order!",
        plain_body="Huge sale this weekend only. Unsubscribe here. Visit our store for the best deals.",
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.SKIPPED_NOT_JOB
    assert len(fake_gmail_client.created_drafts) == 0


def test_06_similar_resumes_returns_scores_and_selects_highest(db_session, settings, candidate_profile, fake_gmail_client):
    strong = _make_databricks_resume(db_session)
    weak = _make_unrelated_resume(db_session)
    raw = make_raw_message(
        message_id="m6", thread_id="t6",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application.selected_resume_id == strong.id
    assert result.application.selected_resume_id != weak.id
    assert result.application.match_score is not None and result.application.match_score > 0


def test_07_location_present_included_in_subject(db_session, settings, candidate_profile, fake_gmail_client):
    _make_databricks_resume(db_session)
    raw = make_raw_message(
        message_id="m7", thread_id="t7",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert "Irvine, CA" in result.application.generated_subject
    assert result.application.generated_subject.startswith("Application")


def test_08_no_location_subject_does_not_invent_one(db_session, settings, candidate_profile, fake_gmail_client):
    _make_databricks_resume(db_session)
    body = (
        "Job Description\n\n"
        "Role: Senior Data Engineer (Databricks)\nContract: W2\n\n"
        "Responsibilities:\n- Build and maintain data pipelines\n\n"
        "Required Skills:\n- Databricks\n- Python\n- SQL\n- Airflow\n- Terraform\n- Data Governance\n\n"
        "Years of experience: 5+\n\n"
        "Recruiter: Naveen Gangupamu <naveen.gangupamu@pamten.com>\n"
    )
    raw = make_raw_message(
        message_id="m8", thread_id="t8",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject="Role: Senior Data Engineer (Databricks)", plain_body=body,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    subject = result.application.generated_subject
    assert subject == "Application for Senior Data Engineer (Databricks)"
    assert "–  –" not in subject
    assert not subject.rstrip().endswith("–")


def test_09_candidate_current_location_never_leaks(db_session, settings, candidate_profile, fake_gmail_client):
    _make_databricks_resume(db_session)
    raw = make_raw_message(
        message_id="m9", thread_id="t9",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    body = result.application.generated_body
    forbidden_locations = ["San Francisco", "New York", "Austin", "Seattle", "Chicago"]
    for loc in forbidden_locations:
        assert loc not in body
    # candidate profile has no location field at all - structurally impossible to leak
    assert not hasattr(candidate_profile, "location")


def test_10_exactly_one_resume_attached(db_session, settings, candidate_profile, fake_gmail_client):
    resume = _make_databricks_resume(db_session)
    raw = make_raw_message(
        message_id="m10", thread_id="t10",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert len(fake_gmail_client.created_drafts) == 1
    assert fake_gmail_client.created_drafts[0]["attachment_path"] == resume.file_path


def test_11_same_message_processed_twice_only_one_draft(db_session, settings, candidate_profile, fake_gmail_client):
    _make_databricks_resume(db_session)
    raw = make_raw_message(
        message_id="m11", thread_id="t11",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    first = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    second = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert first.status == ProcessingStatus.DRAFT_CREATED
    assert second.status == ProcessingStatus.SKIPPED_DUPLICATE
    assert len(fake_gmail_client.created_drafts) == 1


def test_12_recruiter_reply_in_same_thread_no_new_draft(db_session, settings, candidate_profile, fake_gmail_client):
    _make_databricks_resume(db_session)
    raw = make_raw_message(
        message_id="m12a", thread_id="t12",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    reply = make_raw_message(
        message_id="m12b", thread_id="t12",
        from_header="Naveen Gangupamu <naveen.gangupamu@pamten.com>",
        subject="Re: " + SAMPLE_SUBJECT,
        plain_body="Thanks for applying, let's set up a call this week.",
    )
    result = process_message(db_session, settings, reply, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.SKIPPED_REPLY
    assert len(fake_gmail_client.created_drafts) == 1
