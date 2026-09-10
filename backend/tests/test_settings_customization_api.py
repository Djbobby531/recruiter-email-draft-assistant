"""
Settings customization: the user-editable overrides layered on top of
Settings/.env defaults (poll cadence, sender filtering, email template
snippets) - GET/PUT/reset via /api/settings/customization, and the
validate-with-fallback contract: an unset or invalid override must never
break anything, it just falls back to the default and is reported back in
`invalid_fields`.
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
from app.models import AppSettingsOverride
from app.services import runtime_settings
from app.services.pipeline import process_message
from tests.conftest import make_raw_message, make_resume
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


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
        yield c

    app.dependency_overrides.clear()


def test_get_customization_defaults_to_all_unset(client):
    resp = client.get("/api/settings/customization")
    assert resp.status_code == 200
    body = resp.json()
    assert body["poll_interval_seconds"] is None
    assert body["email_hope_line"] is None
    assert body["invalid_fields"] == []
    # effective values fall back to the built-in defaults
    assert body["effective_poll_interval_seconds"] == body["default_poll_interval_seconds"]
    assert body["effective_email_hope_line"] == body["default_email_hope_line"]


def test_put_customization_updates_only_provided_fields(client):
    resp = client.put("/api/settings/customization", json={"poll_interval_seconds": 45})
    assert resp.status_code == 200
    body = resp.json()
    assert body["poll_interval_seconds"] == 45
    assert body["effective_poll_interval_seconds"] == 45
    assert body["sent_sync_every_n_cycles"] is None  # untouched

    # a second, unrelated update must not clobber the first
    resp2 = client.put("/api/settings/customization", json={"allowed_sender_domains": "pamten.com"})
    body2 = resp2.json()
    assert body2["poll_interval_seconds"] == 45
    assert body2["allowed_sender_domains"] == "pamten.com"


def test_put_customization_invalid_value_is_saved_but_falls_back_and_is_reported(client):
    """Out-of-range values are still persisted (so the UI can show what was
    typed), but the effective value falls back to default and the field name
    shows up in invalid_fields - never a 400, never a broken app."""
    resp = client.put("/api/settings/customization", json={"poll_interval_seconds": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["poll_interval_seconds"] == 2
    assert body["effective_poll_interval_seconds"] == body["default_poll_interval_seconds"]
    assert "poll_interval_seconds" in body["invalid_fields"]


def test_put_customization_invalid_domain_list_falls_back(client):
    resp = client.put("/api/settings/customization", json={"allowed_sender_domains": "not a domain!!"})
    body = resp.json()
    assert body["effective_allowed_sender_domains"] == body["default_allowed_sender_domains"]
    assert "allowed_sender_domains" in body["invalid_fields"]


def test_put_customization_email_snippet_with_forbidden_phrase_falls_back(client):
    resp = client.put(
        "/api/settings/customization",
        json={"email_closing_line": "Thank you for reaching out, looking forward to it."},
    )
    body = resp.json()
    assert body["effective_email_closing_line"] == body["default_email_closing_line"]
    assert "email_closing_line" in body["invalid_fields"]


def test_put_customization_blank_hope_line_is_valid_and_omits_the_line(client):
    """The hope line is explicitly allowed to be blank (to omit it) - that's
    a valid customization, not a fallback."""
    resp = client.put("/api/settings/customization", json={"email_hope_line": ""})
    body = resp.json()
    assert body["effective_email_hope_line"] == ""
    assert "email_hope_line" not in body["invalid_fields"]


def test_put_customization_null_clears_an_override_back_to_default(client):
    client.put("/api/settings/customization", json={"poll_interval_seconds": 45})
    resp = client.put("/api/settings/customization", json={"poll_interval_seconds": None})
    body = resp.json()
    assert body["poll_interval_seconds"] is None
    assert body["effective_poll_interval_seconds"] == body["default_poll_interval_seconds"]


def test_reset_customization_clears_every_field(client):
    client.put("/api/settings/customization", json={
        "poll_interval_seconds": 45, "allowed_sender_domains": "pamten.com", "email_hope_line": "",
    })
    resp = client.post("/api/settings/customization/reset")
    assert resp.status_code == 200
    body = resp.json()
    assert body["poll_interval_seconds"] is None
    assert body["allowed_sender_domains"] is None
    assert body["email_hope_line"] is None
    assert body["invalid_fields"] == []


# --- End-to-end: a saved customization actually changes what gets drafted ---


def test_generated_draft_uses_a_saved_valid_customization(
    db_session, settings, candidate_profile, fake_gmail_client
):
    db_session.add(AppSettingsOverride(
        id=1, email_hope_line="", email_closing_line="Happy to connect whenever works for you.",
    ))
    db_session.commit()
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"data_engineering_tools": ["databricks", "airflow"], "languages": ["python", "sql"]},
        years=8, titles=["Senior Data Engineer"],
    )
    raw = make_raw_message(
        message_id="custom-1", thread_id="custom-thread-1",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status.value == "DRAFT_CREATED"

    body = result.application.generated_body
    assert "Happy to connect whenever works for you." in body
    assert "Hope you're doing well." not in body  # overridden to blank


def test_generated_draft_falls_back_to_default_when_stored_override_is_invalid(
    db_session, settings, candidate_profile, fake_gmail_client
):
    """A since-invalidated (or manually corrupted) override in the DB must
    never break draft generation - it silently falls back to the default
    snippet, exactly like an unset one."""
    db_session.add(AppSettingsOverride(
        id=1, email_closing_line="Thank you for reaching out, appreciate it!",
    ))
    db_session.commit()
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"data_engineering_tools": ["databricks", "airflow"], "languages": ["python", "sql"]},
        years=8, titles=["Senior Data Engineer"],
    )
    raw = make_raw_message(
        message_id="custom-2", thread_id="custom-thread-2",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)
    assert result.status.value == "DRAFT_CREATED"

    body = result.application.generated_body
    assert runtime_settings.DEFAULT_CLOSING_LINE in body
    assert "Thank you for reaching out" not in body
