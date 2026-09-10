"""
Recruiter directory persistence (sections 1, 4, 6-8, 18). Every function here
is a pure DB read/write - the actual field extraction lives in
recruiter_info_extractor.py so this module stays independently testable
against plain inputs.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.models import Recruiter, RecruiterRole
from app.services.recruiter_info_extractor import RecruiterInfo, split_display_name
from app.utils.email_utils import normalize_email


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def get_recruiter_by_email(db: Session, email: str) -> Recruiter | None:
    normalized = normalize_email(email)
    return db.query(Recruiter).filter(Recruiter.normalized_email == normalized).first()


def upsert_recruiter(db: Session, email: str, info: RecruiterInfo) -> Recruiter:
    """
    Create-or-update a recruiter record keyed ONLY on normalized email
    (RULE/section 7) - never on name, so two different people who happen to
    share a name are never accidentally merged. Calling this again for the
    same email just refreshes last_seen_at and any newly-discovered fields;
    it never creates a duplicate row for an email already on file.
    """
    normalized = normalize_email(email)
    recruiter = get_recruiter_by_email(db, normalized)
    now = utcnow()

    if recruiter is None:
        first_name, last_name = split_display_name(info.name)
        recruiter = Recruiter(
            normalized_email=normalized,
            display_name=info.name,
            first_name=first_name,
            last_name=last_name,
            company=info.company,
            recruiter_role=info.recruiter_role,
            phone=info.phone,
            source_email_addresses=[normalized],
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(recruiter)
        db.commit()
        db.refresh(recruiter)
        return recruiter

    recruiter.last_seen_at = now
    # only overwrite a field when this extraction actually found something -
    # never blank out previously-known info just because a later email's
    # signature happened to omit it
    if info.name and not recruiter.display_name:
        recruiter.display_name = info.name
        first_name, last_name = split_display_name(info.name)
        recruiter.first_name = first_name
        recruiter.last_name = last_name
    if info.company:
        recruiter.company = info.company
    if info.recruiter_role:
        recruiter.recruiter_role = info.recruiter_role
    if info.phone:
        recruiter.phone = info.phone
    if normalized not in (recruiter.source_email_addresses or []):
        recruiter.source_email_addresses = [*(recruiter.source_email_addresses or []), normalized]

    db.add(recruiter)
    db.commit()
    db.refresh(recruiter)
    return recruiter


def record_role_sent(db: Session, recruiter: Recruiter, job_title: str | None) -> RecruiterRole | None:
    """Tracks the role-history table (section 4). No-op if job_title is unknown."""
    if not job_title:
        return None

    now = utcnow()
    role = (
        db.query(RecruiterRole)
        .filter(RecruiterRole.recruiter_id == recruiter.id, RecruiterRole.job_title == job_title)
        .first()
    )
    if role is None:
        role = RecruiterRole(
            recruiter_id=recruiter.id, job_title=job_title,
            first_seen_at=now, last_seen_at=now, opportunity_count=1,
        )
        db.add(role)
    else:
        role.last_seen_at = now
        role.opportunity_count += 1
        db.add(role)

    db.commit()
    db.refresh(role)
    return role


def increment_opportunities_count(db: Session, recruiter: Recruiter) -> None:
    recruiter.opportunities_count += 1
    db.add(recruiter)
    db.commit()


def increment_drafts_count(db: Session, recruiter: Recruiter) -> None:
    recruiter.drafts_count += 1
    db.add(recruiter)
    db.commit()


def increment_skipped_count(db: Session, recruiter: Recruiter) -> None:
    recruiter.skipped_count += 1
    db.add(recruiter)
    db.commit()


def delete_recruiter(db: Session, recruiter: Recruiter) -> None:
    """Deletes a recruiter and (via cascade) their role history and
    opportunity records - section 18 data-privacy control."""
    db.delete(recruiter)
    db.commit()
