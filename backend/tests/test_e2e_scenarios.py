"""
E2E-01 .. E2E-15: complete end-to-end pipeline scenarios (section 18 of the
hardening spec). Each test drives process_message() exactly as the real
poller would, with a mocked Gmail client and no AI provider.
"""
from __future__ import annotations

from app.models import ProcessingStatus
from app.services.pipeline import process_message
from tests.conftest import load_forwarded_email, make_raw_message, make_resume
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


def _seed_resume(db_session, filename="databricks_resume.pdf", **overrides):
    defaults = dict(
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"], "devops_tools": ["terraform", "ci/cd"],
        },
        years=8, titles=["Senior Data Engineer"],
    )
    defaults.update(overrides)
    return make_resume(db_session, filename, **defaults)


def test_e2e01a_simple_recruiter_email_sender_only_drafts_with_sender_as_recruiter(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """A "simple" direct recruiter email where the sender is the only email
    address anywhere in the message. By explicit configuration choice, the
    sender becomes the recruiter contact as a last-resort fallback (rather
    than blocking for manual review) - there's no other candidate to use, and
    the sender genuinely is the point of contact in this scenario."""
    _seed_resume(db_session)
    raw = make_raw_message(
        message_id="e2e01a", thread_id="e2e01a-t", from_header="recruiter@pamten.com",
        subject="Role: Senior Data Engineer (Databricks)",
        plain_body="Role: Senior Data Engineer (Databricks)\nLocation: Remote\nRequired Skills: Databricks, Python, SQL",
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert len(fake_gmail_client.created_drafts) == 1
    assert fake_gmail_client.created_drafts[0]["to_email"] == "recruiter@pamten.com"
    assert fake_gmail_client.created_drafts[0]["cc_email"] is None


def test_e2e01b_simple_recruiter_email_with_distinct_signature_contact_drafts(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """A simple (non-forwarded) recruiter email that includes a distinguishable,
    named, cross-domain point of contact in its signature (not just the
    sender) - this supplies the strong "different contact" signal RULE B looks
    for even without a forward, and should successfully produce a draft."""
    _seed_resume(db_session)
    raw = make_raw_message(
        message_id="e2e01b", thread_id="e2e01b-t", from_header="notifications@vendorportal.io",
        subject="Role: Senior Data Engineer (Databricks)",
        plain_body=(
            "Role: Senior Data Engineer (Databricks)\nLocation: Remote\n"
            "Required Skills: Databricks, Python, SQL\n\n"
            "Regards,\nSarah Kim\nTalent Acquisition Executive\nsarah.kim@bigstaffingfirm.com"
        ),
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application.recruiter_email == "sarah.kim@bigstaffingfirm.com"


def test_e2e02_forwarded_recruiter_email(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_resume(db_session)
    body = load_forwarded_email("gmail_style.txt")
    raw = make_raw_message(
        message_id="e2e02", thread_id="e2e02-t", from_header="james@algebrait.com",
        subject="Fwd: Senior Data Engineer (Databricks)", plain_body=body,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application.recruiter_email == "recruiter@example.com"
    assert result.application.cc_email == "james@algebrait.com"


def test_e2e03_outlook_forwarded_recruiter_email_inside_gmail(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_resume(db_session)
    body = load_forwarded_email("outlook_style.txt")
    raw = make_raw_message(
        message_id="e2e03", thread_id="e2e03-t", from_header="james@algebrait.com",
        subject="Fwd: Role: Senior Data Engineer (Databricks)", plain_body=body,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application.recruiter_email == "naveen.gangupamu@pamten.com"


def test_e2e04_multiple_email_addresses_selects_exactly_one_recruiter(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    body = SAMPLE_BODY + "\ncc: another@otherco.com, yetanother@thirdco.com\n"
    raw = make_raw_message(
        message_id="e2e04", thread_id="e2e04-t", from_header="james@algebrait.com",
        subject=SAMPLE_SUBJECT, plain_body=body,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert len(fake_gmail_client.created_drafts) == 1


def test_e2e05_original_sender_becomes_cc_recruiter_becomes_to(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    raw = make_raw_message(
        message_id="e2e05", thread_id="e2e05-t", from_header="james@algebrait.com",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert fake_gmail_client.created_drafts[0]["to_email"] == "naveen.gangupamu@pamten.com"
    assert fake_gmail_client.created_drafts[0]["cc_email"] == "james@algebrait.com"


def test_e2e06_reply_to_previous_application_does_nothing(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_resume(db_session)
    raw = make_raw_message(
        message_id="e2e06a", thread_id="e2e06-t", from_header="james@algebrait.com",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    reply = make_raw_message(
        message_id="e2e06b", thread_id="e2e06-t", from_header="naveen.gangupamu@pamten.com",
        subject="Re: " + SAMPLE_SUBJECT, plain_body="Can we talk this week?",
    )
    result = process_message(db_session, settings, reply, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.SKIPPED_REPLY
    assert len(fake_gmail_client.created_drafts) == 1


def test_e2e07_same_email_processed_twice_one_draft(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_resume(db_session)
    raw = make_raw_message(
        message_id="e2e07", thread_id="e2e07-t", from_header="james@algebrait.com",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    result2 = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result2.status == ProcessingStatus.SKIPPED_DUPLICATE
    assert len(fake_gmail_client.created_drafts) == 1


def test_e2e08_forwarded_email_with_no_identifiable_inner_contact_falls_back_to_forwarder(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """A genuine forward (has forward markers) whose forwarder didn't leave any
    distinguishable inner contact - since there's no other candidate email
    anywhere, the forwarder/sender becomes the recruiter contact as a
    last-resort fallback rather than blocking for manual review."""
    _seed_resume(db_session)
    raw = make_raw_message(
        message_id="e2e08", thread_id="e2e08-t", from_header="james@algebrait.com",
        subject="Fwd: Role: Senior Data Engineer",
        plain_body=(
            "FYI, see below.\n\n"
            "---------- Forwarded message ---------\n"
            "Job Description\nRole: Senior Data Engineer\nContract: W2\n\n"
            "Responsibilities:\n- Build pipelines\n\nRequired Skills:\n- Python\n\n"
            "No contact information is included anywhere in the forwarded content."
        ),
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert len(fake_gmail_client.created_drafts) == 1
    assert fake_gmail_client.created_drafts[0]["to_email"] == "james@algebrait.com"
    assert fake_gmail_client.created_drafts[0]["cc_email"] is None


def test_e2e09_not_a_job_email_ignored(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_resume(db_session)
    raw = make_raw_message(
        message_id="e2e09", thread_id="e2e09-t", from_header="deals@shopping-site.com",
        subject="Flash sale this weekend!", plain_body="Unsubscribe here. Huge sale on everything.",
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.SKIPPED_NOT_JOB
    assert len(fake_gmail_client.created_drafts) == 0


def test_e2e10_strong_resume_match_attaches_correct_resume(db_session, settings, candidate_profile, fake_gmail_client):
    strong = _seed_resume(db_session, filename="databricks_resume.pdf")
    make_resume(db_session, "healthcare_resume.pdf", skills={"domains": ["healthcare"]}, years=3)
    raw = make_raw_message(
        message_id="e2e10", thread_id="e2e10-t", from_header="james@algebrait.com",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.application.selected_resume_id == strong.id


def test_e2e11_low_resume_match_still_drafts_with_best_available_resume(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """A low match score no longer blocks draft creation - the best (only)
    resume on file is attached anyway, with the low score/explanation
    recorded on the application for later review."""
    resume = make_resume(db_session, "unrelated_resume.pdf", skills={"domains": ["healthcare"], "languages": ["java"]}, years=2)
    raw = make_raw_message(
        message_id="e2e11", thread_id="e2e11-t", from_header="james@algebrait.com",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application.selected_resume_id == resume.id


def test_e2e12_job_with_multiple_locations_shows_just_the_first_in_subject(db_session, settings, candidate_profile, fake_gmail_client):
    """The subject line is meant to be a concise 'City, ST' - when the JD
    lists more than one location, only the first is used rather than
    stacking them all up."""
    _seed_resume(db_session)
    raw = make_raw_message(
        message_id="e2e12", thread_id="e2e12-t", from_header="james@algebrait.com",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert "Irvine, CA" in result.application.generated_subject
    assert "Los Angeles, CA" not in result.application.generated_subject


def test_e2e13_job_without_location_no_fabricated_location(db_session, settings, candidate_profile, fake_gmail_client):
    _seed_resume(db_session)
    raw = make_raw_message(
        message_id="e2e13", thread_id="e2e13-t", from_header="james@algebrait.com",
        subject="Role: Senior Data Engineer (Databricks)",
        plain_body=(
            "Job Description\nRole: Senior Data Engineer (Databricks)\nContract: W2\n\n"
            "Responsibilities:\n- Build pipelines\n\n"
            "Required Skills:\n- Databricks\n- Python\n- SQL\n\n"
            "Recruiter: Naveen Gangupamu <naveen.gangupamu@pamten.com>\n"
        ),
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application.generated_subject == "Application for Senior Data Engineer (Databricks)"


def test_e2e14_recruiter_reply_after_generated_application_no_new_draft(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    raw = make_raw_message(
        message_id="e2e14a", thread_id="e2e14-t", from_header="james@algebrait.com",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    reply = make_raw_message(
        message_id="e2e14b", thread_id="e2e14-t", from_header="naveen.gangupamu@pamten.com",
        subject="Re: " + SAMPLE_SUBJECT, plain_body="Thanks for applying! Let's schedule a call.",
    )
    result = process_message(db_session, settings, reply, fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.SKIPPED_REPLY
    assert len(fake_gmail_client.created_drafts) == 1


def test_e2e15_new_unrelated_job_from_same_recruiter_gets_new_draft(
    db_session, settings, candidate_profile, fake_gmail_client
):
    _seed_resume(db_session)
    raw1 = make_raw_message(
        message_id="e2e15a", thread_id="e2e15-thread-1", from_header="james@algebrait.com",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    process_message(db_session, settings, raw1, fake_gmail_client, ai_provider=None)

    unrelated_body = (
        "James\nAlgebra IT LLC\nE: james@algebrait.com\n\n"
        "---------- Forwarded message ----------\n"
        "From: Naveen Gangupamu <naveen.gangupamu@pamten.com>\n\n"
        "Role: Data Platform Architect\nLocation: Austin, TX\n\n"
        "Required Skills:\n- Kubernetes\n- Terraform\n- Go\n"
    )
    raw2 = make_raw_message(
        message_id="e2e15b", thread_id="e2e15-thread-2",  # a genuinely new, unrelated thread
        from_header="james@algebrait.com",
        subject="Role: Data Platform Architect Location: Austin, TX", plain_body=unrelated_body,
    )
    result2 = process_message(db_session, settings, raw2, fake_gmail_client, ai_provider=None)
    # this JD is unrelated to the seeded databricks resume, so it correctly
    # routes to manual review rather than misattaching a poor-fit resume -
    # the key assertion is that it is NOT suppressed as a reply/duplicate.
    assert result2.status in (ProcessingStatus.DRAFT_CREATED, ProcessingStatus.MANUAL_REVIEW)
    assert result2.status != ProcessingStatus.SKIPPED_REPLY
    assert result2.status != ProcessingStatus.SKIPPED_DUPLICATE
