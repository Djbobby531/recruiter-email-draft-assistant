"""
ATT-01 .. ATT-07: attachment handling (section 16 of the hardening spec).
"""
from __future__ import annotations

from app.models import ProcessingStatus
from app.services.gmail_service import GmailClient
from app.services.pipeline import process_message
from tests.conftest import make_raw_message, make_resume
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


def _raw(message_id):
    return make_raw_message(
        message_id=message_id, thread_id=f"thread-{message_id}",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )


def _seed_strong_resume(db_session, filename="databricks_resume.pdf"):
    return make_resume(
        db_session, filename,
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"], "devops_tools": ["terraform", "ci/cd"],
        },
        years=8, titles=["Senior Data Engineer"],
    )


def test_att01_correct_pdf_attached(db_session, settings, candidate_profile, fake_gmail_client):
    resume = _seed_strong_resume(db_session)
    process_message(db_session, settings, _raw("att01"), fake_gmail_client, ai_provider=None)
    assert fake_gmail_client.created_drafts[0]["attachment_path"] == resume.file_path


def test_att02_only_selected_resume_attached_not_all(db_session, settings, candidate_profile, fake_gmail_client):
    strong = _seed_strong_resume(db_session)
    make_resume(db_session, "healthcare_resume.pdf", skills={"domains": ["healthcare"]}, years=3)
    make_resume(db_session, "python_resume.pdf", skills={"languages": ["python"]}, years=2)

    process_message(db_session, settings, _raw("att02"), fake_gmail_client, ai_provider=None)

    assert len(fake_gmail_client.created_drafts) == 1
    draft = fake_gmail_client.created_drafts[0]
    assert draft["attachment_path"] == strong.file_path
    # gmail client's create_draft_with_attachment is called with a single
    # attachment_path argument (not a list) - structurally impossible to attach more than one
    assert isinstance(draft["attachment_path"], str)


def test_att03_wrong_resume_never_attached(db_session, settings, candidate_profile, fake_gmail_client):
    strong = _seed_strong_resume(db_session)
    weak = make_resume(db_session, "healthcare_resume.pdf", skills={"domains": ["healthcare"]}, years=3)

    process_message(db_session, settings, _raw("att03"), fake_gmail_client, ai_provider=None)

    attached_path = fake_gmail_client.created_drafts[0]["attachment_path"]
    assert attached_path != weak.file_path
    assert attached_path == strong.file_path


def test_att04_resume_file_missing_on_disk_does_not_create_misleading_draft(
    db_session, settings, candidate_profile
):
    """If the selected resume's file has been deleted from disk out-of-band,
    the real GmailClient's file open() will raise - the pipeline must catch
    that, mark ERROR, and must NOT have created a draft."""
    _seed_strong_resume(db_session)  # file_path points at a nonexistent /tmp path already

    class RealisticFailingClient:
        def create_draft_with_attachment(self, to_email, cc_email, subject, body_text, attachment_path, thread_id=None):
            with open(attachment_path, "rb") as f:  # mirrors the real GmailClient's behavior
                f.read()
            raise AssertionError("should never reach here - file does not exist")

    result = process_message(db_session, settings, _raw("att04"), RealisticFailingClient(), ai_provider=None)
    assert result.status == ProcessingStatus.ERROR
    assert result.draft is None


def test_att05_corrupted_resume_is_excluded_from_matching_routes_to_manual_review(
    db_session, settings, candidate_profile, fake_gmail_client
):
    from app.models import Resume
    corrupted = Resume(
        filename="corrupted.pdf", file_path="/tmp/corrupted.pdf",
        extracted_text="", extracted_metadata={"error": "could not parse PDF"},
        indexing_status="FAILED",
    )
    db_session.add(corrupted)
    db_session.commit()

    result = process_message(db_session, settings, _raw("att05"), fake_gmail_client, ai_provider=None)
    assert result.status == ProcessingStatus.MANUAL_REVIEW
    assert len(fake_gmail_client.created_drafts) == 0


def test_att06_filename_with_spaces_and_special_characters_works(db_session, settings, candidate_profile, fake_gmail_client):
    resume = _seed_strong_resume(db_session, filename="Diwakar Jilakara - Databricks Resume (2026)!.pdf")
    process_message(db_session, settings, _raw("att06"), fake_gmail_client, ai_provider=None)
    assert fake_gmail_client.created_drafts[0]["attachment_path"] == resume.file_path


def test_att07_large_pdf_handled_gracefully_by_real_gmail_client(tmp_path):
    """The real GmailClient must be able to build a MIME draft for a large
    attachment without raising - exercised directly against GmailClient's
    MIME-building logic using a stub Gmail API service object."""
    large_file = tmp_path / "large_resume.pdf"
    large_file.write_bytes(b"%PDF-1.4\n" + (b"0" * (5 * 1024 * 1024)))  # ~5MB

    captured = {}

    class StubDraftsResource:
        def create(self, userId, body):
            captured["body"] = body
            class _Exec:
                def execute(self_inner):
                    return {"id": "draft-large-1"}
            return _Exec()

    class StubUsersResource:
        def drafts(self):
            return StubDraftsResource()

    class StubService:
        def users(self):
            return StubUsersResource()

    client = GmailClient.__new__(GmailClient)  # bypass __init__ (no real OAuth needed)
    client.credentials = None
    client.service = StubService()

    response = client.create_draft_with_attachment(
        to_email="naveen@pamten.com", cc_email="james@algebrait.com",
        subject="Application - Senior Data Engineer", body_text="Hi Naveen,\n...",
        attachment_path=str(large_file),
    )
    assert response["id"] == "draft-large-1"
    assert "raw" in captured["body"]["message"]
