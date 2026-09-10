"""
Mocked Gmail API coverage for app/services/gmail_service.py (section 2 of the
hardening spec: "mocks for Gmail API"). No real Google credentials or network
calls - the underlying `service` object is replaced with a stub that mimics
the googleapiclient discovery-resource call shape (`.users().x().execute()`).
"""
from __future__ import annotations

import base64

from app.services.gmail_service import GmailClient


class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class StubMessagesResource:
    def __init__(self, get_result=None, list_result=None):
        self._get_result = get_result
        self._list_result = list_result
        self.get_calls = []
        self.list_calls = []

    def get(self, userId, id, format):
        self.get_calls.append((userId, id, format))
        return _Exec(self._get_result)

    def list(self, userId, q, maxResults):
        self.list_calls.append((userId, q, maxResults))
        return _Exec(self._list_result)


class StubHistoryResource:
    def __init__(self, pages):
        self._pages = pages  # list of response dicts to return in sequence
        self.calls = []

    def list(self, userId, startHistoryId, historyTypes, pageToken=None):
        self.calls.append((userId, startHistoryId, historyTypes, pageToken))
        idx = len(self.calls) - 1
        return _Exec(self._pages[idx])


class StubUsersResource:
    def __init__(self, profile=None, messages=None, history=None):
        self._profile = profile
        self._messages = messages
        self._history = history

    def getProfile(self, userId):
        return _Exec(self._profile)

    def messages(self):
        return self._messages

    def history(self):
        return self._history


class StubService:
    def __init__(self, users_resource):
        self._users_resource = users_resource

    def users(self):
        return self._users_resource


def _client_with_stub_service(users_resource) -> GmailClient:
    client = GmailClient.__new__(GmailClient)
    client.credentials = None
    client.service = StubService(users_resource)
    return client


def test_get_profile_returns_email_address():
    users = StubUsersResource(profile={"emailAddress": "diwakar@example.com", "historyId": "1000"})
    client = _client_with_stub_service(users)
    profile = client.get_profile()
    assert profile["emailAddress"] == "diwakar@example.com"


def test_get_message_calls_full_format():
    messages = StubMessagesResource(get_result={"id": "m1", "threadId": "t1"})
    users = StubUsersResource(messages=messages)
    client = _client_with_stub_service(users)
    msg = client.get_message("m1")
    assert msg["id"] == "m1"
    assert messages.get_calls == [("me", "m1", "full")]


def test_list_recent_message_ids_returns_ids_and_history_id():
    messages = StubMessagesResource(list_result={"messages": [{"id": "a"}, {"id": "b"}]})
    users = StubUsersResource(profile={"emailAddress": "d@example.com", "historyId": "500"}, messages=messages)
    client = _client_with_stub_service(users)
    ids, history_id = client.list_recent_message_ids(max_results=10)
    assert ids == ["a", "b"]
    assert history_id == "500"


def test_list_history_walks_pagination_and_collects_added_message_ids():
    pages = [
        {
            "history": [{"messagesAdded": [{"message": {"id": "m1"}}, {"message": {"id": "m2"}}]}],
            "historyId": "101",
            "nextPageToken": "page2",
        },
        {
            "history": [{"messagesAdded": [{"message": {"id": "m3"}}]}],
            "historyId": "102",
        },
    ]
    history = StubHistoryResource(pages)
    users = StubUsersResource(history=history)
    client = _client_with_stub_service(users)

    ids, latest_history_id = client.list_history("100")
    assert ids == ["m1", "m2", "m3"]
    assert latest_history_id == "102"
    assert len(history.calls) == 2


def test_list_history_with_no_new_messages_returns_empty():
    pages = [{"historyId": "200"}]  # no "history" key at all
    history = StubHistoryResource(pages)
    users = StubUsersResource(history=history)
    client = _client_with_stub_service(users)

    ids, latest_history_id = client.list_history("199")
    assert ids == []
    assert latest_history_id == "200"


def test_create_draft_with_attachment_builds_valid_mime_and_calls_drafts_create(tmp_path):
    resume_file = tmp_path / "resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 fake resume content")

    captured = {}

    class StubDraftsResource:
        def create(self, userId, body):
            captured["userId"] = userId
            captured["body"] = body
            return _Exec({"id": "draft-abc"})

    class UsersWithDrafts(StubUsersResource):
        def drafts(self):
            return StubDraftsResource()

    client = _client_with_stub_service(UsersWithDrafts())
    response = client.create_draft_with_attachment(
        to_email="naveen@pamten.com", cc_email="james@algebrait.com",
        subject="Application – Senior Data Engineer", body_text="Hi Naveen,\n...",
        attachment_path=str(resume_file), thread_id="thread-1",
    )

    assert response["id"] == "draft-abc"
    assert captured["userId"] == "me"
    raw = captured["body"]["message"]["raw"]
    decoded = base64.urlsafe_b64decode(raw.encode("utf-8")).decode("utf-8", errors="replace")
    assert "naveen@pamten.com" in decoded
    assert "james@algebrait.com" in decoded
    assert "Application" in decoded
    assert captured["body"]["message"]["threadId"] == "thread-1"


def test_credentials_from_token_json_parses_authorized_user_info(monkeypatch):
    from app.services import gmail_service

    captured = {}

    class FakeCredentials:
        @staticmethod
        def from_authorized_user_info(info):
            captured["info"] = info
            return "fake-creds-object"

    monkeypatch.setattr(gmail_service, "Credentials", FakeCredentials)
    token_json = '{"token": "abc", "refresh_token": "def", "client_id": "x", "client_secret": "y"}'
    creds = gmail_service.credentials_from_token_json(token_json)
    assert creds == "fake-creds-object"
    assert captured["info"]["token"] == "abc"


def test_build_oauth_flow_uses_configured_redirect_uri():
    from app.config import Settings
    from app.services.gmail_service import build_oauth_flow

    settings = Settings(
        GOOGLE_CLIENT_ID="test-client-id",
        GOOGLE_CLIENT_SECRET="test-secret",
        GOOGLE_REDIRECT_URI="http://localhost:8000/api/gmail/oauth/callback",
    )
    flow = build_oauth_flow(settings)
    assert flow.redirect_uri == "http://localhost:8000/api/gmail/oauth/callback"
    assert "gmail.readonly" in " ".join(flow.oauth2session.scope)
