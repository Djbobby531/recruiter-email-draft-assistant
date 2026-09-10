from __future__ import annotations

from app.services.resume_matcher import match_resumes
from tests.conftest import make_resume
from tests.test_pipeline import SAMPLE_BODY


def test_ranks_relevant_resume_above_unrelated(db_session):
    databricks_resume = make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"],
            "devops_tools": ["terraform", "ci/cd"],
            "governance_cert": ["data governance", "unity catalog"],
        },
        years=8, titles=["Senior Data Engineer"],
    )
    healthcare_resume = make_resume(
        db_session, "healthcare_resume.pdf",
        skills={"domains": ["healthcare"], "languages": ["java"]},
        years=3, titles=["Healthcare Analyst"],
    )

    result = match_resumes(
        [databricks_resume, healthcare_resume],
        jd_title="Senior Data Engineer (Databricks)",
        jd_text=SAMPLE_BODY,
        jd_requirements=["databricks", "python", "sql", "terraform", "data governance", "unity catalog", "airflow", "ci/cd"],
    )

    assert result.best is not None
    assert result.best.resume_id == databricks_resume.id
    assert result.confident is True
    assert len(result.ranked) == 2


def test_no_indexed_resumes_returns_no_confident_match(db_session):
    unindexed = make_resume(db_session, "pending.pdf", skills={}, status="PENDING")
    result = match_resumes([unindexed], jd_title="Data Engineer", jd_text="python sql", jd_requirements=["python"])
    assert result.best is None
    assert result.confident is False
