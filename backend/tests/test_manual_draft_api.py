"""
Settings/Applications UI "paste an email" box: POST /api/messages/manual-draft
runs pasted JD text through the exact same pipeline as a real incoming Gmail
message - same JD extraction, resume matching, and (RULE J) draft-only
creation - but with an EXPLICIT send-to (and optional CC) address supplied
by the user, overriding auto-detection from the pasted text.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
import app.routers.messages as messages_module
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models import CandidateProfile, GmailAccount
from tests.conftest import FakeGmailClient, make_resume

SAMPLE_JD_BODY = """Role: Senior Data Engineer (Databricks)
Location: Irvine, CA

Required Skills: Databricks, Python, SQL, Airflow

Naveen Gangupamu
Talent Acquisition Executive
PAMTEN
naveen.gangupamu@pamten.com
Phone: (737) 304-8920
"""


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
    test_settings = Settings(RESUME_DIR=str(tmp_path / "resumes"), AI_PROVIDER="none")
    app.dependency_overrides[get_settings] = lambda: test_settings

    async def _noop_polling_loop(stop_event):
        await stop_event.wait()

    monkeypatch.setattr(main_module, "polling_loop", _noop_polling_loop)

    fake_client = FakeGmailClient()
    monkeypatch.setattr(messages_module, "get_gmail_client", lambda settings, token: fake_client)

    with TestClient(app) as c:
        yield c, TestSessionLocal, fake_client

    app.dependency_overrides.clear()


def _seed_account(db):
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}")
    db.add(account)
    db.commit()


def _seed_profile(db):
    db.add(CandidateProfile(
        name="Diwakar Jilakara", experience="8+ years", work_authorization="H1B",
        phone="555-123-4567", email="diwakar@example.com", linkedin="linkedin.com/in/diwakar",
    ))
    db.commit()


def test_manual_draft_requires_a_valid_sender_email(client):
    c, _, _ = client
    resp = c.post("/api/messages/manual-draft", json={
        "sender_email": "not-an-email", "receiver_email": "naveen@pamten.com",
        "subject": "Role", "body": "some text",
    })
    assert resp.status_code == 400


def test_manual_draft_requires_a_valid_receiver_email(client):
    c, _, _ = client
    resp = c.post("/api/messages/manual-draft", json={
        "sender_email": "james@algebrait.com", "receiver_email": "not-an-email",
        "subject": "Role", "body": "some text",
    })
    assert resp.status_code == 400
    assert "send to" in resp.json()["detail"].lower()


def test_manual_draft_requires_receiver_email_to_be_present(client):
    c, _, _ = client
    resp = c.post("/api/messages/manual-draft", json={
        "sender_email": "james@algebrait.com", "receiver_email": "",
        "subject": "Role", "body": "some text",
    })
    assert resp.status_code == 400


def test_manual_draft_rejects_an_invalid_cc_email(client):
    c, _, _ = client
    resp = c.post("/api/messages/manual-draft", json={
        "sender_email": "james@algebrait.com", "receiver_email": "naveen@pamten.com",
        "cc_email": "not-an-email", "subject": "Role", "body": "some text",
    })
    assert resp.status_code == 400
    assert "cc" in resp.json()["detail"].lower()


def test_manual_draft_requires_body_text(client):
    c, _, _ = client
    resp = c.post("/api/messages/manual-draft", json={
        "sender_email": "james@algebrait.com", "receiver_email": "naveen@pamten.com",
        "subject": "Role", "body": "   ",
    })
    assert resp.status_code == 400


def test_manual_draft_requires_gmail_connected(client):
    c, _, _ = client
    resp = c.post("/api/messages/manual-draft", json={
        "sender_email": "james@algebrait.com", "receiver_email": "naveen@pamten.com",
        "subject": "Role", "body": SAMPLE_JD_BODY,
    })
    assert resp.status_code == 400
    assert "gmail" in resp.json()["detail"].lower()


def test_manual_draft_sends_to_the_explicit_receiver_email_not_an_auto_detected_one(client):
    """The pasted body contains naveen.gangupamu@pamten.com in its signature,
    but the user explicitly asked to send to a different address - that
    explicit choice must win, never the auto-detected one."""
    c, TestSessionLocal, fake_client = client
    db = TestSessionLocal()
    _seed_account(db)
    _seed_profile(db)
    make_resume(
        db, "databricks_resume.pdf",
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"],
        },
        years=8, titles=["Senior Data Engineer"],
    )
    db.close()

    resp = c.post("/api/messages/manual-draft", json={
        "sender_email": "james@algebrait.com", "receiver_email": "jane.recruiter@techcorp.com",
        "subject": "Fw: Hiring for Senior Data Engineer", "body": SAMPLE_JD_BODY,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "DRAFT_CREATED"
    assert body["application_id"] is not None
    assert body["draft_id"] is not None
    assert body["recruiter_email"] == "jane.recruiter@techcorp.com"
    assert body["cc_email"] is None
    assert "Senior Data Engineer" in (body["generated_subject"] or "")

    # a real draft was actually created via the Gmail client, addressed
    # exactly to the explicitly-supplied recruiter email
    assert len(fake_client.created_drafts) == 1
    draft_call = fake_client.created_drafts[0]
    assert draft_call["to_email"] == "jane.recruiter@techcorp.com"
    assert draft_call["cc_email"] is None
    assert draft_call["body_text"].startswith("Hi,")


def test_manual_draft_includes_an_explicit_cc_email(client):
    c, TestSessionLocal, fake_client = client
    db = TestSessionLocal()
    _seed_account(db)
    _seed_profile(db)
    make_resume(
        db, "databricks_resume.pdf",
        skills={"data_engineering_tools": ["databricks"], "languages": ["python", "sql"]},
        years=8, titles=["Senior Data Engineer"],
    )
    db.close()

    resp = c.post("/api/messages/manual-draft", json={
        "sender_email": "james@algebrait.com", "receiver_email": "jane.recruiter@techcorp.com",
        "cc_email": "my.tracking@example.com", "subject": "Role", "body": SAMPLE_JD_BODY,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "DRAFT_CREATED"
    assert body["recruiter_email"] == "jane.recruiter@techcorp.com"
    assert body["cc_email"] == "my.tracking@example.com"

    assert len(fake_client.created_drafts) == 1
    assert fake_client.created_drafts[0]["cc_email"] == "my.tracking@example.com"


def test_manual_draft_two_submissions_never_collide_on_message_id(client):
    """Each manual submission must get its own unique synthetic message id -
    otherwise a second paste would be silently treated as a duplicate."""
    c, TestSessionLocal, fake_client = client
    db = TestSessionLocal()
    _seed_account(db)
    _seed_profile(db)
    make_resume(
        db, "databricks_resume.pdf",
        skills={"data_engineering_tools": ["databricks"], "languages": ["python", "sql"]},
        years=8, titles=["Senior Data Engineer"],
    )
    db.close()

    payload = {
        "sender_email": "james@algebrait.com", "receiver_email": "jane.recruiter@techcorp.com",
        "subject": "Role A", "body": SAMPLE_JD_BODY,
    }
    resp1 = c.post("/api/messages/manual-draft", json=payload)
    resp2 = c.post("/api/messages/manual-draft", json=payload)
    assert resp1.status_code == 200 and resp2.status_code == 200
    assert resp1.json()["application_id"] != resp2.json()["application_id"]
    assert len(fake_client.created_drafts) == 2
