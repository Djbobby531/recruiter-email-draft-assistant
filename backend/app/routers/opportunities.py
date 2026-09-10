"""
Opportunity history + skipped-opportunities view (sections 8, 15, 18, 26-27).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
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
    Opportunity,
    ProcessingStatus,
    Resume,
)
from app.schemas import OpportunityOut
from app.services import draft_service, runtime_settings
from app.services.application_events import add_event
from app.services.gmail_service import get_gmail_client
from app.services.resume_matcher import match_resumes
from app.utils.email_utils import first_name

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@router.get("", response_model=list[OpportunityOut])
def list_opportunities(status: str | None = None, db: Session = Depends(get_db), limit: int = 200):
    query = db.query(Opportunity)
    if status:
        query = query.filter(Opportunity.status == status)
    return query.order_by(Opportunity.created_at.desc()).limit(limit).all()


@router.get("/skipped", response_model=list[OpportunityOut])
def list_skipped_opportunities(db: Session = Depends(get_db), limit: int = 200):
    """Section 26 - the dedicated Skipped Opportunities view."""
    return (
        db.query(Opportunity)
        .filter(Opportunity.status == ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW)
        .order_by(Opportunity.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/{opportunity_id}", response_model=OpportunityOut)
def get_opportunity(opportunity_id: int, db: Session = Depends(get_db)):
    opportunity = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if opportunity is None:
        raise HTTPException(404, "Opportunity not found")
    return opportunity


@router.delete("/{opportunity_id}")
def delete_opportunity(opportunity_id: int, db: Session = Depends(get_db)):
    """Section 18 data-privacy control: delete a single opportunity record."""
    opportunity = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if opportunity is None:
        raise HTTPException(404, "Opportunity not found")
    db.delete(opportunity)
    db.commit()
    return {"deleted": True}


@router.delete("")
def delete_all_opportunity_history(db: Session = Depends(get_db)):
    """Section 18: "Delete opportunity history" - wipes every Opportunity
    record. Recruiters, resumes, applications, and drafts are untouched."""
    count = db.query(Opportunity).delete()
    db.commit()
    return {"deleted": count}


@router.post("/{opportunity_id}/override", response_model=OpportunityOut)
def override_interview_skip(
    opportunity_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Section 27 manual override: the user explicitly decides an
    automatically-skipped in-person-interview opportunity should be processed
    after all. Resume matching, email generation, and Gmail draft creation
    all run for the first time here (they were never performed for a skipped
    opportunity). The original classification is preserved for audit.
    """
    opportunity = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if opportunity is None:
        raise HTTPException(404, "Opportunity not found")
    if opportunity.status != ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW:
        raise HTTPException(400, "Only an in-person-interview skip can be overridden")
    if opportunity.application_id is not None:
        raise HTTPException(400, "This opportunity has already been processed into an application")

    profile = db.query(CandidateProfile).first()
    if profile is None:
        raise HTTPException(400, "Candidate profile not configured")

    resumes = db.query(Resume).order_by(Resume.id.asc()).all()
    match_result = match_resumes(
        resumes, jd_title=opportunity.job_title, jd_text=opportunity.job_title or "",
        jd_requirements=opportunity.required_skills or [],
        min_score=settings.RESUME_MATCH_CONFIDENCE_THRESHOLD,
    )
    if match_result.best is None:
        raise HTTPException(400, "No indexed resumes available to attach")
    best_resume = db.query(Resume).filter(Resume.id == match_result.best.resume_id).first()
    if best_resume is None:
        raise HTTPException(400, "Selected resume was removed before the draft could be created")

    recruiter = opportunity.recruiter
    message = opportunity.source_message
    recruiter_first_name = first_name(recruiter.display_name)
    top_skills = match_result.best.matched_terms or opportunity.required_skills or []
    effective_settings = runtime_settings.get_effective_settings(db, settings)

    generated = draft_service.generate_email(
        job_title=opportunity.job_title,
        job_location=opportunity.job_location,
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

    application = Application(
        source_message_id=opportunity.source_message_id,
        thread_id=opportunity.thread_id,
        recruiter_id=recruiter.id,
        recruiter_name=recruiter.display_name,
        recruiter_email=recruiter.normalized_email,
        cc_email=message.from_email,
        job_title=opportunity.job_title,
        job_location=opportunity.job_location,
        local_requirement=opportunity.local_requirement,
        implementation_partner=opportunity.implementation_partner,
        end_client=opportunity.end_client,
        employment_type=opportunity.employment_type,
        requirements=opportunity.required_skills or [],
        selected_resume_id=best_resume.id,
        match_score=match_result.best.score,
        match_explanation=match_result.best.explanation,
        recruiter_confidence=1.0,
        generated_subject=generated.subject,
        generated_body=generated.body,
        candidate_recruiter_emails=[recruiter.normalized_email],
        interview_type=opportunity.interview_type,
        requires_in_person_interview=False,  # overridden - see interview_overridden below
        interview_confidence=opportunity.interview_confidence,
        interview_reason=opportunity.interview_reason,
        interview_evidence=opportunity.interview_evidence,
        status=AppStatus.DRAFT,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    add_event(db, application.id, EventType.EMAIL_RECEIVED, {"gmail_message_id": message.gmail_message_id if message else None}, commit=False)
    add_event(db, application.id, EventType.MANUAL_OVERRIDE, {"reason": "in-person-interview skip overridden"}, commit=False)
    add_event(db, application.id, EventType.RESUME_SELECTED, {"resume_id": best_resume.id, "filename": best_resume.filename}, commit=False)

    gmail_draft_id = None
    account = db.query(GmailAccount).first()
    if account is not None:
        client = get_gmail_client(settings, account.token_json)
        # Brand new standalone email - never threaded under the original
        # message, so it never appears quoted/rewritten as a "reply".
        api_response = client.create_draft_with_attachment(
            to_email=recruiter.normalized_email,
            cc_email=message.from_email,
            subject=generated.subject,
            body_text=generated.body,
            attachment_path=best_resume.file_path,
        )
        gmail_draft_id = api_response.get("id")
        new_thread_id = api_response.get("message", {}).get("threadId")
        if new_thread_id:
            application.thread_id = new_thread_id
            opportunity.thread_id = new_thread_id

    draft = Draft(
        gmail_draft_id=gmail_draft_id,
        application_id=application.id,
        to_email=recruiter.normalized_email,
        cc_email=message.from_email,
        subject=generated.subject,
        attached_resume_filename=best_resume.filename,
        status="CREATED",
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    add_event(db, application.id, EventType.DRAFT_CREATED,
              {"gmail_draft_id": gmail_draft_id, "to_email": draft.to_email, "subject": draft.subject})

    # Audit trail (section 27): preserve exactly what the system originally
    # decided, and when/that a human overrode it.
    opportunity.original_interview_type = opportunity.interview_type
    opportunity.original_requires_in_person_interview = opportunity.requires_in_person_interview
    opportunity.interview_overridden = True
    opportunity.interview_override_at = _utcnow()
    opportunity.application_id = application.id
    opportunity.draft_id = draft.id
    opportunity.status = ProcessingStatus.DRAFT_CREATED
    opportunity.selected_resume_id = best_resume.id
    opportunity.resume_match_score = match_result.best.score
    db.add(opportunity)

    if message is not None:
        message.status = ProcessingStatus.DRAFT_CREATED
        message.error_message = None
        db.add(message)

    recruiter.drafts_count += 1
    db.add(recruiter)

    db.commit()
    db.refresh(opportunity)
    return opportunity
