"""
Deterministic resume match scoring + truthful, format-preserving customization.

By explicit configuration choice, this is never LLM-assisted (see the
app.services.resume_customizer module docstring) - `evaluate_and_customize`
must ignore any `ai_provider` it's given and always produce the deterministic
result. The core guarantee under test everywhere here: the customizer can
only ever surface skills the candidate's OWN resume metadata already claims -
it must never introduce a skill/technology/experience that isn't already
there.
"""
from __future__ import annotations

import docx

from app.services.resume_customizer import (
    customize_resume_file,
    evaluate_and_customize,
    find_underemphasized_overlap_skills,
)
from app.services.resume_matcher import ResumeScore


class _StubResume:
    def __init__(self, filename, file_path, extracted_text, skills):
        self.id = 1
        self.filename = filename
        self.file_path = file_path
        self.extracted_text = extracted_text
        self.extracted_metadata = {"skills": skills}


class _StubAI:
    """Instrumented only to prove it is NEVER called - resume customization
    must stay purely deterministic even when an ai_provider is passed in."""

    def __init__(self, result=None, raise_exc=None):
        self.result = result or {}
        self.raise_exc = raise_exc
        self.called = False

    def evaluate_and_customize_resume(self, resume_text, jd_title, jd_text, candidate_existing_skills):
        self.called = True
        if self.raise_exc:
            raise self.raise_exc
        return self.result


BASE_SCORE = ResumeScore(resume_id=1, filename="r.docx", score=42.0, explanation="baseline deterministic score")


# --- find_underemphasized_overlap_skills -----------------------------------


def test_finds_skills_candidate_has_but_didnt_write_into_prose():
    resume = _StubResume(
        "r.docx", "/tmp/r.docx", extracted_text="I am a data engineer with Python experience.",
        skills={"data_engineering_tools": ["airflow"], "languages": ["python"]},
    )
    result = find_underemphasized_overlap_skills(resume, jd_requirements=["airflow", "python", "kafka"])
    assert result == ["airflow"]  # python already in prose; kafka isn't a candidate skill at all


def test_never_returns_a_skill_the_candidate_does_not_actually_have():
    resume = _StubResume("r.docx", "/tmp/r.docx", extracted_text="", skills={"languages": ["python"]})
    result = find_underemphasized_overlap_skills(resume, jd_requirements=["kubernetes", "terraform"])
    assert result == []  # JD wants these, but candidate's own metadata never claims them


def test_returns_empty_when_everything_already_in_prose():
    resume = _StubResume(
        "r.docx", "/tmp/r.docx", extracted_text="Skilled in Databricks and SQL.",
        skills={"data_engineering_tools": ["databricks"], "languages": ["sql"]},
    )
    result = find_underemphasized_overlap_skills(resume, jd_requirements=["databricks", "sql"])
    assert result == []


def test_skill_buried_outside_summary_is_still_underemphasized():
    """Regression test for a real bug: metadata.skills is itself extracted
    by scanning the WHOLE resume text, so a skill in metadata is always
    present *somewhere* in the full text by construction - checking the
    whole text again (the old behavior) could never find anything
    "underemphasized" and this feature silently never fired for any real
    resume. It must instead check prominence within the Summary section
    specifically: a skill mentioned only in an old job's bullet point, but
    never in the Summary, is still a genuine gap worth surfacing."""
    resume = _StubResume(
        "r.docx", "/tmp/r.docx",
        extracted_text=(
            "Summary\n"
            "10+ years building cloud-native data pipelines with Python and SQL.\n"
            "Experience\n"
            "Data Engineer, Acme Corp\n"
            "Used Databricks to build a one-off migration pipeline in 2019.\n"
        ),
        skills={"data_engineering_tools": ["databricks"], "languages": ["python", "sql"]},
    )
    result = find_underemphasized_overlap_skills(resume, jd_requirements=["databricks", "python"])
    # databricks is genuinely true of the candidate (in metadata) and JD-relevant,
    # but never mentioned in the Summary - still underemphasized despite
    # appearing in an Experience bullet
    assert result == ["databricks"]
    # python IS in the Summary - correctly excluded
    assert "python" not in result


def test_skill_already_prominent_in_summary_is_not_returned():
    resume = _StubResume(
        "r.docx", "/tmp/r.docx",
        extracted_text=(
            "Summary\n"
            "Data engineer skilled in Databricks, Python, and SQL.\n"
            "Experience\n"
            "Built pipelines.\n"
        ),
        skills={"data_engineering_tools": ["databricks"], "languages": ["python"]},
    )
    result = find_underemphasized_overlap_skills(resume, jd_requirements=["databricks", "python"])
    assert result == []


def test_falls_back_to_whole_text_when_no_summary_section_is_identifiable():
    """A resume with no recognizable Summary heading at all keeps the old,
    whole-text-based behavior rather than treating everything as missing."""
    resume = _StubResume(
        "r.docx", "/tmp/r.docx", extracted_text="Just a plain resume mentioning Databricks somewhere.",
        skills={"data_engineering_tools": ["databricks"]},
    )
    result = find_underemphasized_overlap_skills(resume, jd_requirements=["databricks"])
    assert result == []  # already present in the (only) text available to check


# --- evaluate_and_customize (deterministic path, no AI) --------------------


def test_deterministic_path_used_when_no_ai_provider():
    resume = _StubResume(
        "r.docx", "/tmp/r.docx", extracted_text="Data engineer.",
        skills={"data_engineering_tools": ["airflow"]},
    )
    result = evaluate_and_customize(
        resume, jd_title="Data Engineer", jd_text="Requires Airflow", jd_requirements=["airflow"],
        base_score=BASE_SCORE, ai_provider=None,
    )
    assert result.source == "deterministic"
    assert result.match_score == BASE_SCORE.score
    assert "Airflow" in result.additional_points[0]


def test_deterministic_path_has_no_additional_points_when_nothing_missing():
    resume = _StubResume("r.docx", "/tmp/r.docx", extracted_text="Python developer.", skills={"languages": ["python"]})
    result = evaluate_and_customize(
        resume, jd_title="Dev", jd_text="Python role", jd_requirements=["python"],
        base_score=BASE_SCORE, ai_provider=None,
    )
    assert result.additional_points == []


# --- evaluate_and_customize never calls the AI provider --------------------


def test_ai_provider_is_never_consulted_even_when_configured():
    """By explicit configuration choice: resume customization is purely
    deterministic. Even when a (mis)configured ai_provider is passed in, it
    must never be called, and its output must never influence the result -
    not the score, not the explanation, not the additional points."""
    resume = _StubResume(
        "r.docx", "/tmp/r.docx", extracted_text="Data engineer.",
        skills={"data_engineering_tools": ["airflow"]},
    )
    ai = _StubAI({
        "match_score": 91.0, "match_explanation": "Strong airflow alignment",
        "additional_points": ["Hands-on experience orchestrating pipelines with Airflow"],
    })
    result = evaluate_and_customize(
        resume, jd_title="Data Engineer", jd_text="Requires Airflow", jd_requirements=["airflow"],
        base_score=BASE_SCORE, ai_provider=ai,
    )
    assert ai.called is False
    assert result.source == "deterministic"
    assert result.match_score == BASE_SCORE.score
    assert result.match_explanation == BASE_SCORE.explanation
    assert result.additional_points == ["Additional Relevant Skills: Airflow"]


def test_ai_provider_raising_is_irrelevant_since_it_is_never_called():
    resume = _StubResume(
        "r.docx", "/tmp/r.docx", extracted_text="Data engineer.",
        skills={"data_engineering_tools": ["airflow"]},
    )
    ai = _StubAI(raise_exc=ConnectionError("ollama down"))
    result = evaluate_and_customize(
        resume, jd_title="Data Engineer", jd_text="Requires Airflow", jd_requirements=["airflow"],
        base_score=BASE_SCORE, ai_provider=ai,
    )
    assert ai.called is False
    assert result.source == "deterministic"
    assert result.match_score == BASE_SCORE.score


# --- customize_resume_file (format-preserving DOCX only) -------------------


def _make_docx_resume(tmp_path, filename="resume.docx", paragraphs=None):
    document = docx.Document()
    for text in paragraphs or ["Diwakar Jilakara", "Senior Data Engineer"]:
        document.add_paragraph(text)
    path = tmp_path / filename
    document.save(str(path))
    return _StubResume(filename, str(path), extracted_text="\n".join(paragraphs or []), skills={})


def test_customize_resume_file_returns_none_when_no_additional_points(tmp_path):
    resume = _make_docx_resume(tmp_path)
    assert customize_resume_file(resume, [], dest_dir=str(tmp_path)) is None


def test_customize_resume_file_returns_none_for_pdf(tmp_path):
    resume = _StubResume("resume.pdf", str(tmp_path / "resume.pdf"), extracted_text="", skills={})
    assert customize_resume_file(resume, ["Additional Relevant Skills: Airflow"], dest_dir=str(tmp_path)) is None


def test_customize_resume_file_returns_none_for_doc(tmp_path):
    resume = _StubResume("resume.doc", str(tmp_path / "resume.doc"), extracted_text="", skills={})
    assert customize_resume_file(resume, ["Additional Relevant Skills: Airflow"], dest_dir=str(tmp_path)) is None


def test_customize_resume_file_creates_new_docx_without_touching_original(tmp_path):
    original_dir = tmp_path / "originals"
    original_dir.mkdir()
    dest_dir = tmp_path / "customized"
    resume = _make_docx_resume(original_dir, paragraphs=["Diwakar Jilakara", "Senior Data Engineer"])
    original_text_before = docx.Document(resume.file_path).paragraphs[0].text

    result = customize_resume_file(resume, ["Additional Relevant Skills: Airflow, Kafka"], dest_dir=str(dest_dir))

    assert result is not None
    new_path, display_filename = result
    assert new_path != resume.file_path
    assert display_filename == "Customized_resume.docx"

    # original untouched
    assert docx.Document(resume.file_path).paragraphs[0].text == original_text_before
    original_paragraph_count = len(docx.Document(resume.file_path).paragraphs)

    # new file has the original content PLUS the new section
    new_document = docx.Document(new_path)
    new_texts = [p.text for p in new_document.paragraphs]
    assert "Diwakar Jilakara" in new_texts
    assert "Additional Relevant Skills" in new_texts
    assert any("Airflow" in t for t in new_texts)
    assert len(new_document.paragraphs) > original_paragraph_count


# --- customize_resume_file: section-aware insertion (Experience/Summary) ---


def _make_structured_docx_resume(tmp_path, filename="resume.docx"):
    """A resume shaped like a real one: header, Summary (prose), Experience
    (bulleted), Education - so section-boundary detection has something
    realistic to work with."""
    document = docx.Document()
    document.add_paragraph("Diwakar Jilakara")
    document.add_paragraph("Senior Data Engineer")
    document.add_paragraph("Summary")
    document.add_paragraph("Experienced data engineer with a background in cloud data platforms.")
    document.add_paragraph("Experience")
    document.add_paragraph("Built and maintained ETL pipelines using Airflow.", style="List Bullet")
    document.add_paragraph("Optimized Spark jobs for large-scale processing.", style="List Bullet")
    document.add_paragraph("Education")
    document.add_paragraph("B.S. Computer Science")
    path = tmp_path / filename
    document.save(str(path))
    return _StubResume(filename, str(path), extracted_text="", skills={})


def test_customize_resume_file_inserts_points_into_experience_before_education(tmp_path):
    resume = _make_structured_docx_resume(tmp_path)
    result = customize_resume_file(
        resume, ["Implemented CI/CD pipelines using Jenkins and Terraform"], dest_dir=str(tmp_path / "out"),
    )
    assert result is not None
    new_path, _ = result
    new_texts = [p.text for p in docx.Document(new_path).paragraphs]

    # inserted, and landed BEFORE "Education" (i.e. still inside Experience),
    # not appended at the very end of the document
    assert any("Jenkins" in t for t in new_texts)
    experience_idx = new_texts.index("Experience")
    education_idx = new_texts.index("Education")
    jenkins_idx = next(i for i, t in enumerate(new_texts) if "Jenkins" in t)
    assert experience_idx < jenkins_idx < education_idx
    # no separate "Additional Relevant Skills" appendix was needed
    assert "Additional Relevant Skills" not in new_texts


def test_customize_resume_file_preserves_bullet_style_in_experience(tmp_path):
    resume = _make_structured_docx_resume(tmp_path)
    result = customize_resume_file(
        resume, ["Automated deployments with Terraform"], dest_dir=str(tmp_path / "out"),
    )
    new_path, _ = result
    new_document = docx.Document(new_path)
    inserted = next(p for p in new_document.paragraphs if "Terraform" in p.text)
    existing_bullet = next(p for p in new_document.paragraphs if "Airflow" in p.text)
    assert inserted.style.name == existing_bullet.style.name == "List Bullet"


def test_customize_resume_file_does_not_modify_any_existing_paragraph_text(tmp_path):
    resume = _make_structured_docx_resume(tmp_path)
    before_texts = [p.text for p in docx.Document(resume.file_path).paragraphs]

    result = customize_resume_file(resume, ["A new point"], dest_dir=str(tmp_path / "out"))
    new_path, _ = result
    new_texts = [p.text for p in docx.Document(new_path).paragraphs]

    # every original paragraph's text still appears, unmodified, in order
    filtered = [t for t in new_texts if t in before_texts]
    assert filtered == before_texts
    # and the original file on disk is completely untouched
    assert [p.text for p in docx.Document(resume.file_path).paragraphs] == before_texts


def test_customize_resume_file_falls_back_to_summary_when_no_experience_heading(tmp_path):
    document = docx.Document()
    document.add_paragraph("Diwakar Jilakara")
    document.add_paragraph("Summary")
    document.add_paragraph("Experienced engineer.")
    document.add_paragraph("Education")
    document.add_paragraph("B.S. Computer Science")
    path = tmp_path / "resume.docx"
    document.save(str(path))
    resume = _StubResume("resume.docx", str(path), extracted_text="", skills={})

    result = customize_resume_file(resume, ["Skilled in Kubernetes-based deployments"], dest_dir=str(tmp_path / "out"))
    new_texts = [p.text for p in docx.Document(result[0]).paragraphs]

    assert any("Kubernetes" in t for t in new_texts)
    summary_idx = new_texts.index("Summary")
    education_idx = new_texts.index("Education")
    kube_idx = next(i for i, t in enumerate(new_texts) if "Kubernetes" in t)
    assert summary_idx < kube_idx < education_idx
    assert "Additional Relevant Skills" not in new_texts


def test_customize_resume_file_falls_back_to_appendix_when_no_recognized_section(tmp_path):
    """A resume with no detectable Summary/Experience heading at all must
    still get the points added - just via the old safe appendix, not
    guessed insertion."""
    resume = _make_docx_resume(tmp_path, paragraphs=["Diwakar Jilakara", "Senior Data Engineer"])
    result = customize_resume_file(resume, ["Skilled in Kubernetes"], dest_dir=str(tmp_path / "out"))
    new_texts = [p.text for p in docx.Document(result[0]).paragraphs]
    assert "Additional Relevant Skills" in new_texts
    assert any("Kubernetes" in t for t in new_texts)


# --- post-save formatting-integrity check: every failure reverts to the ---
# --- original, unmodified resume, for every email                      ---


def test_is_subsequence_helper_accepts_original_plus_insertions():
    from app.services.resume_customizer import _is_subsequence_of_texts
    original = ["A", "B", "C"]
    new = ["A", "INSERTED", "B", "C", "INSERTED 2"]
    assert _is_subsequence_of_texts(original, new) is True


def test_is_subsequence_helper_rejects_modified_text():
    from app.services.resume_customizer import _is_subsequence_of_texts
    original = ["A", "B", "C"]
    new = ["A", "B (edited)", "C"]  # "B" itself was changed, not just added-around
    assert _is_subsequence_of_texts(original, new) is False


def test_is_subsequence_helper_rejects_reordered_text():
    from app.services.resume_customizer import _is_subsequence_of_texts
    original = ["A", "B", "C"]
    new = ["A", "C", "B"]  # same texts, wrong order
    assert _is_subsequence_of_texts(original, new) is False


def test_is_subsequence_helper_rejects_dropped_paragraph():
    from app.services.resume_customizer import _is_subsequence_of_texts
    original = ["A", "B", "C"]
    new = ["A", "C"]  # "B" went missing
    assert _is_subsequence_of_texts(original, new) is False


def test_customization_is_format_safe_catches_a_content_losing_save(tmp_path):
    """Direct unit test of the integrity check: a saved file with LESS
    content than the original (simulating a corrupted/lossy save) must be
    rejected."""
    from app.services.resume_customizer import _customization_is_format_safe

    original = docx.Document()
    original.add_paragraph("Diwakar Jilakara")
    original.add_paragraph("Senior Data Engineer")
    original_path = tmp_path / "original.docx"
    original.save(str(original_path))

    broken = docx.Document()
    broken.add_paragraph("Only this survived")  # original content lost
    broken_path = tmp_path / "broken.docx"
    broken.save(str(broken_path))

    assert _customization_is_format_safe(docx.Document(str(original_path)), str(broken_path)) is False


def test_customize_resume_file_reverts_to_original_and_cleans_up_when_integrity_check_fails(tmp_path, monkeypatch):
    """Integration behavior: when the post-save integrity check fails, the
    customization must be discarded entirely (return None -> caller attaches
    the plain original resume) and the bad file must not be left behind."""
    import app.services.resume_customizer as resume_customizer_module

    resume = _make_structured_docx_resume(tmp_path)
    out_dir = tmp_path / "out"

    monkeypatch.setattr(resume_customizer_module, "_customization_is_format_safe", lambda *a, **k: False)

    result = customize_resume_file(resume, ["Skilled in Kubernetes"], dest_dir=str(out_dir))
    assert result is None
    assert list(out_dir.glob("*.docx")) == []  # the rejected file was cleaned up, not left behind


def test_customize_resume_file_reverts_to_original_when_document_open_raises(tmp_path, monkeypatch):
    """A resume file that can't even be opened must fail safe (None), not
    raise out of the pipeline."""
    import app.services.resume_customizer as resume_customizer_module

    resume = _make_structured_docx_resume(tmp_path)

    def _raise(*args, **kwargs):
        raise ValueError("simulated corrupt docx")

    monkeypatch.setattr(resume_customizer_module.docx, "Document", _raise)

    result = customize_resume_file(resume, ["Skilled in Kubernetes"], dest_dir=str(tmp_path / "out"))
    assert result is None


def test_customize_resume_file_keeps_a_genuinely_clean_edit(tmp_path):
    """Sanity check: the integrity check doesn't reject normal, correct
    edits - only actually-broken ones."""
    resume = _make_structured_docx_resume(tmp_path)
    result = customize_resume_file(resume, ["Skilled in Kubernetes-based deployments"], dest_dir=str(tmp_path / "out"))
    assert result is not None
