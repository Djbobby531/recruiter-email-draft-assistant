"""
Gmail API integration: OAuth 2.0 flow, incremental history polling, message
retrieval, and draft creation with attachment. Minimum practical scopes:
gmail.readonly (read messages/threads) + gmail.compose (create drafts only -
NOT gmail.send, so this app is structurally incapable of sending mail).
"""
from __future__ import annotations

import base64
import json
import mimetypes
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import Settings


class GmailNotConnectedError(Exception):
    pass


def build_oauth_flow(settings: Settings) -> Flow:
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=settings.gmail_scopes_list)
    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI
    return flow


def credentials_from_token_json(token_json: str) -> Credentials:
    data = json.loads(token_json)
    return Credentials.from_authorized_user_info(data)


class GmailClient:
    """Thin wrapper around the Gmail API for exactly the operations this app needs."""

    def __init__(self, credentials: Credentials):
        self.credentials = credentials
        self.service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    # ---- read ----

    def get_profile(self) -> dict:
        return self.service.users().getProfile(userId="me").execute()

    def get_message(self, message_id: str) -> dict:
        return self.service.users().messages().get(userId="me", id=message_id, format="full").execute()

    def get_message_from_header(self, message_id: str) -> str:
        """Lightweight metadata-only fetch (no body/attachments) - lets the
        caller check who a message is from before deciding whether it's
        worth the full fetch + pipeline processing at all."""
        message = (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata", metadataHeaders=["From"])
            .execute()
        )
        for header in message.get("payload", {}).get("headers", []):
            if header.get("name", "").lower() == "from":
                return header.get("value", "")
        return ""

    def get_thread(self, thread_id: str) -> dict:
        """Used by sent-message detection (section 17): metadata is enough to
        check labels/To/Subject without downloading full message bodies."""
        return (
            self.service.users()
            .threads()
            .get(userId="me", id=thread_id, format="metadata", metadataHeaders=["To", "Subject"])
            .execute()
        )

    def list_history(self, start_history_id: str) -> tuple[list[str], str | None]:
        """Returns (new_message_ids, latest_history_id). Uses Gmail history API for
        incremental sync so we never re-download the whole mailbox."""
        message_ids: list[str] = []
        page_token = None
        latest_history_id = start_history_id
        while True:
            try:
                resp = (
                    self.service.users()
                    .history()
                    .list(
                        userId="me",
                        startHistoryId=start_history_id,
                        historyTypes=["messageAdded"],
                        pageToken=page_token,
                    )
                    .execute()
                )
            except HttpError as e:
                if e.resp.status == 404:
                    # startHistoryId too old / expired -> caller should do a fresh sync
                    raise
                raise

            for record in resp.get("history", []):
                for added in record.get("messagesAdded", []):
                    msg_id = added.get("message", {}).get("id")
                    if msg_id:
                        message_ids.append(msg_id)

            latest_history_id = resp.get("historyId", latest_history_id)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return message_ids, latest_history_id

    def list_recent_message_ids(self, max_results: int = 25, query: str = "in:inbox") -> tuple[list[str], str | None]:
        """Initial sync fallback: grab the most recent N inbox messages and the
        mailbox's current historyId to start incremental polling from."""
        resp = self.service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        ids = [m["id"] for m in resp.get("messages", [])]
        profile = self.get_profile()
        return ids, profile.get("historyId")

    def list_message_ids_since(self, after_epoch_seconds: int, query: str = "in:inbox") -> list[str]:
        """Every message id received after the given watermark, fully
        paginated (no cap). Used to backfill a gap when the stored
        historyId has expired (Gmail only retains history for ~a week) -
        so a period the poller was offline for never silently drops
        messages, instead of jumping straight to "now"."""
        ids: list[str] = []
        page_token = None
        while True:
            resp = (
                self.service.users()
                .messages()
                .list(userId="me", q=f"{query} after:{after_epoch_seconds}", maxResults=100, pageToken=page_token)
                .execute()
            )
            ids.extend(m["id"] for m in resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids

    # ---- draft creation ----

    def create_draft_with_attachment(
        self,
        to_email: str,
        cc_email: str | None,
        subject: str,
        body_text: str,
        attachment_path: str,
        thread_id: str | None = None,
    ) -> dict:
        message = MIMEMultipart()
        message["to"] = to_email
        if cc_email:
            message["cc"] = cc_email
        message["subject"] = subject
        message.attach(MIMEText(body_text, "plain"))

        path = Path(attachment_path)
        mime_type, _ = mimetypes.guess_type(path.name)
        mime_type = mime_type or "application/octet-stream"
        maintype, subtype = mime_type.split("/", 1)
        with open(path, "rb") as f:
            attachment = MIMEApplication(f.read(), _subtype=subtype)
        attachment.add_header("Content-Disposition", "attachment", filename=path.name)
        message.attach(attachment)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        body: dict = {"message": {"raw": raw}}
        if thread_id:
            body["message"]["threadId"] = thread_id

        return self.service.users().drafts().create(userId="me", body=body).execute()


def get_gmail_client(settings: Settings, token_json: str) -> GmailClient:
    creds = credentials_from_token_json(token_json)
    return GmailClient(creds)
