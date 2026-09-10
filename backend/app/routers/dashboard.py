from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Application, AppStatus, Draft, ProcessedMessage, ProcessingStatus, Resume
from app.schemas import ChartPoint, DashboardCharts, DashboardStats

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
def stats(db: Session = Depends(get_db)):
    today_start = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    today_messages = db.query(ProcessedMessage).filter(ProcessedMessage.processed_at >= today_start)

    def count_status(status: ProcessingStatus) -> int:
        return today_messages.filter(ProcessedMessage.status == status).count()

    emails_processed = today_messages.count()
    replies_skipped = count_status(ProcessingStatus.SKIPPED_REPLY)
    duplicates_skipped = count_status(ProcessingStatus.SKIPPED_DUPLICATE)
    manual_review = count_status(ProcessingStatus.MANUAL_REVIEW)
    errors = count_status(ProcessingStatus.ERROR)
    drafts_created = count_status(ProcessingStatus.DRAFT_CREATED)
    not_job = count_status(ProcessingStatus.SKIPPED_NOT_JOB)
    in_person_interview_skipped = count_status(ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW)
    # SKIPPED_OWN_SENT_EMAIL is filtered out before job classification even
    # runs (same as replies/duplicates) - it was never a candidate
    # opportunity in the first place, so it's excluded the same way.
    own_sent_emails = count_status(ProcessingStatus.SKIPPED_OWN_SENT_EMAIL)
    job_opportunities = emails_processed - not_job - replies_skipped - duplicates_skipped - own_sent_emails

    latest_draft = db.query(Draft).order_by(Draft.created_at.desc()).first()
    latest_resume_selected = latest_draft.attached_resume_filename if latest_draft else None

    # --- Application-tracker lifecycle KPIs (dashboard phase, sections 6/21) ---
    # Computed across ALL applications (not just today's) - a SENT/SUBMITTED/
    # INTERVIEW status is usually reached well after the original email date.
    def count_app_status(status: str) -> int:
        return db.query(Application).filter(Application.status == status).count()

    sent_count = count_app_status(AppStatus.SENT)
    submitted_count = count_app_status(AppStatus.SUBMITTED)
    interview_count = count_app_status(AppStatus.INTERVIEW)
    rejected_count = count_app_status(AppStatus.REJECTED)
    withdrawn_count = count_app_status(AppStatus.WITHDRAWN)
    on_hold_count = count_app_status(AppStatus.ON_HOLD)

    now = dt.datetime.now(dt.UTC)
    week_start = now - dt.timedelta(days=7)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    applications_this_week = db.query(Application).filter(Application.created_at >= week_start).count()
    applications_this_month = db.query(Application).filter(Application.created_at >= month_start).count()
    interviews_this_month = (
        db.query(Application)
        .filter(Application.status == AppStatus.INTERVIEW, Application.interview_at >= month_start)
        .count()
    )
    recruiters_contacted = (
        db.query(Application.recruiter_id).filter(Application.recruiter_id.isnot(None)).distinct().count()
    )

    # Section 21: never show a ratio when the denominator is meaningless.
    submitted_or_later = submitted_count + interview_count + rejected_count + withdrawn_count
    interview_rate = round(100.0 * interview_count / submitted_or_later, 1) if submitted_or_later > 0 else None

    return DashboardStats(
        emails_processed=emails_processed,
        job_opportunities=max(0, job_opportunities),
        replies_skipped=replies_skipped,
        duplicates_skipped=duplicates_skipped,
        drafts_created=drafts_created,
        in_person_interview_skipped=in_person_interview_skipped,
        manual_review=manual_review,
        errors=errors,
        latest_resume_selected=latest_resume_selected,
        sent_count=sent_count,
        submitted_count=submitted_count,
        interview_count=interview_count,
        rejected_count=rejected_count,
        withdrawn_count=withdrawn_count,
        on_hold_count=on_hold_count,
        applications_this_week=applications_this_week,
        applications_this_month=applications_this_month,
        interviews_this_month=interviews_this_month,
        recruiters_contacted=recruiters_contacted,
        interview_rate=interview_rate,
    )


@router.get("/charts", response_model=DashboardCharts)
def charts(db: Session = Depends(get_db)):
    """Section 22: a small number of useful, real-data charts. Rendered as
    simple bar lists in the UI rather than pulling in a charting library."""
    by_status = (
        db.query(Application.status, func.count(Application.id))
        .group_by(Application.status)
        .order_by(func.count(Application.id).desc())
        .all()
    )
    by_location = (
        db.query(Application.job_location, func.count(Application.id))
        .filter(Application.job_location.isnot(None))
        .group_by(Application.job_location)
        .order_by(func.count(Application.id).desc())
        .limit(5)
        .all()
    )
    by_resume = (
        db.query(Resume.filename, func.count(Application.id))
        .join(Application, Application.selected_resume_id == Resume.id)
        .group_by(Resume.filename)
        .order_by(func.count(Application.id).desc())
        .limit(5)
        .all()
    )

    return DashboardCharts(
        applications_by_status=[ChartPoint(label=label, value=count) for label, count in by_status],
        applications_by_location=[ChartPoint(label=label, value=count) for label, count in by_location],
        applications_by_resume=[ChartPoint(label=label, value=count) for label, count in by_resume],
    )
