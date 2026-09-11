"""
RULE D/E/F/I - Generate the application email subject + body, and orchestrate
Gmail draft creation with the selected resume attached.

Subject and location-handling are deterministic (never AI) because the spec
requires exact, auditable compliance: never leak the candidate's current
location, always use the "I'm interested in the..." style, format
the subject as "Application for [Job Title] - [City, ST]" - a single,
concise location (never a joined list, never omitted-but-implied), and no
trailing location segment at all when the JD states none.

A few body snippets (the "hope you're doing well" line, the capability
sentence, the closing call-to-action) are user-customizable from the
Settings page (see services/runtime_settings.py) - the DEFAULT_* constants
below are both the built-in text AND the single source of truth those
overrides fall back to when unset or invalid.

An AI provider MAY be used afterward to polish phrasing (`polish_email_body`),
but the deterministic template is always the source of truth, and the AI
output is validated against the same forbidden-phrase list before being
accepted - if it fails validation, we keep the deterministic template.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.base import AIProvider
from app.services.ai_text_guardrails import canonical_skill, validate_generated_statement

FORBIDDEN_PHRASES = [
    "thank you for sharing the opportunity",
    "thank you for reaching out",
    "thanks for reaching out",
    "thank you for sending this opportunity",
    "thank you for contacting me",
    "thank you for the opportunity",
]

DEFAULT_HOPE_LINE = "Hope you're doing well."
DEFAULT_CAPABILITY_SENTENCE = (
    "I have experience building scalable data pipelines, analytical data "
    "models, reporting solutions, and governed data platforms"
)
DEFAULT_CLOSING_LINE = "Please find my updated resume attached for your review. I'd be happy to discuss further."
DEFAULT_CAREER_FOCUS_SENTENCE = (
    "Throughout my career, I've focused on delivering reliable, well-tested "
    "solutions and collaborating closely with cross-functional teams to meet "
    "both technical and business goals."
)
DEFAULT_CLOSING_APPRECIATION = (
    "Thank you for your time and consideration - I look forward to the possibility of connecting."
)


@dataclass
class GeneratedEmail:
    subject: str
    body: str


def _primary_location(job_location: str | None) -> str | None:
    """Subject lines show just one concise place - if the JD lists multiple
    ('Irvine, CA / Los Angeles, CA'), only the first is used. Never invents
    anything: this only ever narrows down text already extracted from the
    JD, and returns None (mention nothing) when there's nothing to narrow."""
    if not job_location:
        return None
    first = job_location.split("/")[0].strip()
    return first or None


def generate_subject(job_title: str | None, job_location: str | None) -> str:
    title = job_title.strip() if job_title else "the opportunity"
    location = _primary_location(job_location)
    if location:
        return f"Application for {title} - {location}"
    return f"Application for {title}"


_SKILL_CASE_OVERRIDES = {
    "etl": "ETL", "elt": "ELT", "etl/elt": "ETL/ELT", "ci/cd": "CI/CD",
    "power bi": "Power BI", "azure devops": "Azure DevOps", "dbt": "dbt",
}


def _format_skill(term: str) -> str:
    override = _SKILL_CASE_OVERRIDES.get(term.lower())
    if override:
        return override
    return term.upper() if len(term) <= 3 else term.title()


def _top_skills_for_body(skills: list[str]) -> str:
    if skills:
        return ", ".join(_format_skill(s) for s in skills)
    return "the required technologies"


_YEAR_WORD_RE = re.compile(r"year", re.IGNORECASE)


def _experience_phrase(experience: str) -> str:
    text = experience.strip()
    if not text or _YEAR_WORD_RE.search(text):
        return text
    return f"{text} years"


_VISA_TYPES = {
    "h1b", "h-1b", "opt", "opt-ead", "cpt", "gc ead", "gc-ead", "green card ead",
    "tn", "l1", "l-1", "l1b", "l-1b", "l1a", "l-1a", "ead",
}


def _work_auth_phrase(work_auth: str) -> str:
    text = work_auth.strip()
    if text.lower() in _VISA_TYPES:
        return f"{text} authorized"
    return text


def generate_body(
    job_title: str | None,
    job_location: str | None,
    recruiter_first_name: str,
    top_skills: list[str],
    candidate_name: str,
    candidate_experience: str,
    candidate_work_auth: str,
    candidate_phone: str,
    candidate_email: str,
    candidate_linkedin: str,
    hope_line: str = DEFAULT_HOPE_LINE,
    capability_sentence: str = DEFAULT_CAPABILITY_SENTENCE,
    closing_line: str = DEFAULT_CLOSING_LINE,
    skills_sentence: str | None = None,
) -> str:
    """`recruiter_first_name` is intentionally accepted but no longer used in
    the greeting - the recruiter's name is often wrong/unreliable (extracted
    from a forwarded signature, a generic team alias, etc.), so the email
    always opens with a plain, safe "Hi," rather than risking an incorrect
    name. `candidate_linkedin` is likewise accepted but no longer rendered in
    the body - only the fields the candidate actually wants surfaced here are
    included.

    `hope_line`/`capability_sentence`/`closing_line` are the only
    user-customizable pieces (see services/runtime_settings.py) - callers
    are expected to pass already-validated values (or the defaults);
    `hope_line` may be an empty string to omit that line entirely.

    `skills_sentence` is an already-validated, JD-tailored sentence (see
    `generate_email`'s AI-assisted skills pitch) that replaces the plain
    "with strong expertise in X, Y, Z" list when given - callers pass None
    to keep the fully deterministic, always-identical-shape template."""
    title = job_title.strip() if job_title else "this opportunity"
    location_suffix = f" in {job_location.strip()}" if job_location else ""
    experience_phrase = _experience_phrase(candidate_experience)
    work_auth_phrase = _work_auth_phrase(candidate_work_auth)

    if skills_sentence:
        expertise_sentence = (
            f"I have {experience_phrase} of experience. {skills_sentence.rstrip('.')}. {capability_sentence}."
        )
    else:
        # First N matched skills carry the "strong expertise in" sentence;
        # any further matches (still real, still JD-matched - never
        # invented) round out a second "along with" clause rather than one
        # long list.
        primary_skills_text = _top_skills_for_body(top_skills[:8])
        remaining_skills = top_skills[8:13]
        along_with_clause = f", along with {_top_skills_for_body(remaining_skills)}" if remaining_skills else ""
        expertise_sentence = (
            f"I have {experience_phrase} of experience with strong expertise in {primary_skills_text}. "
            f"{capability_sentence}{along_with_clause}."
        )

    hope_paragraph = f"\n\n{hope_line}" if hope_line else ""

    body = f"""Hi,{hope_paragraph}

I'm interested in the {title} opportunity{location_suffix}. {expertise_sentence}

{DEFAULT_CAREER_FOCUS_SENTENCE}

I'm {work_auth_phrase} and available for this opportunity. {closing_line}

{DEFAULT_CLOSING_APPRECIATION}

Candidate Details
Name: {candidate_name}
Experience: {experience_phrase}
Work Authorization: {candidate_work_auth}
Phone: {candidate_phone}
Email: {candidate_email}

Best regards,
{candidate_name}
"""
    return body.strip() + "\n"


def violates_forbidden_phrases(text: str) -> str | None:
    lowered = text.lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lowered:
            return phrase
    return None


def contains_location(text: str, forbidden_location: str | None) -> bool:
    if not forbidden_location:
        return False
    return forbidden_location.strip().lower() in text.lower()


_GREETING_RE = re.compile(r"^hi\s*,\s*$", re.IGNORECASE)


def starts_with_greeting(text: str) -> bool:
    """Rejects two things in one check, both defense-in-depth against a
    non-compliant AI response: (1) the classic small-local-model habit of
    prepending meta commentary before the actual email (e.g. "Here is the
    rewritten email:", "Sure, here's a revised version:"), and (2) naming
    the recruiter in the greeting (e.g. "Hi Naveen,") - the recruiter's name
    is never used, since it's often unreliable. The very first line must be
    a bare "Hi," and nothing else."""
    first_line = text.strip().splitlines()[0].strip() if text.strip() else ""
    return bool(_GREETING_RE.match(first_line))


def generate_email(
    job_title: str | None,
    job_location: str | None,
    recruiter_first_name: str,
    top_skills: list[str],
    candidate_name: str,
    candidate_experience: str,
    candidate_work_auth: str,
    candidate_phone: str,
    candidate_email: str,
    candidate_linkedin: str,
    ai_provider: AIProvider | None = None,
    candidate_current_location: str | None = None,
    hope_line: str = DEFAULT_HOPE_LINE,
    capability_sentence: str = DEFAULT_CAPABILITY_SENTENCE,
    closing_line: str = DEFAULT_CLOSING_LINE,
    jd_text: str | None = None,
) -> GeneratedEmail:
    subject = generate_subject(job_title, job_location)

    # AI-tailored skills pitch: replaces the plain "expertise in X, Y, Z"
    # list (identical shape for every email) with 1-2 sentences that
    # actually reflect what THIS job description emphasizes - restricted to
    # only the already-matched top_skills, so it can never claim a
    # technology the candidate hasn't already been credited with. Any doubt
    # (no provider/jd_text, an exception, a hedge phrase, an unapproved
    # technology mention, too long) silently keeps the deterministic list.
    skills_sentence = None
    if ai_provider is not None and jd_text:
        try:
            pitch = ai_provider.generate_email_skills_pitch(
                job_title=job_title or "", jd_text=jd_text,
                top_skills=top_skills, candidate_experience=candidate_experience,
            )
            approved_canonical = {canonical_skill(s) for s in top_skills}
            skills_sentence = validate_generated_statement(pitch, approved_canonical, max_words=70)
        except Exception:
            pass  # keep the deterministic skills list on any AI failure

    body = generate_body(
        job_title=job_title,
        job_location=job_location,
        recruiter_first_name=recruiter_first_name,
        top_skills=top_skills,
        candidate_name=candidate_name,
        candidate_experience=candidate_experience,
        candidate_work_auth=candidate_work_auth,
        candidate_phone=candidate_phone,
        candidate_email=candidate_email,
        candidate_linkedin=candidate_linkedin,
        hope_line=hope_line,
        capability_sentence=capability_sentence,
        closing_line=closing_line,
        skills_sentence=skills_sentence,
    )

    if ai_provider is not None:
        constraints = [
            "Never use the phrase 'thank you for sharing/sending the opportunity' or similar thank-you-for-sending wording",
            "Never mention or imply the candidate's current/personal location",
            "Keep the 'Candidate Details' block exactly as given, unmodified",
            "Keep it concise and professional",
            "Do not claim any experience/skills not already present in the draft",
            "Never address the recipient by name - the greeting must stay a plain 'Hi,' with no name",
            "Output ONLY the email itself, starting directly with 'Hi,' as the very first "
            "line - never add any preamble, commentary, or explanation before or after it "
            "(e.g. never write things like 'Here is the rewritten email:' or 'Sure, here you go:')",
        ]
        try:
            polished = ai_provider.polish_email_body(body, constraints)
            bad_phrase = violates_forbidden_phrases(polished)
            leaks_location = contains_location(polished, candidate_current_location)
            has_greeting = starts_with_greeting(polished)
            if (
                polished.strip() and not bad_phrase and not leaks_location
                and has_greeting and "Candidate Details" in polished
            ):
                body = polished.strip() + "\n"
        except Exception:
            pass  # keep deterministic template on any AI failure

    return GeneratedEmail(subject=subject, body=body)
