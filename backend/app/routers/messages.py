from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.ai.factory import get_ai_provider
from app.config import Settings, get_settings
from app.database import get_db
from app.models import Application, Draft, GmailAccount, ProcessedMessage
from app.schemas import ApplicationOut, ManualDraftIn, ManualDraftOut, ProcessedMessageDetailOut, ProcessedMessageOut
from app.services.gmail_service import get_gmail_client
from app.services.manual_draft import build_synthetic_raw_message
from app.services.pipeline import process_message
from app.utils.email_utils import EMAIL_RE

router = APIRouter(prefix="/api/messages", tags=["messages"])


@router.get("", response_model=list[ProcessedMessageOut])
def list_messages(
    search: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    limit: int = 200,
):
    """Section: Processed Emails page search/filter. `search` matches
    subject, sender, or recipient (case-insensitive, substring); `status`
    filters to an exact ProcessingStatus value (e.g. DRAFT_CREATED)."""
    query = db.query(ProcessedMessage)
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                ProcessedMessage.subject.ilike(like),
                ProcessedMessage.from_email.ilike(like),
                ProcessedMessage.to_email.ilike(like),
            )
        )
    if status:
        query = query.filter(ProcessedMessage.status == status)
    return query.order_by(ProcessedMessage.processed_at.desc()).limit(limit).all()


@router.post("/manual-draft", response_model=ManualDraftOut)
def create_manual_draft(
    payload: ManualDraftIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Settings/Applications UI "paste an email" box: takes pasted JD text
    plus an explicit send-to (and optional CC) address, builds a synthetic
    Gmail-message-shaped payload, and runs it through the EXACT same
    pipeline.process_message used for real incoming mail - identical JD
    extraction, interview screening, resume matching/customization, and
    (RULE J) draft-only creation. The recruiter/CC addresses are NOT
    auto-detected here - the user types them, and they override whatever
    the pipeline would otherwise infer from the pasted text. Requires Gmail
    to be connected, since the whole point is a real Gmail draft, not a dry
    run."""
    if not payload.sender_email.strip() or not EMAIL_RE.fullmatch(payload.sender_email.strip()):
        raise HTTPException(400, "A valid sender email is required")
    if not payload.receiver_email.strip() or not EMAIL_RE.fullmatch(payload.receiver_email.strip()):
        raise HTTPException(400, "A valid 'send to' email is required")
    cc_email = payload.cc_email.strip()
    if cc_email and not EMAIL_RE.fullmatch(cc_email):
        raise HTTPException(400, "The CC email address is not valid")
    if not payload.body.strip():
        raise HTTPException(400, "Email body text is required")

    account = db.query(GmailAccount).first()
    if account is None:
        raise HTTPException(400, "Gmail is not connected - connect it first to create a real draft")

    gmail_client = get_gmail_client(settings, account.token_json)
    ai_provider = get_ai_provider(settings)

    raw_message = build_synthetic_raw_message(
        sender_email=payload.sender_email.strip(),
        receiver_email=payload.receiver_email.strip(),
        subject=payload.subject.strip(),
        body=payload.body,
    )
    result = process_message(
        db, settings, raw_message, gmail_client, ai_provider,
        recruiter_email_override=payload.receiver_email.strip(),
        cc_email_override=cc_email or None,
    )

    draft = None
    if result.application is not None:
        draft = db.query(Draft).filter(Draft.application_id == result.application.id).first()

    return ManualDraftOut(
        status=result.status.value,
        reason=result.reason,
        application_id=result.application.id if result.application else None,
        draft_id=draft.id if draft else None,
        generated_subject=result.application.generated_subject if result.application else None,
        generated_body=result.application.generated_body if result.application else None,
        recruiter_email=result.application.recruiter_email if result.application else None,
        cc_email=result.application.cc_email if result.application else None,
    )


@router.get("/{message_id}", response_model=ProcessedMessageDetailOut)
def get_message(message_id: int, db: Session = Depends(get_db)):
    """Detail view backing "click a row" on the Processed Emails page - the
    linked application (if any) carries the resume/draft info, including
    whether an AI-customized resume was attached."""
    message = db.query(ProcessedMessage).filter(ProcessedMessage.id == message_id).first()
    if message is None:
        raise HTTPException(404, "Message not found")
    application = db.query(Application).filter(Application.source_message_id == message.id).first()
    return ProcessedMessageDetailOut(
        **ProcessedMessageOut.model_validate(message).model_dump(),
        application=ApplicationOut.model_validate(application) if application else None,
    )
