"""
End-to-end integration between the pipeline and resume customization: DOCX
resumes get a customized copy attached when there's a truthful gap to fill;
PDFs never get rewritten. By explicit configuration choice, customization is
purely deterministic - an `ai_provider`, even if configured/passed through,
is never consulted for it (see app/services/resume_customizer.py).
"""
from __future__ import annotations

import docx

from app.ai.base import AIProvider
from app.models import ProcessingStatus, Resume
from app.services.pipeline import process_message
from tests.conftest import make_raw_message

SUBJECT = "Role: Senior Data Engineer Location: Remote"
BODY = """James

---------- Forwarded message ----------
From: Naveen Gangupamu <naveen.gangupamu@pamten.com>

Role: Senior Data Engineer
Location: Remote

Required Skills:
- Airflow
- Python
- SQL

All interviews will be conducted via Zoom.

Thanks,
Naveen
Talent Acquisition Executive
PAMTEN
"""


class _StubAI(AIProvider):
    """Only implements what these tests exercise; other methods delegate to
    plain deterministic-friendly stand-ins since they're never reached with
    a strong deterministic match on this fixture. `evaluate_and_customize_resume`
    is instrumented specifically to PROVE it is never called - resume
    customization must stay purely deterministic even when an AI provider is
    configured (see test_ai_provider_is_never_consulted_for_resume_customization
    below)."""

    def __init__(self, customize_result=None, customize_raises=None):
        self.customize_result = customize_result or {}
        self.customize_raises = customize_raises
        self.evaluate_and_customize_resume_called = False

    def classify_job_email(self, subject, body):
        raise NotImplementedError

    def extract_job_details(self, text):
        raise NotImplementedError

    def polish_email_body(self, draft_body, constraints):
        return draft_body

    def classify_interview_requirement(self, text):
        raise NotImplementedError

    def evaluate_and_customize_resume(self, resume_text, jd_title, jd_text, candidate_existing_skills):
        self.evaluate_and_customize_resume_called = True
        if self.customize_raises:
            raise self.customize_raises
        return self.customize_result


def _make_docx_resume(db_session, filename="resume.docx", tmp_path=None, paragraphs=None):
    document = docx.Document()
    for text in paragraphs or ["Diwakar Jilakara", "Data Engineer with Python experience."]:
        document.add_paragraph(text)
    path = tmp_path / filename
    document.save(str(path))
    resume = Resume(
        filename=filename, file_path=str(path),
        extracted_text="\n".join(paragraphs or []),
        extracted_metadata={"skills": {"data_engineering_tools": ["airflow"], "languages": ["python"]},
                             "years_of_experience": 6, "job_titles": ["Data Engineer"]},
        indexing_status="INDEXED",
    )
    db_session.add(resume)
    db_session.commit()
    db_session.refresh(resume)
    return resume


def test_docx_resume_gets_customized_and_attached_with_deterministic_points(
    db_session, settings, candidate_profile, fake_gmail_client, tmp_path
):
    _make_docx_resume(db_session, tmp_path=tmp_path)
    raw = make_raw_message(
        message_id="cust-1", thread_id="cust-thread-1",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    application = result.application
    assert application.customized_resume_path is not None
    assert application.match_score is not None
    assert application.has_customized_resume is True

    draft_call = fake_gmail_client.created_drafts[0]
    assert draft_call["attachment_path"] == application.customized_resume_path
    assert draft_call["attachment_path"] != application.selected_resume.file_path

    # the customized file actually contains the new point; the original
    # library resume file is untouched
    customized_text = "\n".join(p.text for p in docx.Document(application.customized_resume_path).paragraphs)
    assert "Airflow" in customized_text
    original_text = "\n".join(p.text for p in docx.Document(application.selected_resume.file_path).paragraphs)
    assert "Additional Relevant Skills" not in original_text


def test_pdf_resume_is_never_rewritten(
    db_session, settings, candidate_profile, fake_gmail_client, tmp_path
):
    from tests.conftest import make_resume

    make_resume(
        db_session, "resume.pdf",
        skills={"data_engineering_tools": ["airflow"], "languages": ["python"]},
        years=6, titles=["Data Engineer"],
    )
    raw = make_raw_message(
        message_id="cust-2", thread_id="cust-thread-2",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    application = result.application
    assert application.customized_resume_path is None
    assert application.has_customized_resume is False
    draft_call = fake_gmail_client.created_drafts[0]
    assert draft_call["attachment_path"] == application.selected_resume.file_path


def test_ai_provider_is_never_consulted_for_resume_customization(
    db_session, settings, candidate_profile, fake_gmail_client, tmp_path
):
    """By explicit configuration choice: even when an AI provider IS
    configured/passed through the pipeline, resume customization must never
    call it - the deterministic result is used unconditionally."""
    _make_docx_resume(db_session, tmp_path=tmp_path)
    ai = _StubAI(customize_result={
        "match_score": 999.0, "match_explanation": "COMPLETELY FABRICATED BY THE AI STUB",
        "additional_points": ["THIS LLM-WRITTEN TEXT MUST NEVER APPEAR ANYWHERE"],
    })
    raw = make_raw_message(
        message_id="cust-3", thread_id="cust-thread-3",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=ai)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    application = result.application
    assert ai.evaluate_and_customize_resume_called is False
    assert application.match_score != 999.0
    assert application.match_explanation != "COMPLETELY FABRICATED BY THE AI STUB"
    customized_text = "\n".join(p.text for p in docx.Document(application.customized_resume_path).paragraphs)
    assert "THIS LLM-WRITTEN TEXT MUST NEVER APPEAR ANYWHERE" not in customized_text
    assert "Additional Relevant Skills" in customized_text


def test_no_ai_provider_still_creates_deterministic_customized_resume(
    db_session, settings, candidate_profile, fake_gmail_client, tmp_path
):
    """AI_PROVIDER=none must still work end-to-end - the deterministic
    "Additional Relevant Skills" section is used directly."""
    _make_docx_resume(db_session, tmp_path=tmp_path)
    raw = make_raw_message(
        message_id="cust-4", thread_id="cust-thread-4",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=None)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    application = result.application
    assert application.customized_resume_path is not None
    customized_text = "\n".join(p.text for p in docx.Document(application.customized_resume_path).paragraphs)
    assert "Airflow" in customized_text
