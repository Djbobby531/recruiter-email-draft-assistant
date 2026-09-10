"""
Section 26: permanent regression fixture for the canonical example from the
product spec. This test must NEVER be deleted or weakened, even after any bug
it once caught is fixed - per project policy on regression fixtures.

Fixture source: tests/fixtures/recruiter_emails/exact_spec_example.json +
exact_spec_example_body.txt
"""
from __future__ import annotations

from app.services.pipeline import process_message
from tests.conftest import make_raw_message, make_resume
from tests.fixtures import load_json, load_text


def test_exact_spec_example_produces_the_documented_result(
    db_session, settings, candidate_profile, fake_gmail_client
):
    fixture = load_json("recruiter_emails", "exact_spec_example.json")
    body = load_text("recruiter_emails", fixture["body_file"])

    make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "cloud_platforms": ["azure", "aws"],
            "data_engineering_tools": ["databricks", "airflow", "unity catalog", "delta lake"],
            "languages": ["python", "sql"],
            "devops_tools": ["terraform", "ci/cd"],
            "governance_cert": ["data governance", "unity catalog"],
        },
        years=8, titles=["Senior Data Engineer"],
    )
    # a deliberately weaker resume must never be chosen over the strong match
    make_resume(db_session, "healthcare_resume.pdf", skills={"domains": ["healthcare"], "languages": ["java"]}, years=3)

    raw = make_raw_message(
        message_id="spec-exact-1", thread_id="spec-exact-thread-1",
        from_header=fixture["from_header"], subject=fixture["subject"], plain_body=body,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status.value == "DRAFT_CREATED"
    assert len(fake_gmail_client.created_drafts) == 1
    draft_call = fake_gmail_client.created_drafts[0]

    expected = fixture["expected"]
    assert draft_call["to_email"] == expected["to_email"]
    assert draft_call["cc_email"] == expected["cc_email"]
    assert draft_call["subject"] == expected["subject"]

    # selected resume must be the Databricks/data-engineering one, not healthcare
    assert draft_call["attachment_path"] == result.application.selected_resume.file_path
    assert "databricks" in result.application.selected_resume.filename.lower()

    # candidate current location must never appear anywhere, even though
    # this JD is location-heavy - the JD's own job location (Irvine, CA) is
    # fine in both subject and body, since it's never candidate-identifying
    for forbidden_city in ["San Francisco", "New York", "Chicago", "Seattle"]:
        assert forbidden_city not in draft_call["body_text"]
        assert forbidden_city not in draft_call["subject"]
    assert "Irvine, CA" in draft_call["subject"]
    assert "Irvine, CA" in draft_call["body_text"]
