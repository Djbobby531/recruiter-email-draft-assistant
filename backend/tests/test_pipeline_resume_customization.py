"""
End-to-end integration between the pipeline and resume customization: DOCX
resumes get a customized copy attached when there's a truthful gap to fill;
PDFs never get rewritten. Resume content customization tries the enhanced
LLM-assisted path first (header role rewrite, summary lines, verified skills
placed into their existing category, experience bullets - see
app/services/resume_customizer.py:generate_llm_customized_resume), falling
back to the plain deterministic "Additional Relevant Skills" customization
whenever the LLM path is unavailable, disabled, or produces anything that
doesn't survive strict validation.
"""
from __future__ import annotations

import docx

from app.ai.base import AIProvider
from app.models import ProcessingStatus, Resume
from app.services.pipeline import process_message
from tests.conftest import make_raw_message

SUBJECT = "Role: Senior AWS Data Engineer Location: Remote"
BODY = """James

---------- Forwarded message ----------
From: Naveen Gangupamu <naveen.gangupamu@pamten.com>

Role: Senior AWS Data Engineer
Location: Remote

Required Skills:
- AWS
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
    plain deterministic-friendly stand-ins since they're never reached with a
    strong deterministic match on this fixture."""

    def __init__(self, plan=None, raises=None, skills_pitch=None):
        self.plan = plan or {}
        self.raises = raises
        self.skills_pitch = skills_pitch
        self.called = False

    def classify_job_email(self, subject, body):
        raise NotImplementedError

    def extract_job_details(self, text):
        raise NotImplementedError

    def polish_email_body(self, draft_body, constraints):
        return draft_body

    def classify_interview_requirement(self, text):
        raise NotImplementedError

    def generate_resume_customization_plan(
        self, resume_text, resume_structure, jd_title, jd_text, jd_requirements,
        approved_skills, approved_experience_identifiers,
    ):
        self.called = True
        if self.raises:
            raise self.raises
        return self.plan

    def generate_email_skills_pitch(self, job_title, jd_text, top_skills, candidate_experience):
        if self.skills_pitch is None:
            raise NotImplementedError
        return self.skills_pitch


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


def _make_full_docx_resume(db_session, tmp_path, filename="full_resume.docx"):
    """Shaped like a real resume, matching the acceptance-example structure:
    header role, Skills categories, and one Experience block with bullets."""
    document = docx.Document()
    document.add_paragraph("Diwakar Jilakara")
    document.add_paragraph("Senior Data Engineer")
    document.add_paragraph("Skills")
    document.add_paragraph("Cloud: AWS, Azure")
    document.add_paragraph("Programming Languages: Python, Java")
    document.add_paragraph("Experience")
    document.add_paragraph("Company A")
    document.add_paragraph("Built and maintained ETL pipelines.", style="List Bullet")
    document.add_paragraph("Optimized Spark jobs for large-scale processing.", style="List Bullet")
    document.add_paragraph("Education")
    document.add_paragraph("B.S. Computer Science")
    path = tmp_path / filename
    document.save(str(path))
    resume = Resume(
        filename=filename, file_path=str(path),
        # deliberately doesn't mention "airflow" anywhere, so the
        # deterministic fallback path (find_underemphasized_overlap_skills)
        # has a genuine truthful gap to surface in tests that need it
        extracted_text="Cloud: AWS, Azure\nPython, Java pipelines built at Company A.",
        extracted_metadata={
            "skills": {
                "cloud_platforms": ["aws", "azure"], "languages": ["python", "java"],
                "data_engineering_tools": ["airflow", "spark"],
            },
            "years_of_experience": 8, "job_titles": ["Senior Data Engineer"],
        },
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
    assert application.customization_source == "deterministic"

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
    assert application.customization_source is None
    draft_call = fake_gmail_client.created_drafts[0]
    assert draft_call["attachment_path"] == application.selected_resume.file_path


def test_llm_plan_customizes_header_skills_and_experience_end_to_end(
    db_session, settings, candidate_profile, fake_gmail_client, tmp_path
):
    _make_full_docx_resume(db_session, tmp_path)
    ai = _StubAI(plan={
        "jd_role": "Senior AWS Data Engineer",
        "header_role": "Senior AWS Data Engineer",
        "summary_points": [],
        "skills_to_add": [{"skill": "airflow", "category": "Cloud", "reason": "verified, JD-relevant"}],
        "experience_updates": [{
            "experience_identifier": "Company A",
            "points": ["Automated pipeline scheduling with Airflow across AWS workloads."],
        }],
    })
    raw = make_raw_message(
        message_id="cust-5", thread_id="cust-thread-5",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=ai)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    application = result.application
    assert ai.called is True
    assert application.customized_resume_path is not None
    assert application.customization_source == "llm"

    new_document = docx.Document(application.customized_resume_path)
    texts = [p.text for p in new_document.paragraphs]
    assert "Senior AWS Data Engineer" in texts  # header replaced
    assert "Senior Data Engineer" not in texts  # old header role gone
    assert any(t.startswith("Cloud:") and "Airflow" in t for t in texts)  # skill surfaced into Cloud
    assert any("Automated pipeline scheduling with Airflow" in t for t in texts)  # experience bullet added

    # display filename is always Diwakar_Resume.docx now, regardless of role
    filename = application.customized_resume_path.split("/")[-1]
    assert filename.endswith("Diwakar_Resume.docx")
    assert fake_gmail_client.created_drafts[0]["attachment_path"] == application.customized_resume_path

    # original library file is completely untouched
    original_texts = [p.text for p in docx.Document(application.selected_resume.file_path).paragraphs]
    assert "Senior Data Engineer" in original_texts
    assert "Senior AWS Data Engineer" not in original_texts


def test_llm_plan_with_unverified_skill_falls_back_to_deterministic(
    db_session, settings, candidate_profile, fake_gmail_client, tmp_path
):
    """The AI claims "Terraform" - not in this candidate's verified skills at
    all - alongside a legitimate header change. The unverified skill must be
    dropped; since nothing else in the plan survives validation either, the
    whole LLM path yields nothing and the pipeline falls back to the plain
    deterministic customization."""
    _make_full_docx_resume(db_session, tmp_path)
    ai = _StubAI(plan={
        "jd_role": "Senior AWS Data Engineer", "header_role": "Senior Data Engineer",  # same as current -> no-op
        "summary_points": [], "skills_to_add": [{"skill": "Terraform", "category": "DevOps", "reason": "x"}],
        "experience_updates": [],
    })
    raw = make_raw_message(
        message_id="cust-6", thread_id="cust-thread-6",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=ai)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    application = result.application
    assert ai.called is True
    assert application.customization_source == "deterministic"
    customized_text = "\n".join(p.text for p in docx.Document(application.customized_resume_path).paragraphs)
    assert "Terraform" not in customized_text
    # deterministic fallback still ran and surfaced the truthful gap (airflow)
    assert "Additional Relevant Skills" in customized_text or "Airflow" in customized_text


def test_llm_provider_failure_falls_back_to_deterministic_customization(
    db_session, settings, candidate_profile, fake_gmail_client, tmp_path
):
    _make_full_docx_resume(db_session, tmp_path)
    ai = _StubAI(raises=ConnectionError("ollama unreachable"))
    raw = make_raw_message(
        message_id="cust-7", thread_id="cust-thread-7",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=ai)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert result.application.match_score is not None
    assert result.application.customized_resume_path is not None  # deterministic path still produced a file
    assert result.application.customization_source == "deterministic"


def test_llm_customization_disabled_by_config_uses_deterministic_path(
    db_session, settings, candidate_profile, fake_gmail_client, tmp_path
):
    settings.RESUME_LLM_CUSTOMIZATION_ENABLED = False
    _make_full_docx_resume(db_session, tmp_path)
    ai = _StubAI(plan={
        "jd_role": "Senior AWS Data Engineer", "header_role": "Senior AWS Data Engineer",
        "summary_points": [], "skills_to_add": [], "experience_updates": [],
    })
    raw = make_raw_message(
        message_id="cust-8", thread_id="cust-thread-8",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=ai)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    assert ai.called is False
    assert result.application.customization_source == "deterministic"
    texts = [p.text for p in docx.Document(result.application.customized_resume_path).paragraphs]
    assert "Senior AWS Data Engineer" not in texts  # LLM path never ran, header untouched


def _make_table_based_docx_resume(db_session, tmp_path, filename="table_resume.docx"):
    """Shaped like the real resume that was reproducibly falling back to the
    deterministic path in production: header AND skills both live in
    TABLES (not plain body paragraphs) - a very common real-world layout.
    Regression coverage for the bug where a skill landing in a table cell
    was applied but never tracked for the post-save validation, so the
    entire LLM customization was silently discarded every time."""
    document = docx.Document()
    header_table = document.add_table(rows=1, cols=1)
    header_table.rows[0].cells[0].paragraphs[0].text = "Senior Azure Data Engineer"
    document.add_paragraph("Experience")
    document.add_paragraph("Company A")
    document.add_paragraph("Built and maintained ETL pipelines.", style="List Bullet")
    skills_table = document.add_table(rows=1, cols=2)
    skills_table.rows[0].cells[0].paragraphs[0].text = "Languages"
    skills_table.rows[0].cells[1].paragraphs[0].text = "Python, Java"
    path = tmp_path / filename
    document.save(str(path))
    resume = Resume(
        filename=filename, file_path=str(path),
        extracted_text="Python, Java pipelines built at Company A.",
        extracted_metadata={
            "skills": {"languages": ["python", "java", "go"]},
            "years_of_experience": 8, "job_titles": ["Senior Azure Data Engineer"],
        },
        indexing_status="INDEXED",
    )
    db_session.add(resume)
    db_session.commit()
    db_session.refresh(resume)
    return resume


def test_llm_plan_skill_in_table_cell_survives_the_real_pipeline(
    db_session, settings, candidate_profile, fake_gmail_client, tmp_path
):
    """The real production regression, exercised through the actual
    `process_message` pipeline (the same function both the manual-draft
    endpoint and the Gmail poller call) rather than only the resume
    customizer's own unit tests: a table-based header + table-based skills
    resume must come out the other end with the LLM customization applied
    and attached - not silently discarded and fallen back to deterministic."""
    _make_table_based_docx_resume(db_session, tmp_path)
    ai = _StubAI(plan={
        "jd_role": "Senior GoLang Data Engineer", "header_role": "Senior GoLang Data Engineer",
        "summary_points": [],
        "skills_to_add": [{"skill": "go", "category": "Languages", "reason": "verified, JD-relevant"}],
        "experience_updates": [],
    })
    raw = make_raw_message(
        message_id="cust-9", thread_id="cust-thread-9",
        from_header="James AlgebraIT <james@algebrait.com>", subject=SUBJECT, plain_body=BODY,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=ai)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    application = result.application
    assert ai.called is True
    assert application.customization_source == "llm"
    filename = application.customized_resume_path.split("/")[-1]
    assert filename.endswith("Diwakar_Resume.docx")

    new_document = docx.Document(application.customized_resume_path)
    assert new_document.tables[0].rows[0].cells[0].text == "Senior GoLang Data Engineer"
    assert new_document.tables[1].rows[0].cells[1].text == "Python, Java, GO"
    assert fake_gmail_client.created_drafts[0]["attachment_path"] == application.customized_resume_path


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
    assert application.customization_source == "deterministic"
    customized_text = "\n".join(p.text for p in docx.Document(application.customized_resume_path).paragraphs)
    assert "Airflow" in customized_text


def test_symbol_laden_title_and_tailored_skills_pitch_flow_through_the_real_pipeline(
    db_session, settings, candidate_profile, fake_gmail_client, tmp_path
):
    """Combines both fixes end-to-end through the real `process_message`:
    (1) a JD title carrying decorative junk (#, emoji) must come out clean
    in the resume header, the customized filename, AND the email
    subject/body - not just wherever it happened to be tested in isolation;
    (2) a valid AI-generated skills pitch must replace the generic
    "expertise in X, Y, Z" list in the email body."""
    _make_full_docx_resume(db_session, tmp_path)
    ai = _StubAI(
        plan={
            "jd_role": "Senior AWS Data Engineer", "header_role": "Senior AWS Data Engineer",
            "summary_points": [], "skills_to_add": [], "experience_updates": [],
        },
        skills_pitch="My hands-on AWS and Python background lines up well with what this role calls for.",
    )
    subject = "Role: \U0001F6A8 Senior AWS Data Engineer #1 Location: Remote"
    raw = make_raw_message(
        message_id="cust-10", thread_id="cust-thread-10",
        from_header="James AlgebraIT <james@algebrait.com>", subject=subject, plain_body=BODY,
    )

    result = process_message(db_session, settings, raw, fake_gmail_client, ai_provider=ai)

    assert result.status == ProcessingStatus.DRAFT_CREATED
    application = result.application

    # (1) symbol-laden title cleaned everywhere it's used
    assert application.job_title == "Senior AWS Data Engineer 1"
    assert "#" not in application.job_title
    assert "\U0001F6A8" not in application.job_title
    assert "#" not in application.generated_subject
    assert "\U0001F6A8" not in application.generated_subject
    new_document = docx.Document(application.customized_resume_path)
    header_texts = [p.text for p in new_document.paragraphs]
    assert any("#" in t or "\U0001F6A8" in t for t in header_texts) is False

    # (2) tailored skills pitch used in the email body, not the generic list
    assert "My hands-on AWS and Python background lines up well" in application.generated_body
    assert "with strong expertise in" not in application.generated_body
