"""
Shared "never trust raw AI output" text-safety checks. Anywhere an LLM is
allowed to generate a short natural-language statement that ends up in front
of a real recruiter - a resume summary/experience bullet (resume_customizer.py)
or an email's skills-pitch sentence (draft_service.py) - it must pass through
these checks first. Kept in one place so both call sites can never silently
drift out of sync on what counts as a hedge phrase or an unapproved technology.
"""
from __future__ import annotations

import re

from app.utils.skills_taxonomy import ALL_CATEGORIES as _SKILLS_TAXONOMY_CATEGORIES

# Safe, narrow aliases only - a broad synonym net would risk treating a truly
# different technology as "already approved".
SKILL_ALIASES: dict[str, str] = {
    "amazon web services": "aws", "microsoft azure": "azure",
    "google cloud platform": "gcp", "google cloud": "gcp",
    "apache spark": "spark", "apache kafka": "kafka", "apache airflow": "airflow",
}


def canonical_skill(skill: str) -> str:
    lowered = skill.strip().lower()
    return SKILL_ALIASES.get(lowered, lowered)


# Phrases that mean the model is HEDGING/GUESSING rather than stating a
# documented fact - e.g. "implied through mention of Power BI" or "which
# could be applied to GCP" are the model admitting it's inferring/stretching.
HEDGE_PHRASES = [
    "implied", "implies", "could be applied", "could apply", "may have",
    "might have", "possibly", "presumably", "suggests", "would suggest",
    "not mentioned, but", "not explicitly mentioned", "not directly stated",
    "inferred", "inferring", "assume", "assuming", "likely has",
    "can be inferred", "not explicitly", "one could argue",
]

# Every known technology term across the whole skills taxonomy, longest
# first, used to catch an unapproved technology mentioned inside a generated
# sentence (not just the one field explicitly being validated).
ALL_TAXONOMY_TERMS: list[str] = sorted(
    {t for terms in _SKILLS_TAXONOMY_CATEGORIES.values() for t in terms}, key=len, reverse=True,
)
MAX_STATEMENT_WORDS = 40  # a generous "one full line" cap - rejects large paragraphs


def contains_hedge_language(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in HEDGE_PHRASES)


def contains_unapproved_technology(text: str, approved_canonical: set[str]) -> str | None:
    """Returns the first taxonomy technology term found in `text` that is NOT
    in the approved skill set, or None if every mentioned technology is
    verified. Matches on alnum-boundaries (not `re.search`'s plain `\\b`,
    which behaves oddly around terms like "c++"/"c#") so a short term like
    "r" or "go" only matches as its own standalone token - never as a
    substring of an unrelated word like "orchestration" or "algorithm"."""
    lowered = text.lower()
    for term in ALL_TAXONOMY_TERMS:
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        if re.search(pattern, lowered) and canonical_skill(term) not in approved_canonical:
            return term
    return None


def validate_generated_statement(
    text: str, approved_canonical: set[str], max_words: int = MAX_STATEMENT_WORDS,
) -> str | None:
    """Returns the cleaned statement if it passes every truthfulness/quality
    guard (length, no hedge language, no unapproved technology mentioned),
    else None."""
    if not isinstance(text, str):
        return None
    cleaned = " ".join(text.split()).strip().strip('"')
    if not cleaned or len(cleaned.split()) > max_words:
        return None
    if contains_hedge_language(cleaned):
        return None
    if contains_unapproved_technology(cleaned, approved_canonical):
        return None
    return cleaned
