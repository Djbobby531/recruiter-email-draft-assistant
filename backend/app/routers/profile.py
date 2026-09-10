from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CandidateProfile
from app.schemas import CandidateProfileIn, CandidateProfileOut

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=CandidateProfileOut | None)
def get_profile(db: Session = Depends(get_db)):
    return db.query(CandidateProfile).first()


@router.put("", response_model=CandidateProfileOut)
def upsert_profile(payload: CandidateProfileIn, db: Session = Depends(get_db)):
    profile = db.query(CandidateProfile).first()
    if profile is None:
        profile = CandidateProfile(**payload.model_dump())
    else:
        for k, v in payload.model_dump().items():
            setattr(profile, k, v)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile
