"""
FastAPI TestClient coverage for uploading DOCX/DOC resumes (not just PDF)
through the real `/api/resumes` endpoints.
"""
from __future__ import annotations

import io

import docx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    test_settings = Settings(RESUME_DIR=str(tmp_path / "resumes"))
    app.dependency_overrides[get_settings] = lambda: test_settings

    async def _noop_polling_loop(stop_event):
        await stop_event.wait()

    monkeypatch.setattr(main_module, "polling_loop", _noop_polling_loop)

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def _docx_bytes(paragraphs):
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return buffer.read()


def test_upload_docx_resume_is_indexed(client):
    content = _docx_bytes(["Diwakar Jilakara", "Senior Data Engineer", "Python SQL Databricks Terraform"])
    resp = client.post(
        "/api/resumes",
        files={"file": (
            "resume.docx", io.BytesIO(content),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "resume.docx"
    assert data["indexing_status"] == "INDEXED"
    flat_skills = [s for group in data["extracted_metadata"]["skills"].values() for s in group]
    assert "python" in flat_skills
    assert "databricks" in flat_skills


def test_upload_doc_resume_is_accepted(client):
    blob = b"\x00\x01Python SQL Databricks\x00\xff\xfeSenior Data Engineer\x00"
    resp = client.post(
        "/api/resumes",
        files={"file": ("resume.doc", io.BytesIO(blob), "application/msword")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "resume.doc"
    assert data["indexing_status"] in ("INDEXED", "INDEXED_EMPTY_TEXT")


def test_upload_still_rejects_truly_unsupported_extensions(client):
    resp = client.post(
        "/api/resumes",
        files={"file": ("resume.txt", io.BytesIO(b"plain text resume"), "text/plain")},
    )
    assert resp.status_code == 400


def test_replace_pdf_resume_with_docx(client):
    minimal_pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/Resources<<>>/MediaBox[0 0 300 144]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF"
    )
    create_resp = client.post(
        "/api/resumes", files={"file": ("original.pdf", io.BytesIO(minimal_pdf), "application/pdf")}
    )
    resume_id = create_resp.json()["id"]

    docx_content = _docx_bytes(["Updated resume", "AWS Python SQL"])
    replace_resp = client.post(
        f"/api/resumes/{resume_id}/replace",
        files={"file": (
            "updated.docx", io.BytesIO(docx_content),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )},
    )
    assert replace_resp.status_code == 200
    data = replace_resp.json()
    assert data["filename"] == "updated.docx"
    assert data["indexing_status"] == "INDEXED"
