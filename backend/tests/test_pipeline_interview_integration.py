"""
Pipeline-level integration for the interview-screening step (sections 9, 14-15
of the recruiter-CRM hardening spec): the skip must happen BEFORE resume
matching/email generation/draft creation, must produce an Opportunity record
(not an Application/Draft), and a remote/unknown interview must sail through
the existing pipeline unaffected.

By explicit configuration choice, the skip is scoped ONLY to a genuinely
in-person/face-to-face/F2F interview requirement (interview_type ==
"IN_PERSON") - a HYBRID classification (even if some AI response were to also
set requires_in_person_interview=True on it) never skips, since the interview
itself is at least partly doable remotely. See test_hybrid_interview_never_
skips_even_if_requires_in_person_interview_is_set below. "Onsite" wording is
its own type (ONSITE) and also never skips - see
test_onsite_interview_never_skips_only_literal_in_person_does below.
"""
from __future__ import annotations

from app.models import Application, Draft, Opportunity, ProcessingStatus, Recruiter
from app.services.pipeline import process_message
from tests.conftest import make_raw_message, make_resume

JD_BODY_TEMPLATE = """James

Algebra IT LLC

E: james@algebrait.com

---------- Forwarded message ----------
From: Naveen Gangupamu <naveen.gangupamu@pamten.com>
Sent: Friday, September 4, 2026

Role: Senior Data Engineer (Databricks)
Location: Irvine, CA or Los Angeles, CA (Hybrid) (Local Preferred)

Required Skills: Databricks, Python, SQL, Airflow

{interview}

Thanks and Regards,
Naveen Gangupamu
Talent Acquisition Executive
PAMTEN
Phone: (737) 304-8920
"""

SUBJECT = "Fw: Hiring for || Role: Senior Data Engineer (Databricks) Location: Irvine, CA or Los Angeles, CA ||"


def _seed_resume(db_session):
    return make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"], "devops_tools": ["terraform", "ci/cd"],
        },
        years=8, titles=["Senior Data Engineer"],
    )


def _raw(message_id, interview_text, thread_id=None):
    return make_raw_message(
        message_id=message_id, thread_id=thread_id or f"thread-{message_id}",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SUBJECT, plain_body=JD_BODY_TEMPLATE.format(interview=interview_text),
    )


def test_in_person_interview_skips_before_resume_matching_no_application_no_draft(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    raw = _raw(
        "interview-skip-1",
        "First round will be conducted via Zoom and final interview will be conducted in-person at the client office.",
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW
    assert result.application is None
    assert result.draft is None
    assert len(fake_gmail_client.created_drafts) == 0

    # NO Application/Draft row was ever created for this message
    assert db_session.query(Application).count() == 0
    assert db_session.query(Draft).count() == 0

    # but an Opportunity record WAS created, carrying the full interview detail
    assert result.opportunity is not None
    opp = result.opportunity
    assert opp.status == ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW
    assert opp.interview_type == "IN_PERSON"
    assert opp.requires_in_person_interview is True
    assert opp.interview_confidence >= 0.9
    assert "in-person" in opp.interview_reason.lower() or "in-person" in (opp.interview_evidence or "").lower()
    assert opp.skip_reason is not None
    assert opp.job_title == "Senior Data Engineer (Databricks)"


def test_onsite_interview_never_skips_only_literal_in_person_does(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """"Onsite" wording (as opposed to literal in-person/face-to-face/F2F
    wording) must proceed through the normal pipeline like HYBRID/UNKNOWN -
    it is a distinct, non-skipping ONSITE classification."""
    _seed_resume(db_session)
    raw = _raw(
        "interview-onsite-1",
        "First round will be conducted via Zoom and final interview will be conducted onsite at the client office.",
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application is not None
    assert result.draft is not None
    assert len(fake_gmail_client.created_drafts) == 1
    assert result.opportunity.interview_type == "ONSITE"
    assert result.opportunity.requires_in_person_interview is False


def test_in_person_interview_still_records_recruiter_and_role_history(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    raw = _raw("interview-skip-2", "Candidates must attend an in-person interview.")
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    recruiter = db_session.query(Recruiter).filter(Recruiter.normalized_email == "naveen.gangupamu@pamten.com").first()
    assert recruiter is not None
    assert recruiter.display_name == "Naveen Gangupamu"
    assert recruiter.company == "PAMTEN"
    assert recruiter.recruiter_role == "Talent Acquisition Executive"
    assert recruiter.phone == "(737) 304-8920"
    assert recruiter.opportunities_count == 1
    assert recruiter.skipped_count == 1
    assert recruiter.drafts_count == 0
    assert len(recruiter.roles) == 1
    assert recruiter.roles[0].job_title == "Senior Data Engineer (Databricks)"


def test_hybrid_interview_never_skips_even_if_requires_in_person_interview_is_set(
    db_session, settings, candidate_profile, fake_gmail_client, monkeypatch
):
    """The skip is scoped strictly to interview_type == 'IN_PERSON' - a
    HYBRID classification must always draft, even in the edge case of an AI
    response setting requires_in_person_interview=True on a HYBRID result
    (the interview itself is still at least partly remote-doable)."""
    from app.services import pipeline
    from app.services.interview_classifier import InterviewClassification

    _seed_resume(db_session)
    monkeypatch.setattr(
        pipeline.interview_classifier, "classify_interview_requirement",
        lambda text, ai_provider=None: InterviewClassification(
            interview_type="HYBRID", requires_in_person_interview=True,
            confidence=0.8, reason="hybrid interview arrangement", evidence="hybrid",
        ),
    )
    raw = _raw("interview-hybrid-1", "Interview process TBD.")
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application is not None
    assert result.draft is not None
    assert result.opportunity.interview_type == "HYBRID"
    assert result.opportunity.requires_in_person_interview is True
    assert result.opportunity.status == ProcessingStatus.DRAFT_CREATED


def test_remote_interview_continues_through_normal_pipeline(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    raw = _raw("interview-remote-1", "All interviews will be conducted via Zoom.")
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application is not None
    assert result.draft is not None
    assert len(fake_gmail_client.created_drafts) == 1

    assert result.opportunity.interview_type == "REMOTE"
    assert result.opportunity.requires_in_person_interview is False
    assert result.opportunity.status == ProcessingStatus.DRAFT_CREATED
    assert result.opportunity.draft_id == result.draft.id
    assert result.opportunity.selected_resume_id == result.application.selected_resume_id

    recruiter = db_session.query(Recruiter).filter(Recruiter.normalized_email == "naveen.gangupamu@pamten.com").first()
    assert recruiter.drafts_count == 1
    assert recruiter.skipped_count == 0


def test_unknown_interview_format_continues_through_normal_pipeline(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    raw = _raw("interview-unknown-1", "Interview details will be discussed later.")
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.opportunity.interview_type == "UNKNOWN"
    assert result.opportunity.requires_in_person_interview is False


def test_opportunity_carries_over_to_application_interview_fields(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    raw = _raw("interview-remote-2", "Interview via Microsoft Teams.")
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.application.interview_type == "REMOTE"
    assert result.application.requires_in_person_interview is False
    assert result.application.interview_confidence is not None


def test_manual_review_due_to_resume_mismatch_still_creates_opportunity_record(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """No resumes are seeded, so resume matching fails -> MANUAL_REVIEW - the
    Opportunity record must still exist and reflect that outcome."""
    raw = _raw("interview-manual-1", "Interview via Zoom.")
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.MANUAL_REVIEW
    assert result.opportunity is not None
    assert result.opportunity.status == ProcessingStatus.MANUAL_REVIEW
    assert result.opportunity.interview_type == "REMOTE"  # interview screening still ran


def test_duplicate_in_person_skip_message_does_not_double_count_recruiter_stats(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    raw = _raw("interview-dup-1", "Final interview is in-person.")
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    result2 = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result2.status == ProcessingStatus.SKIPPED_DUPLICATE
    recruiter = db_session.query(Recruiter).filter(Recruiter.normalized_email == "naveen.gangupamu@pamten.com").first()
    assert recruiter.opportunities_count == 1
    assert recruiter.skipped_count == 1
    assert db_session.query(Opportunity).count() == 1
