"""
The end-to-end email processing pipeline (section 7/27 of the spec):

NEW EMAIL -> duplicate check -> reply check -> parse -> extract addresses ->
recruiter identification -> job classification -> JD extraction -> resume
matching -> email generation -> draft creation -> persist result.

This module is pure orchestration: every real decision is delegated to a
dedicated, independently-testable service. Gmail I/O is injected via a
`GmailClient`-shaped object so the pipeline can be tested without real
network calls.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ai.base import AIProvider
from app.config import Settings
from app.models import (
    Application,
    AppStatus,
    CandidateProfile,
    Draft,
    EventType,
    Opportunity,
    ProcessedMessage,
    ProcessingStatus,
    Resume,
)
from app.services import (
    draft_service,
    interview_classifier,
    jd_extractor,
    job_classifier,
    reply_detector,
    resume_customizer,
    runtime_settings,
)
from app.services.application_events import add_event
from app.services.email_parser import ParsedEmail, parse_gmail_message
from app.services.recruiter_info_extractor import extract_recruiter_info
from app.services.recruiter_selector import (
    RecruiterCandidate,
    RecruiterSelection,
    build_subject_prefix_name,
    select_recruiter_email,
)
from app.services.recruiter_service import (
    increment_drafts_count,
    increment_opportunities_count,
    increment_skipped_count,
    record_role_sent,
    upsert_recruiter,
)
from app.services.resume_matcher import match_resumes
from app.utils.email_utils import guess_name_for_email, normalize_email

logger = logging.getLogger("app.pipeline")

# Statuses that mean "this message is fully, terminally handled - never touch it
# again automatically". ERROR is deliberately excluded: a failed attempt (Gmail
# timeout, AI outage, transient DB issue) must be retryable (section 20/21).
# MANUAL_REVIEW is also excluded from auto-retry-on-poll but is handled below by
# the draft-exists check rather than blind reprocessing, since a human may be
# mid-review.
_TERMINAL_NO_RETRY_STATUSES = (
    ProcessingStatus.SKIPPED_REPLY,
    ProcessingStatus.SKIPPED_DUPLICATE,
    ProcessingStatus.SKIPPED_NOT_JOB,
    ProcessingStatus.SKIPPED_NO_RECRUITER_EMAIL,
    ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW,
    ProcessingStatus.SKIPPED_OWN_SENT_EMAIL,
    ProcessingStatus.SKIPPED_RECRUITER_ALREADY_CONTACTED_TODAY,
    ProcessingStatus.DRAFT_CREATED,
)


@dataclass
class PipelineResult:
    status: ProcessingStatus
    processed_message: ProcessedMessage
    application: Application | None = None
    draft: Draft | None = None
    opportunity: Opportunity | None = None
    reason: str | None = None


def _get_or_create_processed_message(db: Session, parsed: ParsedEmail) -> tuple[ProcessedMessage, bool]:
    """Returns (record, already_existed)."""
    existing = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.gmail_message_id == parsed.gmail_message_id)
        .first()
    )
    if existing:
        return existing, True

    record = ProcessedMessage(
        gmail_message_id=parsed.gmail_message_id,
        thread_id=parsed.thread_id,
        from_email=parsed.from_email,
        to_email=parsed.to_email,
        subject=parsed.subject,
        message_id_header=parsed.message_id_header,
        in_reply_to_header=parsed.in_reply_to_header,
        references_header=parsed.references_header,
        status=ProcessingStatus.RECEIVED,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record, False


def _finalize(db: Session, record: ProcessedMessage, status: ProcessingStatus, error: str | None = None) -> None:
    record.status = status
    record.error_message = error
    db.add(record)
    db.commit()


def _get_or_create_opportunity(db: Session, record: ProcessedMessage, recruiter, parsed: ParsedEmail) -> tuple[Opportunity, bool]:
    """
    Returns (opportunity, created). Keyed on source_message_id so a retry
    after a failed/errored attempt reuses and updates the same ledger row
    instead of creating a duplicate and double-counting the recruiter's
    opportunities_count (section 8/19).
    """
    existing = db.query(Opportunity).filter(Opportunity.source_message_id == record.id).first()
    if existing is not None:
        return existing, False

    opportunity = Opportunity(
        recruiter_id=recruiter.id,
        source_message_id=record.id,
        thread_id=parsed.thread_id,
        status=ProcessingStatus.PROCESSING,
        received_at=record.processed_at,
    )
    db.add(opportunity)
    db.commit()
    db.refresh(opportunity)
    increment_opportunities_count(db, recruiter)
    return opportunity, True


def process_message(
    db: Session,
    settings: Settings,
    raw_message: dict,
    gmail_client,  # GmailClient | None - None means "dry run, don't create a real draft"
    ai_provider: AIProvider | None,
    recruiter_email_override: str | None = None,
    cc_email_override: str | None = None,
) -> PipelineResult:
    parsed = parse_gmail_message(raw_message)

    record, already_existed = _get_or_create_processed_message(db, parsed)

    if already_existed:
        existing_application = (
            db.query(Application).filter(Application.source_message_id == record.id).first()
        )

        # Hard safety net (DUP-03): if a draft already exists for this message's
        # application, NEVER call Gmail again, regardless of what record.status
        # says - status could be stale if a worker crashed between the Gmail API
        # call succeeding and the local commit finalizing.
        if existing_application is not None and existing_application.draft is not None:
            return PipelineResult(
                status=ProcessingStatus.SKIPPED_DUPLICATE, processed_message=record,
                application=existing_application, draft=existing_application.draft,
                reason="a Gmail draft already exists for this message",
            )

        if record.status in _TERMINAL_NO_RETRY_STATUSES:
            # TEST 11: same Gmail message processed twice -> only one draft, ever.
            return PipelineResult(status=ProcessingStatus.SKIPPED_DUPLICATE, processed_message=record,
                                   reason="message already processed previously")

        if record.status == ProcessingStatus.MANUAL_REVIEW:
            # Awaiting a human decision via the manual-review UI - don't silently
            # reprocess and overwrite what they're looking at.
            return PipelineResult(status=ProcessingStatus.MANUAL_REVIEW, processed_message=record,
                                   application=existing_application,
                                   reason="message is awaiting manual review")

        if existing_application is not None:
            # A prior RECEIVED/PROCESSING/ERROR attempt left a partial application
            # with no draft (e.g. crashed before Gmail draft creation, or before
            # that commit landed). Safe to discard and recompute from scratch -
            # the source message is unchanged, so deterministic steps reproduce
            # identically, and no Gmail draft was ever created for it.
            db.delete(existing_application)
            db.commit()

    try:
        # Only genuinely RECEIVED mail is ever processed - a message the
        # mailbox owner sent themselves (which still shows up via the Gmail
        # History API, since sending also touches the mailbox's history) is
        # never a candidate to draft an application reply to.
        if settings.MY_EMAIL and normalize_email(parsed.from_email) == normalize_email(settings.MY_EMAIL):
            reason = "message was sent by the mailbox owner, not received"
            _finalize(db, record, ProcessingStatus.SKIPPED_OWN_SENT_EMAIL, reason)
            return PipelineResult(ProcessingStatus.SKIPPED_OWN_SENT_EMAIL, record, reason=reason)

        # RULE A - reply detection (highest priority)
        reply, reply_reason = reply_detector.is_reply(db, parsed)
        if reply:
            _finalize(db, record, ProcessingStatus.SKIPPED_REPLY, reply_reason)
            return PipelineResult(ProcessingStatus.SKIPPED_REPLY, record, reason=reply_reason)

        record.status = ProcessingStatus.PROCESSING
        db.add(record)
        db.commit()

        # Job/recruiter classification
        classification = job_classifier.classify(
            subject=parsed.subject,
            body_text=parsed.full_text,
            threshold=settings.JOB_CLASSIFICATION_THRESHOLD,
            ai_provider=ai_provider,
        )
        if not classification.is_job_email:
            _finalize(db, record, ProcessingStatus.SKIPPED_NOT_JOB, classification.reason)
            return PipelineResult(ProcessingStatus.SKIPPED_NOT_JOB, record, reason=classification.reason)

        # RULE B/C/G - recruiter/contact identification. A low CONFIDENCE
        # score no longer blocks draft creation (by explicit configuration
        # choice - see README "Business rules implemented") - it always
        # proceeds with the best candidate found. If literally no other email
        # address exists anywhere in the message, the sender itself becomes
        # the recruiter contact as a last-resort fallback, so the pipeline
        # never needs a human to type in an address by hand.
        if recruiter_email_override:
            # Manual-draft path (Settings/Applications UI "paste an email" box)
            # - the user typed the destination address themselves, so it
            # always wins over auto-detection from the pasted text.
            override_email = normalize_email(recruiter_email_override)
            recruiter_selection = RecruiterSelection(
                selected=RecruiterCandidate(
                    email=override_email,
                    name=guess_name_for_email(parsed.full_text, override_email),
                    score=1.0,
                    reasons=["manually specified by the user"],
                ),
                all_candidates=[],
                confidence=1.0,
                reason="recruiter email explicitly provided by the user",
            )
        else:
            recruiter_selection = select_recruiter_email(parsed, settings.MY_EMAIL)
            if recruiter_selection.selected is None:
                sender_email = normalize_email(parsed.from_email)
                sender_name = guess_name_for_email(parsed.full_text, sender_email)
                recruiter_selection = RecruiterSelection(
                    selected=RecruiterCandidate(
                        email=sender_email, name=sender_name, score=0.0,
                        reasons=["fallback: no other candidate email found anywhere in the message; used the sender"],
                    ),
                    all_candidates=[],
                    confidence=0.0,
                    reason="no other candidate email found; fell back to using the sender as the recruiter contact",
                )

        assert recruiter_selection.selected is not None  # guaranteed by the branches above

        if cc_email_override:
            # Manual-draft path - explicit CC also always wins over the
            # automatic rule below, since there's no real inbound sender here.
            cc_email = normalize_email(cc_email_override)
        elif recruiter_email_override:
            cc_email = None
        else:
            # RULE C - the incoming sender goes in CC, UNLESS the sender IS the
            # selected recruiter (the fallback above), in which case there's no
            # separate address left to CC.
            cc_email = (
                None if recruiter_selection.selected.email == normalize_email(parsed.from_email)
                else parsed.from_email
            )

        # Recruiter directory (sections 1-8): store/update the recruiter's
        # contact details as soon as they're confidently identified, and log
        # this contact whenever it happens regardless of the eventual outcome
        # (interview-skip, manual review, or a completed draft) below.
        recruiter_info = extract_recruiter_info(parsed.full_text, recruiter_selection.selected.name)
        recruiter = upsert_recruiter(db, recruiter_selection.selected.email, recruiter_info)
        opportunity, _ = _get_or_create_opportunity(db, record, recruiter, parsed)

        # Duplicate-outreach guard: never draft a second application to the
        # same recruiter (by normalized email) on the same calendar day, even
        # if they email again (e.g. forwarding the same/a similar role) -
        # only an actually-CONFIRMED-sent application counts, never a mere
        # draft, so this can never falsely trigger off an unsent draft.
        today_start = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        already_sent_today = (
            db.query(Application)
            .filter(Application.recruiter_id == recruiter.id, Application.sent_at >= today_start)
            .first()
        )
        if already_sent_today is not None:
            sent_at_str = already_sent_today.sent_at.isoformat() if already_sent_today.sent_at else "today"
            reason = (
                f"an application was already sent to this recruiter today "
                f"(application #{already_sent_today.id}, sent at {sent_at_str})"
            )
            opportunity.status = ProcessingStatus.SKIPPED_RECRUITER_ALREADY_CONTACTED_TODAY
            opportunity.skip_reason = reason
            db.add(opportunity)
            db.commit()
            _finalize(db, record, ProcessingStatus.SKIPPED_RECRUITER_ALREADY_CONTACTED_TODAY, reason)
            return PipelineResult(
                ProcessingStatus.SKIPPED_RECRUITER_ALREADY_CONTACTED_TODAY, record,
                opportunity=opportunity, reason=reason,
            )

        # JD extraction
        job_details = jd_extractor.extract_job_details(
            text=parsed.full_text, subject=parsed.subject, ai_provider=ai_provider
        )
        employment_type = jd_extractor.extract_employment_type(parsed.full_text)
        local_requirement = jd_extractor.extract_local_requirement(parsed.full_text)
        implementation_partner = jd_extractor.extract_implementation_partner(
            parsed.full_text, fallback_company=recruiter.company
        )
        end_client = jd_extractor.extract_end_client(parsed.full_text)
        record_role_sent(db, recruiter, job_details.job_title)

        opportunity.job_title = job_details.job_title
        opportunity.job_location = job_details.job_location
        opportunity.local_requirement = local_requirement
        opportunity.company = recruiter.company
        opportunity.implementation_partner = implementation_partner
        opportunity.end_client = end_client
        opportunity.employment_type = employment_type
        opportunity.required_skills = job_details.requirements
        db.add(opportunity)
        db.commit()

        # Interview screening (sections 9-15) - runs BEFORE resume matching
        # and email generation so neither is wastefully performed for an
        # opportunity that's about to be skipped anyway.
        #
        # By explicit configuration choice, the skip below is scoped ONLY to
        # a genuinely in-person/face-to-face/F2F interview requirement
        # (interview_type == "IN_PERSON", e.g. "final round is in-person" or
        # "F2F interview required") - ONSITE (e.g. "final round is onsite at
        # our office"), HYBRID, UNKNOWN, and any JD wording about a
        # requirement being "mandatory" (a certification, a skill, etc.)
        # never skip or block draft creation on their own. The system never
        # withholds an application over a requirement that might not be
        # fully met; it only ever skips when the INTERVIEW ITSELF is
        # explicitly stated to require in-person/face-to-face attendance.
        interview_result = interview_classifier.classify_interview_requirement(
            parsed.full_text, ai_provider=ai_provider
        )
        opportunity.interview_type = interview_result.interview_type
        opportunity.requires_in_person_interview = interview_result.requires_in_person_interview
        opportunity.interview_confidence = interview_result.confidence
        opportunity.interview_reason = interview_result.reason
        opportunity.interview_evidence = interview_result.evidence

        if interview_result.interview_type == "IN_PERSON":
            opportunity.status = ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW
            opportunity.skip_reason = interview_result.reason
            db.add(opportunity)
            db.commit()
            increment_skipped_count(db, recruiter)
            _finalize(db, record, ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW, interview_result.reason)
            return PipelineResult(
                ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW, record,
                opportunity=opportunity, reason=interview_result.reason,
            )

        db.add(opportunity)
        db.commit()

        application = _create_application_stub(
            db, record, parsed, recruiter_selection, cc_email=cc_email,
            job_title=job_details.job_title,
            job_location=job_details.job_location,
            requirements=job_details.requirements,
            recruiter_id=recruiter.id,
            local_requirement=local_requirement,
            implementation_partner=implementation_partner,
            end_client=end_client,
            employment_type=employment_type,
        )
        application.interview_type = interview_result.interview_type
        application.requires_in_person_interview = interview_result.requires_in_person_interview
        application.interview_confidence = interview_result.confidence
        application.interview_reason = interview_result.reason
        application.interview_evidence = interview_result.evidence
        db.add(application)
        db.commit()
        add_event(db, application.id, EventType.EMAIL_RECEIVED, {"gmail_message_id": parsed.gmail_message_id})
        add_event(db, application.id, EventType.JOB_DETECTED, {"job_title": job_details.job_title, "job_location": job_details.job_location})

        opportunity.application_id = application.id
        db.add(opportunity)
        db.commit()

        # Resume matching (RULE H). Always attaches the highest-scoring
        # available resume - the only thing that still routes to manual
        # review here is having zero indexed resumes to choose from at all
        # (match_result.best is None). A low match SCORE no longer blocks
        # draft creation; it's simply the best of what's on file, and
        # match_explanation on the application/draft records exactly how
        # well (or poorly) it matched for you to review afterward.
        resumes = db.query(Resume).order_by(Resume.id.asc()).all()
        match_result = match_resumes(
            resumes, jd_title=job_details.job_title, jd_text=parsed.full_text,
            jd_requirements=job_details.requirements,
            min_score=settings.RESUME_MATCH_CONFIDENCE_THRESHOLD,
        )
        if match_result.best is None:
            application.review_reason = "no resumes uploaded/indexed to match against"
            db.add(application)
            opportunity.status = ProcessingStatus.MANUAL_REVIEW
            db.add(opportunity)
            db.commit()
            _finalize(db, record, ProcessingStatus.MANUAL_REVIEW, application.review_reason)
            return PipelineResult(ProcessingStatus.MANUAL_REVIEW, record, application=application,
                                   opportunity=opportunity, reason=application.review_reason)

        best_resume = db.query(Resume).filter(Resume.id == match_result.best.resume_id).first()
        if best_resume is None:
            # extremely unlikely (TOCTOU: resume deleted between matching and
            # this lookup) but must never crash - fail safe into manual review
            # rather than attach nothing/None.
            application.review_reason = "selected resume was removed before the draft could be created"
            db.add(application)
            opportunity.status = ProcessingStatus.MANUAL_REVIEW
            db.add(opportunity)
            db.commit()
            _finalize(db, record, ProcessingStatus.MANUAL_REVIEW, application.review_reason)
            return PipelineResult(ProcessingStatus.MANUAL_REVIEW, record, application=application,
                                   opportunity=opportunity, reason=application.review_reason)

        application.selected_resume_id = best_resume.id

        # AI-assisted match scoring + truthful, format-preserving resume
        # customization (acting as a senior recruiter + senior data/software
        # engineer). Only ever surfaces skills the candidate's own resume
        # metadata already claims - never invents anything. Only .docx
        # resumes get an actual customized file; PDFs/legacy .doc keep the
        # original attached unchanged, since there's no safe way to rewrite
        # their layout in place.
        customization = resume_customizer.evaluate_and_customize(
            best_resume, jd_title=job_details.job_title, jd_text=parsed.full_text,
            jd_requirements=job_details.requirements, base_score=match_result.best, ai_provider=ai_provider,
        )
        application.match_score = customization.match_score
        application.match_explanation = customization.match_explanation

        attachment_path = best_resume.file_path
        attachment_filename = best_resume.filename
        customized = resume_customizer.customize_resume_file(
            best_resume, customization.additional_points, dest_dir=settings.RESUME_DIR
        )
        if customized is not None:
            customized_path, customized_filename = customized
            application.customized_resume_path = customized_path
            attachment_path = customized_path
            attachment_filename = customized_filename

        db.add(application)
        opportunity.selected_resume_id = best_resume.id
        opportunity.resume_match_score = customization.match_score
        db.add(opportunity)
        db.commit()
        add_event(db, application.id, EventType.RESUME_SELECTED,
                  {"resume_id": best_resume.id, "filename": attachment_filename, "match_score": customization.match_score,
                   "customized": customized is not None})

        # Email generation (RULE D/E/F)
        profile = db.query(CandidateProfile).first()
        if profile is None:
            application.review_reason = "candidate profile not configured"
            db.add(application)
            opportunity.status = ProcessingStatus.MANUAL_REVIEW
            db.add(opportunity)
            db.commit()
            _finalize(db, record, ProcessingStatus.MANUAL_REVIEW, application.review_reason)
            return PipelineResult(ProcessingStatus.MANUAL_REVIEW, record, application=application,
                                   opportunity=opportunity, reason=application.review_reason)

        recruiter_first_name = build_subject_prefix_name(recruiter_selection.selected)
        top_skills = match_result.best.matched_terms or job_details.requirements
        effective_settings = runtime_settings.get_effective_settings(db, settings)
        generated = draft_service.generate_email(
            job_title=job_details.job_title,
            job_location=job_details.job_location,
            recruiter_first_name=recruiter_first_name,
            top_skills=top_skills,
            candidate_name=profile.name,
            candidate_experience=profile.experience,
            candidate_work_auth=profile.work_authorization,
            candidate_phone=profile.phone,
            candidate_email=profile.email,
            candidate_linkedin=profile.linkedin,
            ai_provider=ai_provider,
            hope_line=effective_settings.email_hope_line,
            capability_sentence=effective_settings.email_capability_sentence,
            closing_line=effective_settings.email_closing_line,
        )
        application.generated_subject = generated.subject
        application.generated_body = generated.body
        db.add(application)
        db.commit()

        # RULE I/J - Gmail draft creation with attachment, never auto-send.
        # Deliberately created as a BRAND NEW, standalone email - never
        # threaded under the original recruiter email - so it never shows up
        # quoted/rewritten as a "reply" in Gmail; it's a fresh message that
        # just happens to start "Hi <name>,". Gmail assigns it its own new
        # thread since no threadId/In-Reply-To/References is given.
        gmail_draft_id = None
        if gmail_client is not None:
            api_response = gmail_client.create_draft_with_attachment(
                to_email=recruiter_selection.selected.email,
                cc_email=cc_email,
                subject=generated.subject,
                body_text=generated.body,
                attachment_path=attachment_path,
            )
            gmail_draft_id = api_response.get("id")
            # Track wherever Gmail actually placed this new standalone
            # message so sent-detection (services/sent_detection.py) later
            # looks at the right thread - never the original received email's.
            new_thread_id = api_response.get("message", {}).get("threadId")
            if new_thread_id:
                application.thread_id = new_thread_id

        draft = Draft(
            gmail_draft_id=gmail_draft_id,
            application_id=application.id,
            to_email=recruiter_selection.selected.email,
            cc_email=cc_email,
            subject=generated.subject,
            attached_resume_filename=attachment_filename,
            status="CREATED",
        )
        db.add(draft)
        _finalize(db, record, ProcessingStatus.DRAFT_CREATED)
        db.add(draft)
        db.commit()
        db.refresh(draft)

        # A Gmail draft existing is NOT the same as the application being
        # sent - the tracker status only ever becomes DRAFT here. SENT is set
        # exclusively by services/sent_detection.py once Gmail confirms an
        # actual sent message (section 2/3/35 - the critical tracking rule).
        application.status = AppStatus.DRAFT
        db.add(application)
        db.commit()
        add_event(db, application.id, EventType.DRAFT_CREATED,
                  {"gmail_draft_id": gmail_draft_id, "to_email": draft.to_email, "subject": draft.subject})

        opportunity.status = ProcessingStatus.DRAFT_CREATED
        opportunity.draft_id = draft.id
        db.add(opportunity)
        db.commit()
        increment_drafts_count(db, recruiter)

        return PipelineResult(
            ProcessingStatus.DRAFT_CREATED, record, application=application, draft=draft, opportunity=opportunity
        )

    except Exception as exc:  # never create a bad draft on failure - mark ERROR, allow retry
        logger.exception("pipeline error processing message %s", parsed.gmail_message_id)
        _finalize(db, record, ProcessingStatus.ERROR, str(exc))
        return PipelineResult(ProcessingStatus.ERROR, record, reason=str(exc))


def _create_application_stub(
    db: Session,
    record: ProcessedMessage,
    parsed: ParsedEmail,
    recruiter_selection,
    job_title: str | None,
    job_location: str | None,
    requirements: list[str],
    cc_email: str | None,
    recruiter_id: int | None = None,
    local_requirement: str = "UNKNOWN",
    implementation_partner: str | None = None,
    end_client: str | None = None,
    employment_type: str | None = None,
) -> Application:
    application = Application(
        source_message_id=record.id,
        thread_id=parsed.thread_id,
        recruiter_id=recruiter_id,
        recruiter_name=recruiter_selection.selected.name if recruiter_selection.selected else None,
        recruiter_email=recruiter_selection.selected.email if recruiter_selection.selected else None,
        cc_email=cc_email,
        job_title=job_title,
        job_location=job_location,
        local_requirement=local_requirement,
        implementation_partner=implementation_partner,
        end_client=end_client,
        employment_type=employment_type,
        requirements=requirements,
        # Every application starts as MANUAL_REVIEW-eligible until a Gmail
        # draft is actually created (set explicitly at that point below) -
        # this stub is also used directly as the terminal state for the
        # low-recruiter-confidence and resume/profile-missing branches.
        status=AppStatus.MANUAL_REVIEW,
        recruiter_confidence=recruiter_selection.confidence,
        candidate_recruiter_emails=[c.email for c in recruiter_selection.all_candidates],
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application
