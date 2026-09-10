from __future__ import annotations

import csv
import datetime as dt
import io
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import (
    Application,
    AppStatus,
    CandidateProfile,
    Draft,
    EventType,
    GmailAccount,
    ProcessedMessage,
    ProcessingStatus,
    Resume,
)
from app.schemas import (
    ApplicationDetailOut,
    ApplicationOut,
    ApplicationStatusUpdate,
    DraftOut,
    ManualReviewResolve,
)
from app.services import draft_service, runtime_settings
from app.services.application_events import add_event
from app.services.application_status import InvalidTransitionError, transition
from app.services.gmail_service import get_gmail_client
from app.services.resume_matcher import match_resumes
from app.services.sent_detection import run_sent_sync_cycle
from app.utils.email_utils import first_name

logger = logging.getLogger("app.routers.applications")
router = APIRouter(prefix="/api/applications", tags=["applications"])

_SORTABLE_FIELDS = {
    "created_at": Application.created_at,
    "updated_at": Application.updated_at,
    "job_title": Application.job_title,
    "job_location": Application.job_location,
    "status": Application.status,
    "recruiter_name": Application.recruiter_name,
    "implementation_partner": Application.implementation_partner,
    "end_client": Application.end_client,
}

_DATE_RANGE_PRESETS = {"today", "yesterday", "last_7_days", "last_30_days", "this_month"}

_EXPORT_FIELDS = [
    "created_at", "status", "recruiter_name", "recruiter_email", "recruiter_phone",
    "job_title", "job_location", "local_requirement", "implementation_partner",
    "end_client", "resume", "sent_at", "submitted_at", "interview_status",
]


def _apply_filters(
    query,
    db: Session,
    search: str | None,
    status: str | None,
    recruiter_id: int | None,
    location: str | None,
    local_requirement: str | None,
    implementation_partner: str | None,
    end_client: str | None,
    resume_id: int | None,
    date_from: str | None,
    date_to: str | None,
    date_range: str | None,
):
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Application.recruiter_name.ilike(like),
                Application.recruiter_email.ilike(like),
                Application.job_title.ilike(like),
                Application.job_location.ilike(like),
                Application.implementation_partner.ilike(like),
                Application.end_client.ilike(like),
                Application.status.ilike(like),
            )
        )
    if status:
        query = query.filter(Application.status == status)
    if recruiter_id:
        query = query.filter(Application.recruiter_id == recruiter_id)
    if location:
        query = query.filter(Application.job_location.ilike(f"%{location}%"))
    if local_requirement:
        query = query.filter(Application.local_requirement == local_requirement)
    if implementation_partner:
        query = query.filter(Application.implementation_partner.ilike(f"%{implementation_partner}%"))
    if end_client:
        query = query.filter(Application.end_client.ilike(f"%{end_client}%"))
    if resume_id:
        query = query.filter(Application.selected_resume_id == resume_id)

    now = dt.datetime.now(dt.UTC)
    if date_range == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Application.created_at >= start)
    elif date_range == "yesterday":
        start = (now - dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Application.created_at >= start, Application.created_at < end)
    elif date_range == "last_7_days":
        query = query.filter(Application.created_at >= now - dt.timedelta(days=7))
    elif date_range == "last_30_days":
        query = query.filter(Application.created_at >= now - dt.timedelta(days=30))
    elif date_range == "this_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Application.created_at >= start)

    if date_from:
        query = query.filter(Application.created_at >= dt.datetime.fromisoformat(date_from))
    if date_to:
        query = query.filter(Application.created_at <= dt.datetime.fromisoformat(date_to))

    return query


@router.get("", response_model=list[ApplicationOut])
def list_applications(
    search: str | None = None,
    status: str | None = None,
    recruiter_id: int | None = None,
    location: str | None = None,
    local_requirement: str | None = None,
    implementation_partner: str | None = None,
    end_client: str | None = None,
    resume_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_range: str | None = None,
    sort_by: str = "created_at",
    order: str = "desc",
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """Sections 5-9: the application tracker table, with global search, per-
    column filters (combinable), and sortable columns. Newest activity first
    by default."""
    query = _apply_filters(
        db.query(Application), db, search, status, recruiter_id, location, local_requirement,
        implementation_partner, end_client, resume_id, date_from, date_to, date_range,
    )
    sort_column = _SORTABLE_FIELDS.get(sort_by, Application.created_at)
    query = query.order_by(sort_column.desc() if order == "desc" else sort_column.asc())
    return query.limit(limit).all()


@router.get("/export.csv")
def export_applications_csv(
    search: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """Section 33 CSV export. Never includes OAuth tokens or raw email
    bodies - only the tracker fields a person would actually want in a
    spreadsheet."""
    query = _apply_filters(
        db.query(Application), db, search, status, None, None, None, None, None, None, None, None, None,
    )
    applications = query.order_by(Application.created_at.desc()).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Date", "Status", "Recruiter", "Recruiter Email", "Recruiter Phone", "Job Title",
        "Location", "Local Needed", "Implementation Partner", "End Client", "Resume",
        "Sent Date", "Submitted Date", "Interview Status",
    ])
    for a in applications:
        writer.writerow([
            a.created_at.isoformat() if a.created_at else "",
            a.status,
            a.recruiter_name or "",
            a.recruiter_email or "",
            a.recruiter.phone if a.recruiter else "",
            a.job_title or "",
            a.job_location or "",
            a.local_requirement,
            a.implementation_partner or "",
            a.end_client or "",
            a.selected_resume.filename if a.selected_resume else "",
            a.sent_at.isoformat() if a.sent_at else "",
            a.submitted_at.isoformat() if a.submitted_at else "",
            a.interview_status,
        ])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=applications.csv"},
    )


@router.post("/sync-sent")
def sync_sent(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Manually trigger a Gmail sent-message sync cycle (section 17) instead
    of waiting for the next poll interval. Never scans the whole mailbox -
    only threads belonging to applications still awaiting send confirmation."""
    account = db.query(GmailAccount).first()
    if account is None:
        return {"updated": 0, "reason": "Gmail not connected"}
    client = get_gmail_client(settings, account.token_json)
    updated = run_sent_sync_cycle(db, client)
    return {"updated": updated}


@router.get("/manual-review", response_model=list[ApplicationOut])
def list_manual_review(db: Session = Depends(get_db)):
    msg_ids_in_review = [
        m.id for m in db.query(ProcessedMessage).filter(ProcessedMessage.status == ProcessingStatus.MANUAL_REVIEW)
    ]
    return (
        db.query(Application)
        .filter(Application.source_message_id.in_(msg_ids_in_review))
        .order_by(Application.created_at.desc())
        .all()
    )


@router.get("/{application_id}", response_model=ApplicationDetailOut)
def get_application(application_id: int, db: Session = Depends(get_db)):
    app_ = db.query(Application).filter(Application.id == application_id).first()
    if app_ is None:
        raise HTTPException(404, "Application not found")
    return app_


@router.get("/{application_id}/draft", response_model=DraftOut | None)
def get_draft(application_id: int, db: Session = Depends(get_db)):
    return db.query(Draft).filter(Draft.application_id == application_id).first()


@router.get("/{application_id}/resume-file")
def get_application_resume_file(application_id: int, db: Session = Depends(get_db)):
    """Serves whichever resume was actually attached to this application's
    draft - the AI-customized copy if one was produced, otherwise the
    original library resume. This is what "click the row to see the
    customized resume" resolves to in the UI."""
    application = db.query(Application).filter(Application.id == application_id).first()
    if application is None:
        raise HTTPException(404, "Application not found")

    if application.customized_resume_path:
        return FileResponse(
            application.customized_resume_path,
            filename=f"Customized_{application.selected_resume.filename}" if application.selected_resume else "resume.docx",
        )
    if application.selected_resume is not None:
        return FileResponse(application.selected_resume.file_path, filename=application.selected_resume.filename)
    raise HTTPException(404, "No resume attached to this application")


@router.post("/{application_id}/status", response_model=ApplicationOut)
def update_status(application_id: int, payload: ApplicationStatusUpdate, db: Session = Depends(get_db)):
    """Section 18/30: user-driven status transitions (SENT -> SUBMITTED,
    SUBMITTED -> INTERVIEW/REJECTED, etc). Every transition is validated
    against the allowed lifecycle graph unless `override=true` is passed, and
    every transition - including overrides - is logged as an application
    event for the audit trail."""
    application = db.query(Application).filter(Application.id == application_id).first()
    if application is None:
        raise HTTPException(404, "Application not found")
    try:
        return transition(db, application, payload.status, override=payload.override, notes=payload.notes)
    except InvalidTransitionError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{application_id}/resolve-review", response_model=DraftOut)
def resolve_manual_review(
    application_id: int,
    payload: ManualReviewResolve,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Manual review resolution (section 19): user picks the recruiter email
    themselves, and we run the remaining deterministic steps to create the draft."""
    application = db.query(Application).filter(Application.id == application_id).first()
    if application is None:
        raise HTTPException(404, "Application not found")

    existing_draft = db.query(Draft).filter(Draft.application_id == application_id).first()
    if existing_draft is not None:
        raise HTTPException(400, "A draft already exists for this application")

    profile = db.query(CandidateProfile).first()
    if profile is None:
        raise HTTPException(400, "Candidate profile not configured")

    resumes = db.query(Resume).order_by(Resume.id.asc()).all()
    match_result = match_resumes(
        resumes, jd_title=application.job_title, jd_text=application.job_title or "",
        jd_requirements=application.requirements or [],
        min_score=settings.RESUME_MATCH_CONFIDENCE_THRESHOLD,
    )
    if match_result.best is None:
        raise HTTPException(400, "No indexed resumes available to attach")
    best_resume = db.query(Resume).filter(Resume.id == match_result.best.resume_id).first()
    if best_resume is None:
        raise HTTPException(400, "Selected resume was removed before the draft could be created")

    recruiter_first_name = first_name(payload.recruiter_name)
    top_skills = match_result.best.matched_terms or application.requirements or []
    effective_settings = runtime_settings.get_effective_settings(db, settings)
    generated = draft_service.generate_email(
        job_title=application.job_title,
        job_location=application.job_location,
        recruiter_first_name=recruiter_first_name,
        top_skills=top_skills,
        candidate_name=profile.name,
        candidate_experience=profile.experience,
        candidate_work_auth=profile.work_authorization,
        candidate_phone=profile.phone,
        candidate_email=profile.email,
        candidate_linkedin=profile.linkedin,
        hope_line=effective_settings.email_hope_line,
        capability_sentence=effective_settings.email_capability_sentence,
        closing_line=effective_settings.email_closing_line,
    )

    application.recruiter_email = payload.recruiter_email
    application.recruiter_name = payload.recruiter_name
    application.selected_resume_id = best_resume.id
    application.match_score = match_result.best.score
    application.match_explanation = match_result.best.explanation
    application.generated_subject = generated.subject
    application.generated_body = generated.body
    application.review_reason = None
    db.add(application)

    gmail_draft_id = None
    account = db.query(GmailAccount).first()
    if account is not None:
        client = get_gmail_client(settings, account.token_json)
        # Brand new standalone email - never threaded under the original
        # message, so it never appears quoted/rewritten as a "reply".
        api_response = client.create_draft_with_attachment(
            to_email=payload.recruiter_email,
            cc_email=application.cc_email,
            subject=generated.subject,
            body_text=generated.body,
            attachment_path=best_resume.file_path,
        )
        gmail_draft_id = api_response.get("id")
        new_thread_id = api_response.get("message", {}).get("threadId")
        if new_thread_id:
            application.thread_id = new_thread_id

    draft = Draft(
        gmail_draft_id=gmail_draft_id,
        application_id=application.id,
        to_email=payload.recruiter_email,
        cc_email=application.cc_email,
        subject=generated.subject,
        attached_resume_filename=best_resume.filename,
        status="CREATED",
    )
    db.add(draft)

    message = db.query(ProcessedMessage).filter(ProcessedMessage.id == application.source_message_id).first()
    if message:
        message.status = ProcessingStatus.DRAFT_CREATED
        message.error_message = None
        db.add(message)

    # A Gmail draft existing is NOT the same as the application being sent -
    # the tracker status only ever becomes DRAFT here (section 2/35).
    application.status = AppStatus.DRAFT
    db.add(application)
    db.commit()
    db.refresh(draft)
    add_event(db, application.id, EventType.RESUME_SELECTED, {"resume_id": best_resume.id, "filename": best_resume.filename})
    add_event(db, application.id, EventType.DRAFT_CREATED, {"gmail_draft_id": gmail_draft_id, "to_email": draft.to_email})
    return draft


@router.post("/{application_id}/skip")
def skip_application(application_id: int, db: Session = Depends(get_db)):
    application = db.query(Application).filter(Application.id == application_id).first()
    if application is None:
        raise HTTPException(404, "Application not found")
    application.status = AppStatus.SKIPPED
    application.skip_reason = "manually skipped by user"
    db.add(application)
    message = db.query(ProcessedMessage).filter(ProcessedMessage.id == application.source_message_id).first()
    if message:
        message.status = ProcessingStatus.SKIPPED_NOT_JOB
        message.error_message = "manually skipped by user"
        db.add(message)
    db.commit()
    add_event(db, application.id, EventType.SKIPPED, {"reason": "manually skipped by user"})
    return {"skipped": True}
