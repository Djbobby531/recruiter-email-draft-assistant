"""
FastAPI TestClient coverage for the HTTP API layer (section 2 of the
hardening spec: "FastAPI TestClient"). Uses a temporary SQLite file and
temporary resume storage dir so it never touches the developer's real data/.
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

    # Route handlers that upload/replace resumes read settings.RESUME_DIR via
    # this dependency - without overriding it, uploaded test PDFs would land
    # in the developer's real local data/resumes/ directory. GOOGLE_CLIENT_ID/
    # SECRET are pinned explicitly (rather than left to fall through to a real
    # local .env, which may legitimately exist on a developer machine running
    # the actual app) so the "credentials not configured" test below stays
    # deterministic regardless of what's in that .env.
    test_settings = Settings(RESUME_DIR=str(tmp_path / "resumes"), GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="")
    app.dependency_overrides[get_settings] = lambda: test_settings

    # The real background poller opens its own DB session directly against
    # app.database.SessionLocal (correct for production - it's not tied to a
    # request), which would bypass this override and touch the developer's
    # real local data/db/app.db during tests. Router/HTTP-layer tests don't
    # need the poller running at all, so replace it with an inert stand-in
    # that still respects the stop event the same way the real loop does.
    async def _noop_polling_loop(stop_event):
        await stop_event.wait()

    monkeypatch.setattr(main_module, "polling_loop", _noop_polling_loop)

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def test_health_check(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_gmail_status_when_not_connected(client):
    resp = client.get("/api/gmail/status")
    assert resp.status_code == 200
    assert resp.json() == {"connected": False}


def test_gmail_oauth_start_without_credentials_returns_400(client):
    resp = client.get("/api/gmail/oauth/start")
    assert resp.status_code == 400


def test_profile_upsert_and_get_roundtrip(client):
    payload = {
        "name": "Diwakar Jilakara", "experience": "8+ years",
        "work_authorization": "Authorized to work in the US",
        "phone": "555-123-4567", "email": "diwakar@example.com",
        "linkedin": "linkedin.com/in/diwakar",
    }
    put_resp = client.put("/api/profile", json=payload)
    assert put_resp.status_code == 200
    assert put_resp.json()["name"] == "Diwakar Jilakara"

    get_resp = client.get("/api/profile")
    assert get_resp.status_code == 200
    assert get_resp.json()["email"] == "diwakar@example.com"


def test_profile_never_accepts_a_location_field(client):
    payload = {
        "name": "Diwakar", "experience": "8 years", "work_authorization": "US Citizen",
        "phone": "555", "email": "d@example.com", "linkedin": "li.com/d",
        "location": "San Francisco, CA",
    }
    resp = client.put("/api/profile", json=payload)
    assert resp.status_code == 200
    assert "location" not in resp.json()


def test_resume_upload_rejects_non_pdf(client):
    resp = client.post(
        "/api/resumes",
        files={"file": ("resume.txt", io.BytesIO(b"not a pdf"), "text/plain")},
    )
    assert resp.status_code == 400


def test_resume_upload_list_and_delete_roundtrip(client, tmp_path, monkeypatch):
    # minimal valid PDF with extractable text "Databricks Python SQL"
    minimal_pdf = (
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
    resp = client.post(
        "/api/resumes",
        files={"file": ("databricks_resume.pdf", io.BytesIO(minimal_pdf), "application/pdf")},
    )
    assert resp.status_code == 200
    resume = resp.json()
    assert resume["filename"] == "databricks_resume.pdf"
    resume_id = resume["id"]

    list_resp = client.get("/api/resumes")
    assert list_resp.status_code == 200
    assert any(r["id"] == resume_id for r in list_resp.json())

    delete_resp = client.delete(f"/api/resumes/{resume_id}")
    assert delete_resp.status_code == 200
    assert client.get("/api/resumes").json() == []


def test_resume_delete_nonexistent_returns_404(client):
    resp = client.delete("/api/resumes/99999")
    assert resp.status_code == 404


def test_dashboard_stats_empty_state(client):
    resp = client.get("/api/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["emails_processed"] == 0
    assert data["drafts_created"] == 0


def test_messages_list_empty_state(client):
    resp = client.get("/api/messages")
    assert resp.status_code == 200
    assert resp.json() == []


def test_applications_list_empty_state(client):
    resp = client.get("/api/applications")
    assert resp.status_code == 200
    assert resp.json() == []


def test_application_not_found_returns_404(client):
    resp = client.get("/api/applications/99999")
    assert resp.status_code == 404


def test_settings_endpoint_never_exposes_secret_values(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value")
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.text
    assert "sk-super-secret-value" not in body
