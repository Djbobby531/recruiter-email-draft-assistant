"""
Resume library management: upload, parse-once, store, delete, replace.
Resumes are parsed/indexed exactly once at upload time (RULE H) - never
re-parsed on every incoming email.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Resume
from app.utils.skills_taxonomy import (
    extract_job_titles,
    extract_skills_by_category,
    extract_years_of_experience,
)
from app.utils.text_extraction import extract_resume_text


def _build_metadata(text: str) -> dict:
    return {
        "skills": extract_skills_by_category(text),
        "years_of_experience": extract_years_of_experience(text),
        "job_titles": extract_job_titles(text),
    }


def save_and_index_resume(db: Session, resume_dir: str, filename: str, file_bytes: bytes) -> Resume:
    Path(resume_dir).mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(filename).name}"
    dest_path = Path(resume_dir) / safe_name
    dest_path.write_bytes(file_bytes)

    resume = Resume(
        filename=filename,
        file_path=str(dest_path),
        extracted_text="",
        extracted_metadata={},
        indexing_status="PENDING",
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    try:
        text = extract_resume_text(dest_path, filename)
        metadata = _build_metadata(text)
        resume.extracted_text = text
        resume.extracted_metadata = metadata
        resume.indexing_status = "INDEXED" if text.strip() else "INDEXED_EMPTY_TEXT"
    except Exception as exc:
        resume.indexing_status = "FAILED"
        resume.extracted_metadata = {"error": str(exc)}

    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def replace_resume(db: Session, resume: Resume, filename: str, file_bytes: bytes) -> Resume:
    old_path = Path(resume.file_path)
    resume_dir = str(old_path.parent)
    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(filename).name}"
    dest_path = Path(resume_dir) / safe_name
    dest_path.write_bytes(file_bytes)

    if old_path.exists():
        old_path.unlink()

    resume.filename = filename
    resume.file_path = str(dest_path)
    resume.indexing_status = "PENDING"
    db.add(resume)
    db.commit()

    try:
        text = extract_resume_text(dest_path, filename)
        metadata = _build_metadata(text)
        resume.extracted_text = text
        resume.extracted_metadata = metadata
        resume.indexing_status = "INDEXED" if text.strip() else "INDEXED_EMPTY_TEXT"
    except Exception as exc:
        resume.indexing_status = "FAILED"
        resume.extracted_metadata = {"error": str(exc)}

    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def delete_resume(db: Session, resume: Resume) -> None:
    path = Path(resume.file_path)
    if path.exists():
        path.unlink()
    db.delete(resume)
    db.commit()
