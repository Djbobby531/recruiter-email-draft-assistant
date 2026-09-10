"""
Recruiter directory API (sections 1, 5-7, 17-18).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Opportunity, Recruiter, RecruiterRole
from app.schemas import OpportunityOut, RecruiterDetailOut, RecruiterOut, RecruiterUpdate
from app.services.recruiter_info_extractor import normalize_phone_digits
from app.services.recruiter_service import delete_recruiter as delete_recruiter_service

router = APIRouter(prefix="/api/recruiters", tags=["recruiters"])

_SORTABLE_FIELDS = {
    "last_seen_at": Recruiter.last_seen_at,
    "first_seen_at": Recruiter.first_seen_at,
    "opportunities_count": Recruiter.opportunities_count,
    "drafts_count": Recruiter.drafts_count,
    "display_name": Recruiter.display_name,
    "company": Recruiter.company,
}


def _dedupe_by_phone(recruiters: list[Recruiter]) -> list[Recruiter]:
    """Keeps only one recruiter per distinct (normalized) phone number, so
    e.g. "(737) 304-8920" and "737-304-8920" collapse to a single row -
    recruiters with no phone at all are always excluded, since there's
    nothing to deduplicate against. The FIRST occurrence in the given order
    wins, so this respects whatever sort was already applied (e.g.
    most-recently-seen first)."""
    seen: set[str] = set()
    result: list[Recruiter] = []
    for r in recruiters:
        if not r.phone:
            continue
        key = normalize_phone_digits(r.phone) or r.phone.strip()
        if key in seen:
            continue
        seen.add(key)
        result.append(r)
    return result


@router.get("", response_model=list[RecruiterOut])
def list_recruiters(
    search: str | None = None,
    sort_by: str = "last_seen_at",
    order: str = "desc",
    unique_phone: bool = False,
    db: Session = Depends(get_db),
):
    """Section 6: search by recruiter name, email, company, or job title;
    sort by any of the columns in _SORTABLE_FIELDS. `unique_phone=true`
    collapses the result to one row per distinct mobile number (dropping
    recruiters with no phone recorded) - useful since the same person can
    legitimately appear as several recruiter records under different email
    addresses (section 7) but still share one real phone number."""
    query = db.query(Recruiter)

    if search:
        like = f"%{search}%"
        matching_recruiter_ids = (
            db.query(RecruiterRole.recruiter_id).filter(RecruiterRole.job_title.ilike(like)).scalar_subquery()
        )
        query = query.filter(
            or_(
                Recruiter.display_name.ilike(like),
                Recruiter.normalized_email.ilike(like),
                Recruiter.company.ilike(like),
                Recruiter.recruiter_role.ilike(like),
                Recruiter.id.in_(matching_recruiter_ids),
            )
        )

    sort_column = _SORTABLE_FIELDS.get(sort_by, Recruiter.last_seen_at)
    query = query.order_by(sort_column.desc() if order == "desc" else sort_column.asc())
    results = query.all()

    if unique_phone:
        results = _dedupe_by_phone(results)
    return results


@router.get("/top", response_model=list[RecruiterOut])
def top_recruiters(limit: int = 10, db: Session = Depends(get_db)):
    """Section 17 - "top" means activity volume (opportunities_count), never
    a quality/preference ranking."""
    return (
        db.query(Recruiter)
        .order_by(Recruiter.opportunities_count.desc())
        .limit(limit)
        .all()
    )


@router.get("/{recruiter_id}", response_model=RecruiterDetailOut)
def get_recruiter(recruiter_id: int, db: Session = Depends(get_db)):
    recruiter = db.query(Recruiter).filter(Recruiter.id == recruiter_id).first()
    if recruiter is None:
        raise HTTPException(404, "Recruiter not found")
    return recruiter


@router.get("/{recruiter_id}/opportunities", response_model=list[OpportunityOut])
def get_recruiter_opportunities(recruiter_id: int, db: Session = Depends(get_db), limit: int = 50):
    recruiter = db.query(Recruiter).filter(Recruiter.id == recruiter_id).first()
    if recruiter is None:
        raise HTTPException(404, "Recruiter not found")
    return (
        db.query(Opportunity)
        .filter(Opportunity.recruiter_id == recruiter_id)
        .order_by(Opportunity.created_at.desc())
        .limit(limit)
        .all()
    )


@router.patch("/{recruiter_id}", response_model=RecruiterOut)
def update_recruiter(recruiter_id: int, payload: RecruiterUpdate, db: Session = Depends(get_db)):
    """Lets the user attach free-form notes, or optionally link this
    recruiter record to another one believed to be the same person under a
    different email address (section 7) - this is always an explicit,
    user-driven action, never automatic."""
    recruiter = db.query(Recruiter).filter(Recruiter.id == recruiter_id).first()
    if recruiter is None:
        raise HTTPException(404, "Recruiter not found")

    if payload.linked_recruiter_id is not None:
        if payload.linked_recruiter_id == recruiter_id:
            raise HTTPException(400, "A recruiter cannot be linked to itself")
        target = db.query(Recruiter).filter(Recruiter.id == payload.linked_recruiter_id).first()
        if target is None:
            raise HTTPException(404, "Linked recruiter not found")
        recruiter.linked_recruiter_id = payload.linked_recruiter_id

    if payload.notes is not None:
        recruiter.notes = payload.notes

    db.add(recruiter)
    db.commit()
    db.refresh(recruiter)
    return recruiter


@router.delete("/{recruiter_id}")
def delete_recruiter(recruiter_id: int, db: Session = Depends(get_db)):
    """Section 18: delete a single recruiter (cascades to their role history
    and opportunity records)."""
    recruiter = db.query(Recruiter).filter(Recruiter.id == recruiter_id).first()
    if recruiter is None:
        raise HTTPException(404, "Recruiter not found")
    delete_recruiter_service(db, recruiter)
    return {"deleted": True}


@router.delete("")
def delete_all_recruiter_data(db: Session = Depends(get_db)):
    """Section 18: "Delete all stored recruiter data" - wipes every recruiter,
    their role history, and their opportunity records."""
    count = db.query(Recruiter).count()
    db.query(Recruiter).delete()
    db.commit()
    return {"deleted": count}
