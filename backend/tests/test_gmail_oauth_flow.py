"""Coverage for the Gmail OAuth start/callback endpoints, with build_oauth_flow
and get_gmail_client mocked so no real Google network call is made."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
import app.routers.gmail as gmail_router
from app.database import Base, get_db
from app.main import app
from app.models import GmailAccount


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

    async def _noop_polling_loop(stop_event):
        await stop_event.wait()

    monkeypatch.setattr(main_module, "polling_loop", _noop_polling_loop)

    with TestClient(app) as c:
        yield c, TestSessionLocal

    app.dependency_overrides.clear()


class FakeFlow:
    def __init__(self):
        self.redirect_uri = None
        self.fetch_token_called_with = None
        self.credentials = FakeCredentials()

    def authorization_url(self, **kwargs):
        return "https://accounts.google.com/o/oauth2/auth?fake=1", "state123"

    def fetch_token(self, code):
        self.fetch_token_called_with = code


class FakeCredentials:
    def to_json(self):
        return '{"token": "fake-access-token", "refresh_token": "fake-refresh-token"}'


class FakeGmailClient:
    def get_profile(self):
        return {"emailAddress": "diwakar@example.com"}


def test_oauth_start_with_credentials_returns_authorization_url(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(gmail_router, "build_oauth_flow", lambda settings: FakeFlow())

    from app.config import get_settings
    settings = get_settings()
    settings.GOOGLE_CLIENT_ID = "test-client-id"
    settings.GOOGLE_CLIENT_SECRET = "test-secret"
    try:
        resp = c.get("/api/gmail/oauth/start")
        assert resp.status_code == 200
        assert "authorization_url" in resp.json()
        assert resp.json()["authorization_url"].startswith("https://accounts.google.com")
    finally:
        settings.GOOGLE_CLIENT_ID = ""
        settings.GOOGLE_CLIENT_SECRET = ""


def test_oauth_callback_creates_gmail_account_and_redirects(client, monkeypatch):
    c, session_factory = client
    monkeypatch.setattr(gmail_router, "build_oauth_flow", lambda settings: FakeFlow())
    monkeypatch.setattr(gmail_router, "get_gmail_client", lambda settings, token_json: FakeGmailClient())

    resp = c.get("/api/gmail/oauth/callback?code=fake-auth-code", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "connected=1" in resp.headers["location"]

    db = session_factory()
    account = db.query(GmailAccount).first()
    assert account is not None
    assert account.email_address == "diwakar@example.com"
    db.close()


def test_oauth_callback_updates_existing_account_rather_than_duplicating(client, monkeypatch):
    c, session_factory = client
    monkeypatch.setattr(gmail_router, "build_oauth_flow", lambda settings: FakeFlow())
    monkeypatch.setattr(gmail_router, "get_gmail_client", lambda settings, token_json: FakeGmailClient())

    db = session_factory()
    db.add(GmailAccount(email_address="old@example.com", token_json="{}"))
    db.commit()
    db.close()

    c.get("/api/gmail/oauth/callback?code=fake-auth-code-2", follow_redirects=False)

    db2 = session_factory()
    accounts = db2.query(GmailAccount).all()
    assert len(accounts) == 1
    assert accounts[0].email_address == "diwakar@example.com"
    db2.close()
