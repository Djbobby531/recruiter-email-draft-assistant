"""
Cloud-platform-based resume selection (Tweaks): a JD that clearly asks for a
specific cloud platform prefers the resume whose OWN title names that same
platform - AWS JD -> AWS-titled resume, Azure JD -> Azure-titled resume, GCP
JD -> GCP-titled resume - even over a resume that scores marginally higher on
the general weighted score. If no resume is titled for the requested cloud,
it falls back to the "default" data-engineering resume (titled "Data
Engineer" with no cloud named). This override never engages for a JD that
doesn't name a specific cloud platform at all - see
test_cloud_free_jd_is_never_affected_by_the_override below, which locks in
that domain-specific matches (healthcare, finance, etc.) never get hijacked
by it.
"""
from __future__ import annotations

from app.services.jd_extractor import extract_job_details
from app.services.resume_matcher import match_resumes
from tests.conftest import make_resume_from_fixture


def _match(db_session, resume_fixtures, jd_text, min_score=30.0):
    resumes = [make_resume_from_fixture(db_session, f) for f in resume_fixtures]
    jd = extract_job_details(text=jd_text, subject="")
    result = match_resumes(
        resumes, jd_title=jd.job_title, jd_text=jd_text,
        jd_requirements=jd.requirements, min_score=min_score,
    )
    return result, resumes


AWS_JD = """Role: AWS Data Engineer
Location: Remote

Required Skills:
- AWS
- Glue
- Redshift
- Python
"""

AZURE_JD = """Role: Azure Data Engineer
Location: Remote

Required Skills:
- Azure
- Databricks
- Python
"""

GCP_JD = """Role: GCP Data Engineer
Location: Remote

Required Skills:
- Google Cloud
- BigQuery
- Python
"""

GENERIC_DE_JD = """Role: Senior Data Engineer
Location: Remote

Required Skills:
- Databricks
- Airflow
- Python
- SQL
"""

HEALTHCARE_JD = """Role: Healthcare Data Analyst
Location: Remote

Required Skills:
- SQL
- Python
- HIPAA
"""


def test_aws_jd_selects_the_aws_titled_resume(db_session):
    result, resumes = _match(db_session, ["databricks.json", "aws.json", "azure.json", "gcp.json"], AWS_JD)
    aws_resume = resumes[1]
    assert result.best.resume_id == aws_resume.id


def test_azure_jd_selects_the_azure_titled_resume(db_session):
    result, resumes = _match(db_session, ["databricks.json", "aws.json", "azure.json", "gcp.json"], AZURE_JD)
    azure_resume = resumes[2]
    assert result.best.resume_id == azure_resume.id


def test_gcp_jd_selects_the_gcp_titled_resume_when_one_exists(db_session):
    result, resumes = _match(db_session, ["databricks.json", "aws.json", "azure.json", "gcp.json"], GCP_JD)
    gcp_resume = resumes[3]
    assert result.best.resume_id == gcp_resume.id


def test_gcp_jd_falls_back_to_the_default_data_engineering_resume_when_no_gcp_resume_exists(db_session):
    """Item 4 of the tweaks list: "if nothing matches the default data
    engineering resume should be selected" - here nothing is titled for GCP,
    so the plain "Senior Data Engineer" / "Data Platform Engineer" titled
    resume (databricks.json) is the safe generic pick."""
    result, resumes = _match(db_session, ["databricks.json", "aws.json", "azure.json"], GCP_JD)
    default_resume = resumes[0]  # databricks.json - no cloud named in its title
    assert result.best.resume_id == default_resume.id


def test_aws_jd_falls_through_to_plain_scoring_when_no_aws_or_default_resume_exists(db_session):
    """Ultimate safety net: if neither an AWS-titled nor a "default"
    data-engineering-titled resume exists at all, selection must still fall
    through to ordinary scoring rather than erroring or returning nothing."""
    result, resumes = _match(db_session, ["azure.json", "healthcare.json"], AWS_JD)
    assert result.best is not None
    assert len(result.ranked) == 2


def test_cloud_free_jd_is_never_affected_by_the_override(db_session):
    """The override must never engage for a JD that doesn't name a specific
    cloud platform - a domain-specific match (healthcare here) must keep
    winning on its own merits, never get hijacked into a "default"
    data-engineering resume just because one happens to be in the pool."""
    result, resumes = _match(db_session, ["healthcare.json", "databricks.json"], HEALTHCARE_JD)
    healthcare_resume = resumes[0]
    assert result.best.resume_id == healthcare_resume.id


def test_generic_data_engineer_jd_with_no_cloud_mentioned_uses_plain_scoring(db_session):
    result, resumes = _match(db_session, ["databricks.json", "aws.json", "azure.json"], GENERIC_DE_JD)
    databricks_resume = resumes[0]
    assert result.best.resume_id == databricks_resume.id


def test_ranked_list_is_never_shrunk_by_the_cloud_platform_override(db_session):
    """`ranked` must always reflect every indexed resume's score, regardless
    of which one the override ultimately picks as `best`."""
    result, resumes = _match(db_session, ["databricks.json", "aws.json", "azure.json", "gcp.json"], GCP_JD)
    assert len(result.ranked) == 4
