"""
Extended FastAPI TestClient coverage: manual-review resolution, Gmail
OAuth callback/disconnect, and resume replace/download endpoints.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models import Application, CandidateProfile, GmailAccount, ProcessedMessage, ProcessingStatus, Resume

MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>/MediaBox[0 0 300 144]/Contents 5 0 R>>endobj\n"
    b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"5 0 obj<</Length 63>>stream\n"
    b"BT /F1 12 Tf 10 100 Td (Databricks Python SQL Data Engineer) Tj ET\n"
    b"endstream\nendobj\n"
    b"xref\n0 6\n0000000000 65535 f \n"
    b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n0\n%%EOF"
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    test_settings = Settings(RESUME_DIR=str(tmp_path / "resumes"))
    app.dependency_overrides[get_settings] = lambda: test_settings

    async def _noop_polling_loop(stop_event):
        await stop_event.wait()

    monkeypatch.setattr(main_module, "polling_loop", _noop_polling_loop)

    with TestClient(app) as c:
        yield c, TestSessionLocal

    app.dependency_overrides.clear()


def _seed_manual_review_application(session_factory):
    db = session_factory()
    profile = CandidateProfile(
        name="Diwakar Jilakara", experience="8+ years", work_authorization="US Citizen",
        phone="555-123-4567", email="diwakar@example.com", linkedin="linkedin.com/in/diwakar",
    )
    resume = Resume(
        filename="databricks_resume.pdf", file_path="/tmp/does-not-need-to-exist.pdf",
        extracted_text="databricks python sql airflow",
        extracted_metadata={
            "skills": {"data_engineering_tools": ["databricks", "airflow"], "languages": ["python", "sql"]},
            "years_of_experience": 8, "job_titles": ["Senior Data Engineer"],
        },
        indexing_status="INDEXED",
    )
    message = ProcessedMessage(
        gmail_message_id="manual-1", thread_id="manual-1-t", from_email="james@algebrait.com",
        subject="Role: Senior Data Engineer", status=ProcessingStatus.MANUAL_REVIEW,
    )
    db.add_all([profile, resume, message])
    db.commit()
    application = Application(
        source_message_id=message.id, thread_id="manual-1-t", cc_email="james@algebrait.com",
        job_title="Senior Data Engineer (Databricks)", job_location="Remote",
        requirements=["databricks", "python", "sql"],
        review_reason="low recruiter-email confidence",
        candidate_recruiter_emails=[],
    )
    db.add(application)
    db.commit()
    app_id = application.id
    db.close()
    return app_id


def test_manual_review_list_returns_pending_applications(client):
    c, session_factory = client
    _seed_manual_review_application(session_factory)
    resp = c.get("/api/applications/manual-review")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_resolve_manual_review_creates_draft_without_gmail_connected(client):
    c, session_factory = client
    app_id = _seed_manual_review_application(session_factory)

    resp = c.post(
        f"/api/applications/{app_id}/resolve-review",
        json={"recruiter_email": "naveen.gangupamu@pamten.com", "recruiter_name": "Naveen Gangupamu"},
    )
    assert resp.status_code == 200
    draft = resp.json()
    assert draft["to_email"] == "naveen.gangupamu@pamten.com"
    assert draft["cc_email"] == "james@algebrait.com"
    assert draft["gmail_draft_id"] is None  # no GmailAccount connected in this test

    # a second resolve attempt on the same (now-resolved) application must be rejected
    resp2 = c.post(
        f"/api/applications/{app_id}/resolve-review",
        json={"recruiter_email": "someone-else@pamten.com"},
    )
    assert resp2.status_code == 400


def test_resolve_manual_review_without_profile_returns_400(client):
    c, session_factory = client
    db = session_factory()
    resume = Resume(
        filename="r.pdf", file_path="/tmp/r.pdf", extracted_text="python",
        extracted_metadata={"skills": {"languages": ["python"]}, "years_of_experience": 2, "job_titles": []},
        indexing_status="INDEXED",
    )
    message = ProcessedMessage(
        gmail_message_id="manual-2", thread_id="manual-2-t", from_email="james@algebrait.com",
        subject="Role", status=ProcessingStatus.MANUAL_REVIEW,
    )
    db.add_all([resume, message])
    db.commit()
    application = Application(
        source_message_id=message.id, thread_id="manual-2-t", cc_email="james@algebrait.com",
        job_title="Data Engineer", requirements=[], review_reason="no recruiter", candidate_recruiter_emails=[],
    )
    db.add(application)
    db.commit()
    app_id = application.id
    db.close()

    resp = c.post(f"/api/applications/{app_id}/resolve-review", json={"recruiter_email": "x@y.com"})
    assert resp.status_code == 400


def test_skip_application_marks_message_skipped(client):
    c, session_factory = client
    app_id = _seed_manual_review_application(session_factory)
    resp = c.post(f"/api/applications/{app_id}/skip")
    assert resp.status_code == 200

    db = session_factory()
    message = db.query(ProcessedMessage).filter(ProcessedMessage.gmail_message_id == "manual-1").first()
    assert message.status == ProcessingStatus.SKIPPED_NOT_JOB
    db.close()


def test_get_application_and_draft_after_resolve(client):
    c, session_factory = client
    app_id = _seed_manual_review_application(session_factory)
    c.post(f"/api/applications/{app_id}/resolve-review", json={"recruiter_email": "naveen@pamten.com"})

    get_resp = c.get(f"/api/applications/{app_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["recruiter_email"] == "naveen@pamten.com"

    draft_resp = c.get(f"/api/applications/{app_id}/draft")
    assert draft_resp.status_code == 200
    assert draft_resp.json()["to_email"] == "naveen@pamten.com"


def test_gmail_disconnect_when_no_account_is_a_noop(client):
    c, _ = client
    resp = c.post("/api/gmail/disconnect")
    assert resp.status_code == 200
    assert resp.json() == {"connected": False}


def test_gmail_disconnect_removes_existing_account(client):
    c, session_factory = client
    db = session_factory()
    db.add(GmailAccount(email_address="diwakar@example.com", token_json="{}"))
    db.commit()
    db.close()

    status_resp = c.get("/api/gmail/status")
    assert status_resp.json()["connected"] is True

    disconnect_resp = c.post("/api/gmail/disconnect")
    assert disconnect_resp.status_code == 200

    status_resp2 = c.get("/api/gmail/status")
    assert status_resp2.json()["connected"] is False


def test_resume_replace_updates_filename_and_reindexes(client):
    c, session_factory = client
    upload_resp = c.post(
        "/api/resumes", files={"file": ("original.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")}
    )
    resume_id = upload_resp.json()["id"]

    replace_resp = c.post(
        f"/api/resumes/{resume_id}/replace",
        files={"file": ("updated.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")},
    )
    assert replace_resp.status_code == 200
    assert replace_resp.json()["filename"] == "updated.pdf"
    assert replace_resp.json()["id"] == resume_id


def test_resume_replace_rejects_non_pdf(client):
    c, _ = client
    upload_resp = c.post(
        "/api/resumes", files={"file": ("original.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")}
    )
    resume_id = upload_resp.json()["id"]

    resp = c.post(
        f"/api/resumes/{resume_id}/replace",
        files={"file": ("notes.txt", io.BytesIO(b"hi"), "text/plain")},
    )
    assert resp.status_code == 400


def test_resume_replace_nonexistent_returns_404(client):
    c, _ = client
    resp = c.post(
        "/api/resumes/99999/replace",
        files={"file": ("x.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")},
    )
    assert resp.status_code == 404


def test_resume_download_returns_file(client):
    c, _ = client
    upload_resp = c.post(
        "/api/resumes", files={"file": ("original.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")}
    )
    resume_id = upload_resp.json()["id"]

    resp = c.get(f"/api/resumes/{resume_id}/file")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")


def test_resume_download_nonexistent_returns_404(client):
    c, _ = client
    resp = c.get("/api/resumes/99999/file")
    assert resp.status_code == 404
