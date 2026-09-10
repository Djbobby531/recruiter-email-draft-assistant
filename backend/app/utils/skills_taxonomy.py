"""
A small, hand-curated skills/technology taxonomy used by both resume metadata
extraction and resume<->JD matching. Deterministic and offline - no paid API
required for the core matching behavior described in the spec (RULE H).
"""
from __future__ import annotations

import re

CLOUD_PLATFORMS = ["aws", "amazon web services", "azure", "gcp", "google cloud"]

DATA_ENGINEERING_TOOLS = [
    "databricks", "airflow", "spark", "pyspark", "kafka", "snowflake", "redshift",
    "bigquery", "dbt", "glue", "step functions", "unity catalog", "delta lake",
    "hadoop", "hive", "nifi", "flink", "informatica", "talend", "ssis",
]

DATABASES = [
    "postgres", "postgresql", "mysql", "mongodb", "dynamodb", "cassandra",
    "oracle", "sql server", "sqlite", "cosmos db", "elasticsearch", "redis",
    "neo4j", "mariadb",
]

FRAMEWORKS = [
    "django", "flask", "fastapi", "react", "angular", "vue", "node.js",
    "spring", "spring boot", ".net", "express", "tensorflow", "pytorch",
    "scikit-learn", "pandas", "numpy",
]

LANGUAGES = ["python", "java", "scala", "sql", "javascript", "typescript", "go", "c#", "c++", "r"]

DEVOPS_TOOLS = [
    "terraform", "kubernetes", "docker", "ci/cd", "jenkins", "github actions",
    "gitlab ci", "ansible", "cloudformation", "helm",
]

GOVERNANCE_CERT = [
    "data governance", "unity catalog", "gdpr", "hipaa", "sox", "pci",
    "aws certified", "azure certified", "databricks certified", "pmp", "cissp",
    "ai agents", "mlops", "data lineage", "data quality",
]

DOMAINS = [
    "healthcare", "finance", "banking", "insurance", "retail", "e-commerce",
    "telecom", "manufacturing", "logistics", "government", "energy",
    "pharma", "life sciences", "automotive", "media", "gaming",
]

CATEGORY_WEIGHTS = {
    "cloud_platforms": 1.2,
    "data_engineering_tools": 1.3,
    "databases": 1.0,
    "frameworks": 0.9,
    "languages": 1.1,
    "devops_tools": 1.0,
    "governance_cert": 0.9,
    "domains": 0.8,
}

ALL_CATEGORIES = {
    "cloud_platforms": CLOUD_PLATFORMS,
    "data_engineering_tools": DATA_ENGINEERING_TOOLS,
    "databases": DATABASES,
    "frameworks": FRAMEWORKS,
    "languages": LANGUAGES,
    "devops_tools": DEVOPS_TOOLS,
    "governance_cert": GOVERNANCE_CERT,
    "domains": DOMAINS,
}

YEARS_RE = re.compile(r"(\d+)\+?\s*(?:years|yrs)\b", re.IGNORECASE)


def extract_skills_by_category(text: str) -> dict[str, list[str]]:
    lowered = text.lower()
    result: dict[str, list[str]] = {}
    for category, terms in ALL_CATEGORIES.items():
        hits = sorted({t for t in terms if t in lowered})
        if hits:
            result[category] = hits
    return result


def extract_years_of_experience(text: str) -> int | None:
    matches = [int(m.group(1)) for m in YEARS_RE.finditer(text)]
    return max(matches) if matches else None


def extract_job_titles(text: str) -> list[str]:
    """Very lightweight heuristic: lines that look like a resume header/title."""
    title_keywords = [
        "engineer", "developer", "architect", "analyst", "scientist",
        "manager", "consultant", "administrator", "lead",
    ]
    titles = []
    for line in text.splitlines()[:40]:
        line_clean = line.strip()
        if 3 < len(line_clean) < 80 and any(k in line_clean.lower() for k in title_keywords):
            titles.append(line_clean)
    return titles[:5]
