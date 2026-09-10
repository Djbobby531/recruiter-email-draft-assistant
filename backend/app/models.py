"""SQLAlchemy ORM models for all tables described in the design doc."""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class ProcessingStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    SKIPPED_REPLY = "SKIPPED_REPLY"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    SKIPPED_NOT_JOB = "SKIPPED_NOT_JOB"
    SKIPPED_NO_RECRUITER_EMAIL = "SKIPPED_NO_RECRUITER_EMAIL"
    SKIPPED_IN_PERSON_INTERVIEW = "SKIPPED_IN_PERSON_INTERVIEW"
    # This message was sent BY the mailbox owner (e.g. it's a copy of an
    # outgoing email that shows up via the History API), never something to
    # draft a reply to. Only genuinely RECEIVED mail is ever processed.
    SKIPPED_OWN_SENT_EMAIL = "SKIPPED_OWN_SENT_EMAIL"
    # An application to this same recruiter was already confirmed SENT
    # earlier today - never draft a second application to the same recruiter
    # on the same calendar day, even if they email again (e.g. forwarding a
    # similar/duplicate role).
    SKIPPED_RECRUITER_ALREADY_CONTACTED_TODAY = "SKIPPED_RECRUITER_ALREADY_CONTACTED_TODAY"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    PROCESSING = "PROCESSING"
    DRAFT_CREATED = "DRAFT_CREATED"
    ERROR = "ERROR"


class GmailAccount(Base):
    """A single connected Gmail account (this app targets exactly one)."""

    __tablename__ = "gmail_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_address: Mapped[str] = mapped_column(String(255), unique=True)
    token_json: Mapped[str] = mapped_column(Text)  # encrypted at rest, see gmail_service
    last_history_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Explicit time watermark - the internalDate (Gmail's own received-time)
    # of the newest message the poller has ever fully finished processing.
    # This is a SEPARATE, human-inspectable safety net on top of
    # last_history_id (an opaque Gmail cursor): if last_history_id ever
    # expires (Gmail only retains history for ~a week), this watermark lets
    # the poller ask Gmail directly for "everything after this timestamp"
    # and backfill it, rather than silently jumping to "now" and dropping
    # whatever arrived during the gap. See services/poller.py.
    last_processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CandidateProfile(Base):
    __tablename__ = "candidate_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    experience: Mapped[str] = mapped_column(String(255), default="")
    work_authorization: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    linkedin: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AppSettingsOverride(Base):
    """Singleton row (id=1) of user-editable overrides for values that
    otherwise come from Settings/.env defaults - polling cadence, sender
    filtering, and a few email-template snippets. Every column is nullable:
    NULL means "not customized, use the default". Values are re-validated
    against the same rules every time they're read (see
    services/runtime_settings.py), so a bad or since-invalidated override
    never breaks the app - it just falls back to the default."""

    __tablename__ = "app_settings_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_sync_every_n_cycles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poll_backoff_max_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allowed_sender_domains: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    email_hope_line: Mapped[str | None] = mapped_column(String(300), nullable=True)
    email_capability_sentence: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email_closing_line: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(512))
    file_path: Mapped[str] = mapped_column(String(1024))
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    extracted_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    indexing_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"
    __table_args__ = (UniqueConstraint("gmail_message_id", name="uq_processed_gmail_message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gmail_message_id: Mapped[str] = mapped_column(String(128), index=True)
    thread_id: Mapped[str] = mapped_column(String(128), index=True)
    from_email: Mapped[str] = mapped_column(String(255))
    to_email: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(1024), default="")
    message_id_header: Mapped[str | None] = mapped_column(String(512), nullable=True)
    in_reply_to_header: Mapped[str | None] = mapped_column(String(512), nullable=True)
    references_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus), default=ProcessingStatus.RECEIVED
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    applications: Mapped[list[Application]] = relationship(back_populates="source_message")


class AppStatus:
    """
    Application lifecycle status (dashboard/tracker phase). Deliberately a
    plain string enum (not a SQLAlchemy Enum type) stored as VARCHAR, matching
    how `Draft.status` already works - this keeps the lightweight ALTER-TABLE
    migration path in database.py trivial and avoids a SQLite enum migration.

    CRITICAL RULE: creating a Gmail draft alone must NEVER produce SENT or
    SUBMITTED - only confirmed Gmail sent-message evidence (see
    services/sent_detection.py) or an explicit user action can do that.
    """

    DRAFT = "DRAFT"
    SENT = "SENT"
    SUBMITTED = "SUBMITTED"
    INTERVIEW = "INTERVIEW"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    ON_HOLD = "ON_HOLD"
    SKIPPED = "SKIPPED"
    MANUAL_REVIEW = "MANUAL_REVIEW"

    ALL = (DRAFT, SENT, SUBMITTED, INTERVIEW, REJECTED, WITHDRAWN, ON_HOLD, SKIPPED, MANUAL_REVIEW)


class InterviewStatusValue:
    """Granular interview-stage metadata (section 19). The primary
    user-facing status stays AppStatus.INTERVIEW; this is additional detail."""

    NOT_STARTED = "NOT_STARTED"
    NO_INTERVIEW = "NO_INTERVIEW"
    INTERVIEW_REQUESTED = "INTERVIEW_REQUESTED"
    INTERVIEW_SCHEDULED = "INTERVIEW_SCHEDULED"
    INTERVIEW_COMPLETED = "INTERVIEW_COMPLETED"
    TECHNICAL_ROUND = "TECHNICAL_ROUND"
    FINAL_ROUND = "FINAL_ROUND"


class EventType:
    """application_events.event_type values (section 25) - the audit trail."""

    EMAIL_RECEIVED = "EMAIL_RECEIVED"
    JOB_DETECTED = "JOB_DETECTED"
    RESUME_SELECTED = "RESUME_SELECTED"
    DRAFT_CREATED = "DRAFT_CREATED"
    EMAIL_SENT = "EMAIL_SENT"
    APPLICATION_SUBMITTED = "APPLICATION_SUBMITTED"
    INTERVIEW_REQUESTED = "INTERVIEW_REQUESTED"
    INTERVIEW_SCHEDULED = "INTERVIEW_SCHEDULED"
    INTERVIEW_COMPLETED = "INTERVIEW_COMPLETED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    SKIPPED = "SKIPPED"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_message_id: Mapped[int] = mapped_column(ForeignKey("processed_messages.id"))
    thread_id: Mapped[str] = mapped_column(String(128), index=True)
    recruiter_id: Mapped[int | None] = mapped_column(ForeignKey("recruiters.id"), nullable=True, index=True)
    recruiter_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recruiter_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cc_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(512), index=True, nullable=True)
    job_location: Mapped[str | None] = mapped_column(String(512), index=True, nullable=True)
    local_requirement: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    implementation_partner: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    end_client: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requirements: Mapped[list] = mapped_column(JSON, default=list)
    selected_resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"), nullable=True)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    recruiter_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    generated_subject: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    generated_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_recruiter_emails: Mapped[list] = mapped_column(JSON, default=list)
    # Interview-screening result (recruiter-CRM phase). Populated once JD
    # extraction succeeds, whenever an Application row exists for the message -
    # a SKIPPED_IN_PERSON_INTERVIEW outcome never creates an Application at
    # all (see Opportunity below), so these are only ever REMOTE/UNKNOWN here,
    # unless an in-person skip was later manually overridden into a draft.
    interview_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    requires_in_person_interview: Mapped[bool | None] = mapped_column(nullable=True)
    interview_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    interview_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Application-tracker lifecycle (dashboard phase) ---
    # `status` is the user-facing tracker status (AppStatus.*) and is
    # DELIBERATELY separate from ProcessedMessage.status/Opportunity.status
    # (the pipeline-processing status). A Gmail draft being created only ever
    # sets this to DRAFT - never SENT/SUBMITTED.
    status: Mapped[str] = mapped_column(String(32), default=AppStatus.DRAFT, index=True)
    interview_status: Mapped[str] = mapped_column(String(32), default=InterviewStatusValue.NOT_STARTED)
    interview_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI-assisted resume customization (never exposed via the API - it's a
    # local filesystem path). Points at a COPY of the selected resume with an
    # added "Additional Relevant Skills" section; the original library resume
    # is never modified. None when no customization was produced (e.g. the
    # resume isn't a .docx, or there was nothing truthful to add).
    customized_resume_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    source_message: Mapped[ProcessedMessage] = relationship(back_populates="applications")
    selected_resume: Mapped[Resume | None] = relationship()
    draft: Mapped[Draft | None] = relationship(back_populates="application", uselist=False)
    recruiter: Mapped[Recruiter | None] = relationship()
    events: Mapped[list[ApplicationEvent]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="ApplicationEvent.event_timestamp"
    )

    @property
    def has_customized_resume(self) -> bool:
        return self.customized_resume_path is not None


class ApplicationEvent(Base):
    """Audit-trail row for the application lifecycle (section 25). The Python
    attribute is named `event_metadata` (not `metadata`) because `metadata` is
    reserved on every SQLAlchemy declarative class; the underlying DB column
    is still named `metadata` per the spec's field list."""

    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    event_timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    application: Mapped[Application] = relationship(back_populates="events")


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gmail_draft_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), unique=True)
    to_email: Mapped[str] = mapped_column(String(255))
    cc_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str] = mapped_column(String(1024))
    attached_resume_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="CREATED")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    application: Mapped[Application] = relationship(back_populates="draft")


class Recruiter(Base):
    """
    Recruiter directory / CRM (section 1-7). Uniqueness key is the normalized
    email address ONLY - never the display name, so two different people who
    happen to share a name are never merged (section 7).
    """

    __tablename__ = "recruiters"
    __table_args__ = (UniqueConstraint("normalized_email", name="uq_recruiter_normalized_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    normalized_email: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recruiter_role: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. "Talent Acquisition Executive"
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_email_addresses: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional, user-set link to a different Recruiter row believed to be the
    # same person under another email address. Never set automatically -
    # section 7 explicitly forbids auto-merging on name match alone.
    linked_recruiter_id: Mapped[int | None] = mapped_column(ForeignKey("recruiters.id"), nullable=True)
    opportunities_count: Mapped[int] = mapped_column(Integer, default=0)
    drafts_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    roles: Mapped[list[RecruiterRole]] = relationship(back_populates="recruiter", cascade="all, delete-orphan")
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="recruiter", cascade="all, delete-orphan")
    linked_recruiter: Mapped[Recruiter | None] = relationship(remote_side=[id])


class RecruiterRole(Base):
    """One row per distinct job title a given recruiter has sent (section 4)."""

    __tablename__ = "recruiter_roles"
    __table_args__ = (UniqueConstraint("recruiter_id", "job_title", name="uq_recruiter_role_title"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recruiter_id: Mapped[int] = mapped_column(ForeignKey("recruiters.id"))
    job_title: Mapped[str] = mapped_column(String(512))
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opportunity_count: Mapped[int] = mapped_column(Integer, default=1)

    recruiter: Mapped[Recruiter] = relationship(back_populates="roles")


class Opportunity(Base):
    """
    Recruiter-CRM ledger entry (section 8/19). Created for every job email
    that reaches a confidently-identified recruiter, independent of the
    `applications` table (which remains the draft-pipeline record used by the
    existing Applications/manual-review UI). A message that is skipped for
    requiring an in-person interview gets ONLY an Opportunity row - no
    Application/Draft is ever created for it unless a user later overrides
    the skip (section 27).
    """

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recruiter_id: Mapped[int] = mapped_column(ForeignKey("recruiters.id"))
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    source_message_id: Mapped[int] = mapped_column(ForeignKey("processed_messages.id"))
    thread_id: Mapped[str] = mapped_column(String(128), index=True)
    job_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    job_location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    local_requirement: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    implementation_partner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    end_client: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    required_skills: Mapped[list] = mapped_column(JSON, default=list)
    selected_resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"), nullable=True)
    resume_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    interview_type: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    requires_in_person_interview: Mapped[bool] = mapped_column(default=False)
    interview_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    interview_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProcessingStatus] = mapped_column(Enum(ProcessingStatus), default=ProcessingStatus.RECEIVED)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("drafts.id"), nullable=True)
    # Manual override audit trail (section 27) - set only when a user
    # explicitly overrides an in-person-interview skip to continue processing.
    interview_overridden: Mapped[bool] = mapped_column(default=False)
    interview_override_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    original_interview_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    original_requires_in_person_interview: Mapped[bool | None] = mapped_column(nullable=True)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    recruiter: Mapped[Recruiter] = relationship(back_populates="opportunities")
    application: Mapped[Application | None] = relationship()
    source_message: Mapped[ProcessedMessage] = relationship()
    selected_resume: Mapped[Resume | None] = relationship()
    draft: Mapped[Draft | None] = relationship()
