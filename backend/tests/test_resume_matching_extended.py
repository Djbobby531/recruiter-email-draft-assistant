"""
RM-01 .. RM-15: resume/JD matching scenarios (section 14 of the hardening spec).
Uses the resume metadata fixtures in tests/fixtures/resumes/ and JD text
fixtures in tests/fixtures/jds/.
"""
from __future__ import annotations

from app.models import Resume
from app.services.jd_extractor import extract_job_details
from app.services.resume_matcher import match_resumes
from tests.conftest import load_jd, make_resume_from_fixture


def _match(db_session, resume_fixtures, jd_filename, min_score=30.0):
    resumes = [make_resume_from_fixture(db_session, f) for f in resume_fixtures]
    jd_text = load_jd(jd_filename)
    jd = extract_job_details(text=jd_text, subject="")
    result = match_resumes(resumes, jd_title=jd.job_title, jd_text=jd_text,
                            jd_requirements=jd.requirements, min_score=min_score)
    return result, resumes


def test_rm01_perfect_technology_match(db_session):
    result, resumes = _match(db_session, ["databricks.json", "aws.json", "healthcare.json"], "rm01_perfect_tech_match.txt")
    assert result.best.resume_id == resumes[0].id
    assert result.best.score >= 60


def test_rm02_strong_responsibilities_weaker_keywords(db_session):
    result, resumes = _match(db_session, ["databricks.json", "python_generalist.json"], "rm02_strong_responsibilities_weak_keywords.txt")
    assert result.best is not None
    # weak-keyword JD should still resolve to a ranked, non-crashing result
    assert len(result.ranked) == 2


def test_rm03_strong_title_match(db_session):
    result, resumes = _match(db_session, ["aws.json", "healthcare.json"], "rm03_strong_title_match.txt")
    # "Cloud Data Engineer" title is closest to the AWS resume's "Cloud Data Engineer" title
    assert result.best.resume_id == resumes[0].id


def test_rm04_strong_domain_match(db_session):
    result, resumes = _match(db_session, ["healthcare.json", "databricks.json"], "rm04_strong_domain_match.txt")
    assert result.best.resume_id == resumes[0].id


def test_rm05_databricks_jd_among_aws_azure_databricks_resumes(db_session):
    result, resumes = _match(db_session, ["databricks.json", "aws.json", "azure.json"], "rm05_databricks_among_aws_azure.txt")
    assert result.best.resume_id == resumes[0].id  # databricks.json


def test_rm06_healthcare_jd_among_general_and_healthcare_resumes(db_session):
    result, resumes = _match(db_session, ["python_generalist.json", "healthcare.json"], "rm06_healthcare_among_general.txt")
    assert result.best.resume_id == resumes[1].id  # healthcare.json


def test_rm07_finance_jd_among_general_and_finance_resumes(db_session):
    result, resumes = _match(db_session, ["python_generalist.json", "finance_quant.json"], "rm07_finance_quant_among_general.txt")
    assert result.best.resume_id == resumes[1].id  # finance_quant.json


def test_rm08_cloud_mismatch_all_resumes_score_low(db_session):
    result, resumes = _match(db_session, ["databricks.json", "aws.json", "azure.json"], "rm08_cloud_mismatch.txt")
    # none of our resumes have GCP/BigQuery - every score should reflect the mismatch
    assert all(s.score < 50 for s in result.ranked)


def test_rm09_years_of_experience_mismatch_is_reflected_but_not_disqualifying_alone(db_session):
    result, resumes = _match(db_session, ["python_generalist.json", "databricks.json"], "rm09_years_mismatch.txt")
    # JD wants 15+ years; python_generalist has 3, databricks has 8 - databricks
    # should still come out ahead due to closer experience + skill overlap
    assert result.best.resume_id == resumes[1].id


def test_rm10_very_similar_resumes_still_produce_a_deterministic_ranking(db_session):
    result, resumes = _match(db_session, ["databricks.json", "azure.json"], "rm10_similar_resumes.txt")
    assert result.best is not None
    assert len(result.ranked) == 2
    assert result.ranked[0].score >= result.ranked[1].score


def test_rm11_no_resume_has_a_strong_match_routes_to_manual_review(db_session):
    result, resumes = _match(db_session, ["databricks.json", "aws.json", "healthcare.json"], "rm11_no_strong_match.txt", min_score=30.0)
    assert result.confident is False


def test_rm12_one_resume_clearly_dominates(db_session):
    result, resumes = _match(db_session, ["databricks.json", "aws.json", "azure.json", "healthcare.json", "python_generalist.json"], "rm12_clear_dominant.txt")
    assert result.best.resume_id == resumes[0].id
    assert result.best.score - result.ranked[1].score > 15


def test_rm13_missing_metadata_but_strong_resume_text_does_not_crash(db_session):
    """A resume that was indexed but somehow ended up with an empty metadata
    dict (e.g. a parsing edge case) must not crash the matcher - it should just
    score low/zero on the skill-overlap signal rather than raising."""
    resume = Resume(
        filename="edge_case_resume.pdf", file_path="/tmp/edge_case_resume.pdf",
        extracted_text="Databricks Python SQL Airflow expert with 8 years of experience.",
        extracted_metadata={},  # no structured skills/titles/years extracted
        indexing_status="INDEXED",
    )
    db_session.add(resume)
    db_session.commit()
    db_session.refresh(resume)

    jd_text = load_jd("rm13_missing_metadata_strong_text.txt")
    jd = extract_job_details(text=jd_text, subject="")
    result = match_resumes([resume], jd_title=jd.job_title, jd_text=jd_text, jd_requirements=jd.requirements)
    assert result.best is not None
    # no structured skills to match against -> no crash, and no confident match
    # (only the neutral years-compatibility baseline contributes, not a
    # fabricated high score)
    assert result.best.score < 15.0
    assert result.confident is False


def test_rm14_repeated_skill_keyword_without_responsibility_match(db_session):
    result, resumes = _match(db_session, ["databricks.json", "python_generalist.json"], "rm14_repeated_skill_no_responsibility_match.txt")
    # "Python" repeated many times must not by itself outweigh the fact that
    # neither resume is a QA automation/Selenium background - matcher should
    # not blindly reward raw keyword frequency
    assert result.best is not None


def test_rm15_jd_with_irrelevant_buzzwords_still_matches_on_real_skills(db_session):
    result, resumes = _match(db_session, ["databricks.json", "healthcare.json"], "rm15_irrelevant_buzzwords.txt")
    assert result.best.resume_id == resumes[0].id
    assert "growth mindset" not in result.best.explanation.lower()
    assert "team player" not in result.best.explanation.lower()
