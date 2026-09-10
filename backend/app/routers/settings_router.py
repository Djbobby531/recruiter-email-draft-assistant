"""Read-only view of effective configuration for the Settings page. Secrets are
never returned to the frontend - only booleans indicating whether they're set.

Also exposes the user-editable customization overrides (poll cadence, sender
filtering, email template snippets) - see services/runtime_settings.py for
the validation-with-fallback contract every value here follows.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.schemas import AppSettingsCustomizationIn, AppSettingsCustomizationOut
from app.services import runtime_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_effective_settings(settings: Settings = Depends(get_settings)):
    return {
        "app_env": settings.APP_ENV,
        "email_mode": settings.EMAIL_MODE,
        "poll_interval_seconds": settings.POLL_INTERVAL_SECONDS,
        "ai_provider": settings.AI_PROVIDER,
        "openai_configured": bool(settings.OPENAI_API_KEY),
        "ollama_base_url": settings.OLLAMA_BASE_URL,
        "my_email": settings.MY_EMAIL,
        "job_classification_threshold": settings.JOB_CLASSIFICATION_THRESHOLD,
        "recruiter_email_confidence_threshold": settings.RECRUITER_EMAIL_CONFIDENCE_THRESHOLD,
    }


def _build_customization_out(db: Session, settings: Settings) -> AppSettingsCustomizationOut:
    row = runtime_settings.get_or_create(db)
    effective = runtime_settings.get_effective_settings(db, settings)
    return AppSettingsCustomizationOut(
        poll_interval_seconds=row.poll_interval_seconds,
        sent_sync_every_n_cycles=row.sent_sync_every_n_cycles,
        poll_backoff_max_seconds=row.poll_backoff_max_seconds,
        allowed_sender_domains=row.allowed_sender_domains,
        email_hope_line=row.email_hope_line,
        email_capability_sentence=row.email_capability_sentence,
        email_closing_line=row.email_closing_line,
        effective_poll_interval_seconds=effective.poll_interval_seconds,
        effective_sent_sync_every_n_cycles=effective.sent_sync_every_n_cycles,
        effective_poll_backoff_max_seconds=effective.poll_backoff_max_seconds,
        effective_allowed_sender_domains=effective.allowed_sender_domains,
        effective_email_hope_line=effective.email_hope_line,
        effective_email_capability_sentence=effective.email_capability_sentence,
        effective_email_closing_line=effective.email_closing_line,
        default_poll_interval_seconds=settings.POLL_INTERVAL_SECONDS,
        default_sent_sync_every_n_cycles=settings.SENT_SYNC_EVERY_N_CYCLES,
        default_poll_backoff_max_seconds=settings.POLL_BACKOFF_MAX_SECONDS,
        default_allowed_sender_domains=settings.ALLOWED_SENDER_DOMAINS,
        default_email_hope_line=runtime_settings.DEFAULT_HOPE_LINE,
        default_email_capability_sentence=runtime_settings.DEFAULT_CAPABILITY_SENTENCE,
        default_email_closing_line=runtime_settings.DEFAULT_CLOSING_LINE,
        invalid_fields=effective.invalid_fields,
    )


@router.get("/customization", response_model=AppSettingsCustomizationOut)
def get_customization(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    return _build_customization_out(db, settings)


@router.put("/customization", response_model=AppSettingsCustomizationOut)
def update_customization(
    payload: AppSettingsCustomizationIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Only fields explicitly present in the request body are touched - a
    field omitted entirely is left as-is; a field sent as null clears that
    override back to "use the default". Every value is re-validated on the
    next read regardless (see runtime_settings.get_effective_settings), so
    saving something invalid here doesn't break anything - it just won't
    take effect until corrected, and shows up in `invalid_fields`."""
    row = runtime_settings.get_or_create(db)
    for field_name in payload.model_fields_set:
        setattr(row, field_name, getattr(payload, field_name))
    db.add(row)
    db.commit()
    return _build_customization_out(db, settings)


@router.post("/customization/reset", response_model=AppSettingsCustomizationOut)
def reset_customization(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Clears every override back to "not customized" (default)."""
    row = runtime_settings.get_or_create(db)
    for field_name in (
        "poll_interval_seconds", "sent_sync_every_n_cycles", "poll_backoff_max_seconds",
        "allowed_sender_domains", "email_hope_line", "email_capability_sentence", "email_closing_line",
    ):
        setattr(row, field_name, None)
    db.add(row)
    db.commit()
    return _build_customization_out(db, settings)
