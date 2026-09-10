"""
Processed Emails page API: sender/receiver visibility, search, status
filter, and the row-click detail view (including the customized-resume
download endpoint it links to).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models import Application, ProcessedMessage, ProcessingStatus, Resume


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path/'test.db'}", connect_args={"check_same_thread": False})
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


def _seed_message(db, msg_id="m1", thread_id="t1", from_email="james@algebrait.com",
                   to_email="diwakar@example.com", subject="Role: Data Engineer",
                   status=ProcessingStatus.DRAFT_CREATED):
    message = ProcessedMessage(
        gmail_message_id=msg_id, thread_id=thread_id, from_email=from_email,
        to_email=to_email, subject=subject, status=status,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def test_processed_message_includes_sender_and_receiver(client):
    c, sf = client
    db = sf()
    _seed_message(db)
    db.close()
    resp = c.get("/api/messages")
    data = resp.json()
    assert data[0]["from_email"] == "james@algebrait.com"
    assert data[0]["to_email"] == "diwakar@example.com"


def test_filter_processed_messages_by_status(client):
    c, sf = client
    db = sf()
    _seed_message(db, msg_id="m1", status=ProcessingStatus.DRAFT_CREATED)
    _seed_message(db, msg_id="m2", status=ProcessingStatus.SKIPPED_NOT_JOB)
    db.close()
    resp = c.get("/api/messages", params={"status": "DRAFT_CREATED"})
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "DRAFT_CREATED"


def test_search_processed_messages_by_sender(client):
    c, sf = client
    db = sf()
    _seed_message(db, msg_id="m1", from_email="naveen@pamten.com")
    _seed_message(db, msg_id="m2", from_email="james@algebrait.com")
    db.close()
    resp = c.get("/api/messages", params={"search": "pamten"})
    assert len(resp.json()) == 1


def test_search_processed_messages_by_receiver(client):
    c, sf = client
    db = sf()
    _seed_message(db, msg_id="m1", to_email="test.candidate@example.com")
    _seed_message(db, msg_id="m2", to_email="other@example.com")
    db.close()
    resp = c.get("/api/messages", params={"search": "test.candidate"})
    assert len(resp.json()) == 1


def test_search_processed_messages_by_subject(client):
    c, sf = client
    db = sf()
    _seed_message(db, msg_id="m1", subject="Senior Data Engineer opportunity")
    _seed_message(db, msg_id="m2", subject="Newsletter")
    db.close()
    resp = c.get("/api/messages", params={"search": "Data Engineer"})
    assert len(resp.json()) == 1


def test_message_detail_includes_linked_application(client):
    c, sf = client
    db = sf()
    message = _seed_message(db)
    application = Application(
        source_message_id=message.id, thread_id=message.thread_id,
        recruiter_name="Naveen", recruiter_email="naveen@pamten.com",
        job_title="Senior Data Engineer", status="DRAFT",
    )
    db.add(application)
    db.commit()
    message_id = message.id
    db.close()

    resp = c.get(f"/api/messages/{message_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["application"]["job_title"] == "Senior Data Engineer"


def test_message_detail_application_is_null_when_none_exists(client):
    c, sf = client
    db = sf()
    message = _seed_message(db)
    message_id = message.id
    db.close()

    resp = c.get(f"/api/messages/{message_id}")
    assert resp.json()["application"] is None


def test_message_detail_not_found(client):
    c, _ = client
    assert c.get("/api/messages/999999").status_code == 404


def test_resume_file_endpoint_serves_customized_resume_when_present(client, tmp_path):
    c, sf = client
    db = sf()
    resume_path = tmp_path / "original.docx"
    resume_path.write_bytes(b"original docx bytes")
    resume = Resume(filename="original.docx", file_path=str(resume_path), indexing_status="INDEXED")
    db.add(resume)
    db.commit()

    customized_path = tmp_path / "customized.docx"
    customized_path.write_bytes(b"customized docx bytes")

    message = _seed_message(db)
    application = Application(
        source_message_id=message.id, thread_id=message.thread_id,
        selected_resume_id=resume.id, customized_resume_path=str(customized_path), status="DRAFT",
    )
    db.add(application)
    db.commit()
    app_id = application.id
    db.close()

    resp = c.get(f"/api/applications/{app_id}/resume-file")
    assert resp.status_code == 200
    assert resp.content == b"customized docx bytes"


def test_resume_file_endpoint_falls_back_to_original_when_not_customized(client, tmp_path):
    c, sf = client
    db = sf()
    resume_path = tmp_path / "original.pdf"
    resume_path.write_bytes(b"original pdf bytes")
    resume = Resume(filename="original.pdf", file_path=str(resume_path), indexing_status="INDEXED")
    db.add(resume)
    db.commit()

    message = _seed_message(db)
    application = Application(
        source_message_id=message.id, thread_id=message.thread_id,
        selected_resume_id=resume.id, status="DRAFT",
    )
    db.add(application)
    db.commit()
    app_id = application.id
    db.close()

    resp = c.get(f"/api/applications/{app_id}/resume-file")
    assert resp.status_code == 200
    assert resp.content == b"original pdf bytes"


def test_resume_file_endpoint_404_when_no_resume_attached(client):
    c, sf = client
    db = sf()
    message = _seed_message(db)
    application = Application(source_message_id=message.id, thread_id=message.thread_id, status="MANUAL_REVIEW")
    db.add(application)
    db.commit()
    app_id = application.id
    db.close()

    resp = c.get(f"/api/applications/{app_id}/resume-file")
    assert resp.status_code == 404
