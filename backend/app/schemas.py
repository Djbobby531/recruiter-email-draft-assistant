"""Pydantic request/response schemas for the API layer."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class CandidateProfileIn(BaseModel):
    name: str = ""
    experience: str = ""
    work_authorization: str = ""
    phone: str = ""
    email: str = ""
    linkedin: str = ""


class CandidateProfileOut(CandidateProfileIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    updated_at: dt.datetime


class ResumeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    indexing_status: str
    extracted_metadata: dict
    created_at: dt.datetime
    updated_at: dt.datetime


class ProcessedMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    gmail_message_id: str
    thread_id: str
    from_email: str
    to_email: str
    subject: str
    status: str
    error_message: str | None
    processed_at: dt.datetime


class RecruiterSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_name: str | None
    normalized_email: str
    phone: str | None
    company: str | None


class ApplicationEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: str
    event_timestamp: dt.datetime
    event_metadata: dict


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    thread_id: str
    recruiter_id: int | None = None
    recruiter: RecruiterSummaryOut | None = None
    recruiter_name: str | None
    recruiter_email: str | None
    cc_email: str | None
    job_title: str | None
    job_location: str | None
    local_requirement: str = "UNKNOWN"
    implementation_partner: str | None = None
    end_client: str | None = None
    employment_type: str | None = None
    requirements: list
    selected_resume_id: int | None
    match_score: float | None
    match_explanation: str | None
    recruiter_confidence: float | None
    generated_subject: str | None
    generated_body: str | None
    review_reason: str | None
    candidate_recruiter_emails: list
    interview_type: str | None = None
    requires_in_person_interview: bool | None = None
    interview_confidence: float | None = None
    interview_reason: str | None = None
    interview_evidence: str | None = None
    status: str = "DRAFT"
    interview_status: str = "NOT_STARTED"
    interview_notes: str | None = None
    interview_at: dt.datetime | None = None
    sent_message_id: str | None = None
    sent_at: dt.datetime | None = None
    submitted_at: dt.datetime | None = None
    skip_reason: str | None = None
    has_customized_resume: bool = False
    created_at: dt.datetime
    updated_at: dt.datetime | None = None


class DraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    gmail_draft_id: str | None
    application_id: int
    to_email: str
    cc_email: str | None
    subject: str
    attached_resume_filename: str | None
    status: str
    created_at: dt.datetime


class ApplicationDetailOut(ApplicationOut):
    events: list[ApplicationEventOut] = []
    draft: DraftOut | None = None


class ProcessedMessageDetailOut(ProcessedMessageOut):
    application: ApplicationOut | None = None


class ApplicationStatusUpdate(BaseModel):
    status: str
    override: bool = False
    notes: str | None = None


class ManualReviewResolve(BaseModel):
    recruiter_email: str
    recruiter_name: str | None = None


class DashboardStats(BaseModel):
    emails_processed: int
    job_opportunities: int
    replies_skipped: int
    duplicates_skipped: int
    drafts_created: int
    in_person_interview_skipped: int
    manual_review: int
    errors: int
    latest_resume_selected: str | None = None
    # Application-tracker lifecycle KPIs (dashboard phase) - computed across
    # ALL applications, not just today's, since a submitted/interview status
    # is usually reached days after the original email.
    sent_count: int = 0
    submitted_count: int = 0
    interview_count: int = 0
    rejected_count: int = 0
    withdrawn_count: int = 0
    on_hold_count: int = 0
    applications_this_week: int = 0
    applications_this_month: int = 0
    interviews_this_month: int = 0
    recruiters_contacted: int = 0
    # Only computed when the denominator is meaningful (section 21) - None
    # rather than a misleading 0% when there are no submitted applications yet.
    interview_rate: float | None = None


class ChartPoint(BaseModel):
    label: str
    value: int


class DashboardCharts(BaseModel):
    applications_by_status: list[ChartPoint] = []
    applications_by_location: list[ChartPoint] = []
    applications_by_resume: list[ChartPoint] = []


class RecruiterRoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_title: str
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    opportunity_count: int


class RecruiterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    normalized_email: str
    display_name: str | None
    first_name: str | None
    last_name: str | None
    company: str | None
    recruiter_role: str | None
    phone: str | None
    notes: str | None
    linked_recruiter_id: int | None
    opportunities_count: int
    drafts_count: int
    skipped_count: int
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime


class RecruiterDetailOut(RecruiterOut):
    roles: list[RecruiterRoleOut] = []


class RecruiterUpdate(BaseModel):
    notes: str | None = None
    linked_recruiter_id: int | None = None


class OpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    recruiter_id: int
    application_id: int | None
    thread_id: str
    job_title: str | None
    job_location: str | None
    local_requirement: str = "UNKNOWN"
    company: str | None
    implementation_partner: str | None = None
    end_client: str | None = None
    employment_type: str | None
    required_skills: list
    selected_resume_id: int | None
    resume_match_score: float | None
    interview_type: str
    requires_in_person_interview: bool
    interview_confidence: float | None
    interview_reason: str | None
    interview_evidence: str | None
    status: str
    skip_reason: str | None
    draft_id: int | None
    interview_overridden: bool
    interview_override_at: dt.datetime | None
    original_interview_type: str | None
    original_requires_in_person_interview: bool | None
    received_at: dt.datetime
    created_at: dt.datetime


class InterviewOverrideRequest(BaseModel):
    confirm: bool = True


class AppSettingsCustomizationIn(BaseModel):
    """All fields optional/nullable: omit a field to leave it unchanged,
    send it as null to clear the override back to "use the default"."""
    poll_interval_seconds: int | None = None
    sent_sync_every_n_cycles: int | None = None
    poll_backoff_max_seconds: int | None = None
    allowed_sender_domains: str | None = None
    email_hope_line: str | None = None
    email_capability_sentence: str | None = None
    email_closing_line: str | None = None


class AppSettingsCustomizationOut(BaseModel):
    # raw stored overrides - None means "not customized"
    poll_interval_seconds: int | None
    sent_sync_every_n_cycles: int | None
    poll_backoff_max_seconds: int | None
    allowed_sender_domains: str | None
    email_hope_line: str | None
    email_capability_sentence: str | None
    email_closing_line: str | None
    # resolved values actually in effect right now (override if valid, else default)
    effective_poll_interval_seconds: int
    effective_sent_sync_every_n_cycles: int
    effective_poll_backoff_max_seconds: int
    effective_allowed_sender_domains: str
    effective_email_hope_line: str
    effective_email_capability_sentence: str
    effective_email_closing_line: str
    # built-in defaults, shown so the UI can offer a per-field "reset" and
    # placeholder text
    default_poll_interval_seconds: int
    default_sent_sync_every_n_cycles: int
    default_poll_backoff_max_seconds: int
    default_allowed_sender_domains: str
    default_email_hope_line: str
    default_email_capability_sentence: str
    default_email_closing_line: str
    # fields whose stored override exists but is currently invalid and is
    # falling back to the default
    invalid_fields: list[str] = []


class ManualDraftIn(BaseModel):
    """Settings/Applications UI "paste an email" box - runs pasted text
    through the exact same pipeline as a real incoming Gmail message.
    `receiver_email` is the recruiter/contact address the crafted email
    should actually be sent to - it overrides auto-detection instead of
    relying on it. `cc_email` is optional and also explicit - it overrides
    the normal "sender goes in CC" rule, since there is no real inbound
    sender here to fall back to."""
    sender_email: str
    receiver_email: str
    cc_email: str = ""
    subject: str = ""
    body: str


class ManualDraftOut(BaseModel):
    status: str
    reason: str | None = None
    application_id: int | None = None
    draft_id: int | None = None
    generated_subject: str | None = None
    generated_body: str | None = None
    recruiter_email: str | None = None
    cc_email: str | None = None
