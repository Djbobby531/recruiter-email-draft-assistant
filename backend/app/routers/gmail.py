from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import GmailAccount
from app.services.gmail_service import build_oauth_flow, get_gmail_client

logger = logging.getLogger("app.routers.gmail")
router = APIRouter(prefix="/api/gmail", tags=["gmail"])


@router.get("/status")
def gmail_status(db: Session = Depends(get_db)):
    account = db.query(GmailAccount).first()
    if account is None:
        return {"connected": False}
    return {
        "connected": True,
        "email_address": account.email_address,
        "last_history_id": account.last_history_id,
        "connected_at": account.connected_at,
    }


@router.get("/oauth/start")
def oauth_start(settings: Settings = Depends(get_settings)):
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            400,
            "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET not configured. See README OAuth setup section.",
        )
    flow = build_oauth_flow(settings)
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    return {"authorization_url": auth_url}


@router.get("/oauth/callback")
def oauth_callback(code: str, settings: Settings = Depends(get_settings), db: Session = Depends(get_db)):
    flow = build_oauth_flow(settings)
    flow.fetch_token(code=code)
    creds = flow.credentials

    client = get_gmail_client(settings, creds.to_json())
    profile = client.get_profile()
    email_address = profile.get("emailAddress", "")

    account = db.query(GmailAccount).first()
    if account is None:
        account = GmailAccount(email_address=email_address, token_json=creds.to_json())
    else:
        account.email_address = email_address
        account.token_json = creds.to_json()
    db.add(account)
    db.commit()

    logger.info("gmail account connected: %s", email_address)
    return RedirectResponse(url=f"{settings.FRONTEND_ORIGIN}/gmail?connected=1")


@router.post("/disconnect")
def disconnect(db: Session = Depends(get_db)):
    account = db.query(GmailAccount).first()
    if account:
        db.delete(account)
        db.commit()
    return {"connected": False}
