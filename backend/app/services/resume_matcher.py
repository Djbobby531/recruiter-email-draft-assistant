"""
RULE H - Resume <-> JD matching.

Deterministic, multi-signal weighted scoring (NOT simple keyword counting):
  - category-weighted skill/technology overlap (cloud, DE tools, DBs,
    frameworks, languages, devops, certs/governance, domain)
  - job title similarity (fuzzy string match against resume's detected titles
    and the JD title)
  - years-of-experience compatibility
  - domain/industry overlap

Produces a 0-100 score per resume plus a human-readable explanation. `ranked`
always contains every indexed resume's score, in score order, unaffected by
the rule below - it's the `best` PICK that the rule can override:

By explicit configuration choice, a JD that clearly asks for a specific cloud
platform (AWS/Azure/GCP) prefers the resume whose OWN title says that same
cloud platform, even if another resume scores marginally higher overall - the
title is a stronger, more deterministic signal of "this is the resume built
for that cloud" than the general weighted score. If no resume is titled for
that cloud, it falls back to the "default" data-engineering resume (one whose
title says "data engineer" but names no specific cloud). If neither exists,
selection falls through to the plain highest overall score - same as when the
JD doesn't ask for a specific cloud at all. This override never engages for a
JD that doesn't name a specific cloud platform, so it can never hijack an
otherwise-correct domain-specific match (healthcare, finance, etc.).

If every resume scores below a sane confidence floor, the caller should
route to MANUAL_REVIEW rather than attach a poor match (RULE H / section 20).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from app.models import Resume
from app.utils.skills_taxonomy import CATEGORY_WEIGHTS, extract_skills_by_category, extract_years_of_experience

DEFAULT_MIN_ACCEPTABLE_SCORE = 30.0  # fallback when no threshold is supplied by the caller

# (platform label, pattern) - the earliest match in the combined JD text wins,
# regardless of list order.
_CLOUD_PLATFORM_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws", re.compile(r"\b(aws|amazon\s+web\s+services)\b", re.IGNORECASE)),
    ("azure", re.compile(r"\bazure\b", re.IGNORECASE)),
    ("gcp", re.compile(r"\b(gcp|google\s+cloud)\b", re.IGNORECASE)),
]

_DATA_ENGINEER_TITLE_RE = re.compile(r"\bdata\s+engineer", re.IGNORECASE)


def _detect_cloud_platform(text: str) -> str | None:
    """Returns "aws"/"azure"/"gcp" for whichever platform is named earliest in
    `text`, or None if none is named at all."""
    if not text:
        return None
    earliest: tuple[int, str] | None = None
    for label, pattern in _CLOUD_PLATFORM_PATTERNS:
        m = pattern.search(text)
        if m and (earliest is None or m.start() < earliest[0]):
            earliest = (m.start(), label)
    return earliest[1] if earliest else None


def _is_default_data_engineering_title(resume_titles: list[str]) -> bool:
    """A "default" resume: its own title says "data engineer" but names no
    specific cloud platform - the safe, generic pick when a JD wants a cloud
    platform none of the uploaded resumes are titled for."""
    joined = " ".join(resume_titles)
    return bool(_DATA_ENGINEER_TITLE_RE.search(joined)) and _detect_cloud_platform(joined) is None


@dataclass
class ResumeScore:
    resume_id: int
    filename: str
    score: float
    explanation: str
    matched_terms: list[str] = field(default_factory=list)


def _title_similarity(jd_title: str | None, resume_titles: list[str]) -> float:
    if not jd_title or not resume_titles:
        return 0.0
    best = 0.0
    for t in resume_titles:
        ratio = difflib.SequenceMatcher(None, jd_title.lower(), t.lower()).ratio()
        best = max(best, ratio)
    return best


def _skill_overlap_score(jd_requirements: list[str], jd_text: str, resume_skills: dict[str, list[str]]) -> tuple[float, list[str]]:
    """
    Returns (weighted 0..1 score, list of matched terms in taxonomy prominence
    order). The denominator (total_weight) is built ONLY from what the JD
    actually asks for - not from the resume's own unrelated skills - so a
    resume with many skills the job doesn't need isn't unfairly penalized.
    """
    jd_lower_reqs = {r.lower() for r in jd_requirements}
    jd_skills = extract_skills_by_category(jd_text.lower())

    total_weight = 0.0
    achieved_weight = 0.0
    matched_terms: list[str] = []
    matched_set: set[str] = set()

    for category, jd_terms in jd_skills.items():
        weight = CATEGORY_WEIGHTS.get(category, 1.0)
        resume_terms = set(resume_skills.get(category, []))
        for term in jd_terms:
            total_weight += weight
            if term in resume_terms:
                achieved_weight += weight
                if term not in matched_set:
                    matched_set.add(term)
                    matched_terms.append(term)

    # freeform JD requirement lines not captured by the taxonomy at all
    categorized_jd_terms = {t for terms in jd_skills.values() for t in terms}
    for req in jd_lower_reqs - categorized_jd_terms:
        total_weight += 1.0
        if any(req in terms for terms in resume_skills.values()):
            achieved_weight += 1.0
            if req not in matched_set:
                matched_set.add(req)
                matched_terms.append(req)

    if total_weight == 0:
        return 0.0, []
    return min(1.0, achieved_weight / total_weight), matched_terms


def _years_compatibility(jd_text: str, resume_years: int | None) -> float:
    required = extract_years_of_experience(jd_text)
    if required is None or resume_years is None:
        return 0.5  # neutral - not enough info to penalize or reward
    if resume_years >= required:
        return 1.0
    if resume_years >= required - 1:
        return 0.7
    return 0.3


def score_resume(resume: Resume, jd_title: str | None, jd_text: str, jd_requirements: list[str]) -> ResumeScore:
    metadata = resume.extracted_metadata or {}
    resume_skills = metadata.get("skills", {})
    resume_titles = metadata.get("job_titles", [])
    resume_years = metadata.get("years_of_experience")

    skill_score, matched_terms = _skill_overlap_score(jd_requirements, jd_text, resume_skills)
    title_score = _title_similarity(jd_title, resume_titles)
    years_score = _years_compatibility(jd_text, resume_years)

    # weighted blend: skills matter most, then title, then years
    final = (skill_score * 0.65 + title_score * 0.20 + years_score * 0.15) * 100
    final = round(min(100.0, max(0.0, final)), 1)

    top_matches = matched_terms[:8]
    explanation = (
        f"skill overlap {skill_score * 100:.0f}% (matched: {', '.join(top_matches) or 'none'}); "
        f"title similarity {title_score * 100:.0f}%; "
        f"experience compatibility {years_score * 100:.0f}%"
    )

    return ResumeScore(
        resume_id=resume.id, filename=resume.filename, score=final,
        explanation=explanation, matched_terms=matched_terms,
    )


@dataclass
class MatchResult:
    ranked: list[ResumeScore] = field(default_factory=list)
    best: ResumeScore | None = None
    confident: bool = False


def _select_best_for_cloud_platform(
    indexed: list[Resume], scores: list[ResumeScore], jd_title: str | None, jd_text: str
) -> ResumeScore | None:
    jd_cloud = _detect_cloud_platform(f"{jd_title or ''}\n{jd_text or ''}")
    if jd_cloud is None:
        return None  # JD doesn't ask for a specific cloud - never overrides plain scoring

    titles_by_id = {r.id: (r.extracted_metadata or {}).get("job_titles", []) for r in indexed}

    cloud_titled = [s for s in scores if _detect_cloud_platform(" ".join(titles_by_id[s.resume_id])) == jd_cloud]
    if cloud_titled:
        return max(cloud_titled, key=lambda s: s.score)

    default_titled = [s for s in scores if _is_default_data_engineering_title(titles_by_id[s.resume_id])]
    if default_titled:
        return max(default_titled, key=lambda s: s.score)

    return None  # no cloud-titled or default resume on file - fall through to plain top score


def match_resumes(
    resumes: list[Resume],
    jd_title: str | None,
    jd_text: str,
    jd_requirements: list[str],
    min_score: float = DEFAULT_MIN_ACCEPTABLE_SCORE,
) -> MatchResult:
    indexed = [r for r in resumes if r.indexing_status.startswith("INDEXED")]
    if not indexed:
        return MatchResult(ranked=[], best=None, confident=False)

    scores = [score_resume(r, jd_title, jd_text, jd_requirements) for r in indexed]
    scores.sort(key=lambda s: s.score, reverse=True)

    best = _select_best_for_cloud_platform(indexed, scores, jd_title, jd_text) or scores[0]
    confident = best.score >= min_score
    return MatchResult(ranked=scores, best=best, confident=confident)
