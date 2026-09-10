from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Resume
from app.schemas import ResumeOut
from app.services import resume_service

router = APIRouter(prefix="/api/resumes", tags=["resumes"])

ALLOWED_EXTENSIONS = (".pdf", ".docx", ".doc")


def _has_allowed_extension(filename: str | None) -> bool:
    return filename is not None and filename.lower().endswith(ALLOWED_EXTENSIONS)


@router.get("", response_model=list[ResumeOut])
def list_resumes(db: Session = Depends(get_db)):
    return db.query(Resume).order_by(Resume.created_at.desc()).all()


@router.post("", response_model=ResumeOut)
async def upload_resume(
    file: UploadFile, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
):
    if not _has_allowed_extension(file.filename):
        raise HTTPException(400, "Only PDF, DOC, and DOCX resumes are supported")
    assert file.filename is not None  # narrowed by _has_allowed_extension above
    file_bytes = await file.read()
    resume = resume_service.save_and_index_resume(db, settings.RESUME_DIR, file.filename, file_bytes)
    return resume


@router.post("/{resume_id}/replace", response_model=ResumeOut)
async def replace_resume(resume_id: int, file: UploadFile, db: Session = Depends(get_db)):
    resume = db.query(Resume).filter(Resume.id == resume_id).first()
    if resume is None:
        raise HTTPException(404, "Resume not found")
    if not _has_allowed_extension(file.filename):
        raise HTTPException(400, "Only PDF, DOC, and DOCX resumes are supported")
    assert file.filename is not None  # narrowed by _has_allowed_extension above
    file_bytes = await file.read()
    return resume_service.replace_resume(db, resume, file.filename, file_bytes)


@router.delete("/{resume_id}")
def delete_resume(resume_id: int, db: Session = Depends(get_db)):
    resume = db.query(Resume).filter(Resume.id == resume_id).first()
    if resume is None:
        raise HTTPException(404, "Resume not found")
    resume_service.delete_resume(db, resume)
    return {"deleted": True}


@router.get("/{resume_id}/file")
def download_resume(resume_id: int, db: Session = Depends(get_db)):
    resume = db.query(Resume).filter(Resume.id == resume_id).first()
    if resume is None:
        raise HTTPException(404, "Resume not found")
    return FileResponse(resume.file_path, filename=resume.filename)
