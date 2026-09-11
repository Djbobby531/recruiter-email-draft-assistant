"""
Job description extraction: job title, job-required location, requirements.

Deterministic regex/heuristic extraction runs first (works fully offline, and
is what the sample input in the spec is shaped for: "Role: ... Location: ...").
If it can't find a title or location and an AI provider is configured, we fall
back to AI extraction. AI is never allowed to invent a location - the prompt
requires verbatim copy, and we additionally validate the AI's location string
actually appears in the source text before trusting it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ai.base import AIProvider
from app.utils.text_cleaning import clean_role_text

TITLE_PATTERNS = [
    re.compile(r"(?:^|\n)\s*(?:Role|Job Title|Position|Title)\s*[:\-]\s*(?P<val>[^\n]{3,120})", re.IGNORECASE),
]

LOCATION_PATTERNS = [
    re.compile(r"(?:^|\n)\s*(?:Location|Job Location|Work Location)\s*[:\-]\s*(?P<val>[^\n]{3,160})", re.IGNORECASE),
]

REQUIREMENTS_SECTION_PATTERNS = [
    re.compile(r"(?:^|\n)\s*(?:Skills|Required Skills|Requirements|Qualifications|Tech(?:nology)? Stack)\s*[:\-]?\s*\n(?P<val>(?:.+\n?){1,40})", re.IGNORECASE),
]

# Subject line pattern used in the sample data:
# "Fw: Hiring for || Role: Senior Data Engineer (Databricks) Location: Irvine, CA ... ||"
#
# The trailing terminator is a LOOKAHEAD for "||", end-of-line, or true
# end-of-string - not a consuming `\s*$` - because `extract_job_details`
# always searches this against `f"{subject}\n{full_body_text}"`, not the
# subject alone. A bare `$` (no MULTILINE) only matches the true end of the
# WHOLE combined string, so a single-line subject like "Role: X Location:
# Y" - with a real email body following it - would never satisfy `$` right
# after "Y", silently failing to match at all and falling through to the
# much cruder TITLE_PATTERNS below, which has no notion of where an
# embedded "Location:" clause starts and would swallow it into the title
# (e.g. "Senior Data Engineer Location: Remote"). Matching up to end-of-LINE
# fixes that without needing re.MULTILINE (which would also change `^`
# elsewhere in this module).
SUBJECT_ROLE_LOCATION_RE = re.compile(
    r"Role\s*:\s*(?P<title>.+?)\s*Location\s*:\s*(?P<location>.+?)(?=\s*\|\||\n|$)",
    re.IGNORECASE,
)

KNOWN_TECH_TERMS = [
    "python", "sql", "java", "scala", "spark", "databricks", "airflow", "kafka",
    "terraform", "kubernetes", "docker", "aws", "azure", "gcp", "google cloud",
    "snowflake", "redshift", "bigquery", "unity catalog", "data governance",
    "ci/cd", "etl", "elt", "data pipelines", "power bi", "tableau", "hadoop",
    "hive", "nosql", "postgres", "mysql", "mongodb", "dynamodb", "react",
    "node.js", "django", "flask", "fastapi", "machine learning", "ai agents",
    "data lake", "data warehouse", "lambda", "glue", "step functions",
    "delta lake", "pyspark", "linux", "rest api", "microservices", "git",
]


TITLE_KEYWORDS = [
    "engineer", "developer", "architect", "analyst", "scientist", "manager",
    "consultant", "administrator", "lead", "specialist", "director", "designer",
]

SUBJECT_NOISE_PREFIX_RE = re.compile(
    r"^\s*(?:(?:fwd?|re)\s*:\s*)+|^\s*(?:now hiring|job opening|new opportunity|hiring|urgent requirement)\s*:\s*",
    re.IGNORECASE,
)

# "City, ST" (two-letter state code) or a bare Remote/Hybrid/Nationwide tail -
# used to decide whether the text after a dash in a bare subject line is a
# location (split it out) or part of the title itself (e.g. "Data Engineer - AWS").
BARE_LOCATION_TAIL_RE = re.compile(r"^[A-Za-z][A-Za-z .]{1,40},\s*[A-Z]{2}$")
BARE_LOCATION_KEYWORD_RE = re.compile(r"^(Remote|Hybrid|Onsite|On-site|Nationwide)\b", re.IGNORECASE)

BARE_TITLE_LOCATION_SPLIT_RE = re.compile(r"^(?P<title>.+?)\s+[–—-]\s+(?P<tail>[^–—-]+)$")

TITLE_ABBREVIATIONS = [
    (re.compile(r"\bSr\.(?=\s|$)"), "Senior"),
    (re.compile(r"\bSr\b(?!\.)"), "Senior"),
    (re.compile(r"\bJr\.(?=\s|$)"), "Junior"),
    (re.compile(r"\bJr\b(?!\.)"), "Junior"),
]


@dataclass
class JobDetails:
    job_title: str | None = None
    job_location: str | None = None
    requirements: list[str] = field(default_factory=list)
    source: str = "deterministic"


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -|:")


LOCATION_QUALIFIER_RE = re.compile(
    r"\s*\((?:Hybrid|Remote|Onsite|On-site|Local Preferred|Local Only|W2 Only)\)\s*",
    re.IGNORECASE,
)

# A "Location:" line is sometimes really a work-arrangement description plus
# a soft regional preference tacked on as a free-text sentence, e.g. "Onshore
# United States Remote, anyone from Pennsylvania area will be highly
# preferred." The named region is far more useful in a subject line than the
# whole run-on sentence - extracted verbatim from the source, never invented.
LOCATION_REGIONAL_PREFERENCE_RE = re.compile(
    r"\bfrom\s+(?P<place>[A-Za-z][A-Za-z .]{1,40}?)\s*(?:area)?\s*(?:will\s+be\s+)?"
    r"(?:strongly|highly)?\s*preferred\b",
    re.IGNORECASE,
)


def _clean_location(value: str) -> str:
    """Strip work-arrangement qualifiers like '(Hybrid)'/'(Local Preferred)' and
    normalize 'X or Y' into 'X / Y' for a concise subject line, without ever
    inventing a location not present in the source text."""
    preference_override = LOCATION_REGIONAL_PREFERENCE_RE.search(value)
    if preference_override:
        return _clean(preference_override.group("place"))
    cleaned = LOCATION_QUALIFIER_RE.sub(" ", value)
    cleaned = re.sub(r"\s+or\s+", " / ", cleaned, flags=re.IGNORECASE)
    return _clean(cleaned)


def _normalize_title(title: str | None) -> str | None:
    if not title:
        return title
    normalized = title
    for pattern, replacement in TITLE_ABBREVIATIONS:
        normalized = pattern.sub(replacement, normalized)
    # Recruiter subject lines routinely carry decorative junk (emoji, a
    # stray "#") that has no place in a job title once it's used verbatim
    # in a resume header, filename, or email subject/body downstream.
    return clean_role_text(_clean(normalized))


def _extract_title(text: str) -> str | None:
    subj_match = SUBJECT_ROLE_LOCATION_RE.search(text)
    if subj_match:
        return _normalize_title(subj_match.group("title"))
    for pat in TITLE_PATTERNS:
        m = pat.search(text)
        if m:
            return _normalize_title(m.group("val"))
    return None


def _looks_like_location_tail(tail: str) -> bool:
    t = tail.strip()
    return bool(BARE_LOCATION_TAIL_RE.match(t) or BARE_LOCATION_KEYWORD_RE.match(t))


def _looks_like_job_title(candidate: str) -> bool:
    lowered = candidate.lower()
    return 3 < len(candidate) < 100 and any(k in lowered for k in TITLE_KEYWORDS)


def _extract_bare_subject_title_location(subject: str) -> tuple[str | None, str | None]:
    """
    Fallback for subjects that state the title (and sometimes a dash-separated
    location) directly with no "Role:"/"Location:" labels at all, e.g.
    "Senior Data Engineer - Dallas, TX" or "Sr. Data Engineer (Databricks)".
    Never invents a location - only splits one out when the text after the
    dash actually looks like a place (City, ST or a Remote/Hybrid keyword);
    otherwise a dash-suffixed tech suffix (e.g. "Data Engineer - AWS") is kept
    as part of the title rather than misread as a location.
    """
    if not subject or not subject.strip():
        return None, None

    cleaned_subject = SUBJECT_NOISE_PREFIX_RE.sub("", subject).strip(" |")
    if not cleaned_subject:
        return None, None

    split_match = BARE_TITLE_LOCATION_SPLIT_RE.match(cleaned_subject)
    if split_match:
        tail = split_match.group("tail")
        if _looks_like_location_tail(tail):
            return _normalize_title(split_match.group("title")), _clean_location(tail)

    if _looks_like_job_title(cleaned_subject):
        return _normalize_title(cleaned_subject), None

    return None, None


def _extract_location(text: str) -> str | None:
    subj_match = SUBJECT_ROLE_LOCATION_RE.search(text)
    if subj_match:
        return _clean_location(subj_match.group("location"))
    for pat in LOCATION_PATTERNS:
        m = pat.search(text)
        if m:
            return _clean_location(m.group("val"))
    return None


EMPLOYMENT_TYPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bcorp\s*to\s*corp\b|\bc2c\b", re.IGNORECASE), "C2C"),
    (re.compile(r"\bc2h\b|\bcontract\s*to\s*hire\b", re.IGNORECASE), "Contract-to-Hire"),
    (re.compile(r"\bw2\b|\bw-2\b", re.IGNORECASE), "W2"),
    (re.compile(r"\b1099\b", re.IGNORECASE), "1099"),
    (re.compile(r"\bfull[-\s]?time\b", re.IGNORECASE), "Full-time"),
    (re.compile(r"\bpart[-\s]?time\b", re.IGNORECASE), "Part-time"),
    (re.compile(r"\bcontract\b", re.IGNORECASE), "Contract"),
]


def extract_employment_type(text: str) -> str | None:
    """Best-effort employment-type tag (section 8's `employment_type` field).
    Never invented - returns None when the text simply doesn't say."""
    if not text:
        return None
    for pattern, label in EMPLOYMENT_TYPE_PATTERNS:
        if pattern.search(text):
            return label
    return None


# --- Local-candidate requirement (dashboard phase, section 10) -------------
#
# Must never be confused with job location or the candidate's own location.
# "YES" requires an explicit only/must/required local phrase; "PREFERRED" is
# a softer ask; a bare "remote"/"work from home" mention (with no local-only
# or local-preferred phrase present) means "NO". Anything else is UNKNOWN -
# never guessed.
LOCAL_YES_PATTERNS = [
    re.compile(r"\blocal candidates?\s+only\b", re.IGNORECASE),
    re.compile(r"\bonly\s+local candidates?\b", re.IGNORECASE),
    re.compile(r"\bmust be local\b", re.IGNORECASE),
    re.compile(r"\bmust reside locally\b", re.IGNORECASE),
    re.compile(r"\blocal candidates?\s+(?:are\s+)?required\b", re.IGNORECASE),
    re.compile(r"\bno relocation\b", re.IGNORECASE),
]

LOCAL_PREFERRED_PATTERNS = [
    re.compile(r"\blocal candidates?\s+preferred\b", re.IGNORECASE),
    re.compile(r"\bprefer(?:ably|red)?\s+local candidates?\b", re.IGNORECASE),
    re.compile(r"\blocal candidates?\s+(?:strongly\s+)?preferred\b", re.IGNORECASE),
]

LOCAL_NO_PATTERNS = [
    re.compile(r"\bfully\s+remote\b", re.IGNORECASE),
    re.compile(r"\b100%\s*remote\b", re.IGNORECASE),
    re.compile(r"\bremote position\b", re.IGNORECASE),
    re.compile(r"\bwork from home\b", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*Location\s*[:\-]\s*Remote\b", re.IGNORECASE),
]


def extract_local_requirement(text: str) -> str:
    """Returns YES / NO / PREFERRED / UNKNOWN. Never derived from the
    candidate's own location - only from what the recruiter/JD text says."""
    if not text:
        return "UNKNOWN"
    if any(p.search(text) for p in LOCAL_YES_PATTERNS):
        return "YES"
    if any(p.search(text) for p in LOCAL_PREFERRED_PATTERNS):
        return "PREFERRED"
    if any(p.search(text) for p in LOCAL_NO_PATTERNS):
        return "NO"
    return "UNKNOWN"


# --- Implementation partner / end client (sections 11-12) ------------------
#
# Both are label-anchored to the start of a line (like TITLE_PATTERNS /
# LOCATION_PATTERNS above) so a mid-sentence mention - e.g. "our recruiter
# works with JPMorgan" - can never be mistaken for an explicit "Client:" /
# "Implementation Partner:" declaration. Falls back to UNKNOWN/None rather
# than guessing.
IMPLEMENTATION_PARTNER_PATTERNS = [
    re.compile(
        r"(?:^|\n)\s*(?:Implementation Partner|Staffing Partner|Vendor|Recruiting (?:Company|Agency)|Supplier)\s*[:\-]\s*(?P<val>[^\n]{2,120})",
        re.IGNORECASE,
    ),
]

END_CLIENT_PATTERNS = [
    re.compile(r"(?:^|\n)\s*End Client\s*[:\-]\s*(?P<val>[^\n]{2,120})", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*Client\s*[:\-]\s*(?P<val>[^\n]{2,120})", re.IGNORECASE),
]

_NON_ANSWER_VALUES_RE = re.compile(r"^(unknown|n/?a|tbd|confidential)$", re.IGNORECASE)


def extract_implementation_partner(text: str, fallback_company: str | None = None) -> str | None:
    """Explicit "Implementation Partner:"/"Vendor:"/etc. label wins; otherwise
    falls back to the recruiter's own company (already independently
    extracted elsewhere) when available. Never guessed beyond that."""
    if text:
        for pat in IMPLEMENTATION_PARTNER_PATTERNS:
            m = pat.search(text)
            if m:
                val = _clean(m.group("val"))
                if val and not _NON_ANSWER_VALUES_RE.match(val):
                    return val
    return fallback_company


def extract_end_client(text: str) -> str | None:
    """Only trusts an explicit "End Client:"/"Client:" label - never assumes
    the recruiter's own company is the end client (section 12)."""
    if not text:
        return None
    for pat in END_CLIENT_PATTERNS:
        m = pat.search(text)
        if m:
            val = _clean(m.group("val"))
            if val and not _NON_ANSWER_VALUES_RE.match(val):
                return val
    return None


def _extract_requirements(text: str) -> list[str]:
    found: set[str] = set()
    lowered = text.lower()
    for term in KNOWN_TECH_TERMS:
        if term in lowered:
            found.add(term)

    for pat in REQUIREMENTS_SECTION_PATTERNS:
        m = pat.search(text)
        if m:
            block = m.group("val")
            for line in block.splitlines():
                line = re.sub(r"^[\s\-\*•]+", "", line).strip()
                if line and len(line) < 80 and not re.match(r"^(skills|requirements)\s*:?$", line, re.IGNORECASE):
                    found.add(line.lower())

    return sorted(found)


def extract_job_details(text: str, subject: str, ai_provider: AIProvider | None = None) -> JobDetails:
    combined = f"{subject}\n{text}"

    title = _extract_title(combined)
    location = _extract_location(combined)
    requirements = _extract_requirements(combined)

    if not title or not location:
        bare_title, bare_location = _extract_bare_subject_title_location(subject)
        title = title or bare_title
        location = location or bare_location

    if title and location:
        return JobDetails(job_title=title, job_location=location, requirements=requirements)

    if ai_provider is not None:
        try:
            ai_result = ai_provider.extract_job_details(combined[:8000])
            ai_title = clean_role_text(ai_result.get("job_title")) or title
            ai_location = ai_result.get("job_location")
            # never trust an AI-invented location: it must appear verbatim in source text
            if ai_location and ai_location.lower() not in combined.lower():
                ai_location = location
            ai_location = ai_location or location
            ai_requirements = ai_result.get("requirements") or []
            merged_requirements = sorted(set(requirements) | {str(r).lower() for r in ai_requirements})
            return JobDetails(
                job_title=ai_title, job_location=ai_location, requirements=merged_requirements, source="ai"
            )
        except Exception:
            pass

    return JobDetails(job_title=title, job_location=location, requirements=requirements)
