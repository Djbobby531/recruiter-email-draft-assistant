from __future__ import annotations

from app.ai.base import AIProvider
from app.services import draft_service


class _StubAIProvider(AIProvider):
    """Minimal AIProvider double - only polish_email_body is ever exercised
    by these tests, the rest simply aren't called."""

    def __init__(self, polished_text: str):
        self._polished_text = polished_text

    def classify_job_email(self, subject, body):
        raise NotImplementedError

    def extract_job_details(self, text):
        raise NotImplementedError

    def polish_email_body(self, draft_body, constraints):
        return self._polished_text

    def classify_interview_requirement(self, text):
        raise NotImplementedError

    def evaluate_and_customize_resume(self, resume_text, jd_title, jd_text, candidate_existing_skills):
        raise NotImplementedError


def _generate_email_kwargs(**overrides):
    kwargs = dict(
        job_title="Senior Data Engineer",
        job_location="Remote",
        recruiter_first_name="Naveen",
        top_skills=["databricks", "python"],
        candidate_name="Diwakar Jilakara",
        candidate_experience="8+ years",
        candidate_work_auth="Authorized to work in the US",
        candidate_phone="555-123-4567",
        candidate_email="diwakar@example.com",
        candidate_linkedin="linkedin.com/in/diwakar",
    )
    kwargs.update(overrides)
    return kwargs


def test_subject_with_location():
    """A JD listing multiple locations only shows the first, concise one."""
    subject = draft_service.generate_subject("Senior Data Engineer (Databricks)", "Irvine, CA / Los Angeles, CA")
    assert subject == "Application for Senior Data Engineer (Databricks) - Irvine, CA"


def test_subject_without_location_does_not_invent_one():
    subject = draft_service.generate_subject("Senior Data Engineer (Databricks)", None)
    assert subject == "Application for Senior Data Engineer (Databricks)"
    assert "None" not in subject


def test_body_never_uses_forbidden_thank_you_phrases():
    body = draft_service.generate_body(
        job_title="Senior Data Engineer",
        job_location=None,
        recruiter_first_name="Naveen",
        top_skills=["databricks", "python", "sql"],
        candidate_name="Diwakar Jilakara",
        candidate_experience="8+ years",
        candidate_work_auth="Authorized to work in the US",
        candidate_phone="555-123-4567",
        candidate_email="diwakar@example.com",
        candidate_linkedin="linkedin.com/in/diwakar",
    )
    assert draft_service.violates_forbidden_phrases(body) is None
    assert "I'm interested in the" in body
    assert "Diwakar Jilakara" in body


def test_body_uses_application_language_not_gratitude_for_sharing():
    body = draft_service.generate_body(
        job_title="Data Engineer",
        job_location=None,
        recruiter_first_name="Naveen",
        top_skills=[],
        candidate_name="Diwakar",
        candidate_experience="8 years",
        candidate_work_auth="US Citizen",
        candidate_phone="555-000-0000",
        candidate_email="d@example.com",
        candidate_linkedin="linkedin.com/in/d",
    )
    lowered = body.lower()
    # a generic "thank you for your time" closing is fine - what must never
    # appear is gratitude that implies this is a reply to being contacted
    assert "thank you for sharing" not in lowered
    assert "thank you for reaching out" not in lowered
    assert "thank you for the opportunity" not in lowered
    assert "thanks for reaching out" not in lowered


def test_starts_with_greeting_accepts_a_clean_hi_opening():
    assert draft_service.starts_with_greeting("Hi,\n\nI came across...") is True


def test_starts_with_greeting_rejects_a_name_in_the_greeting():
    """The recruiter's name is never used - only a bare 'Hi,' passes."""
    assert draft_service.starts_with_greeting("Hi Naveen,\n\nI came across...") is False


def test_starts_with_greeting_rejects_a_meta_commentary_preamble():
    """The classic small-local-model habit: prepending a sentence about the
    rewrite instead of just writing the email."""
    assert draft_service.starts_with_greeting("Here is the rewritten email:\n\nHi Naveen,\n\n...") is False
    assert draft_service.starts_with_greeting("Sure, here's a revised version:\nHi Naveen,\n...") is False


def test_starts_with_greeting_rejects_empty_text():
    assert draft_service.starts_with_greeting("") is False


def test_generate_email_discards_ai_output_with_a_preamble_and_keeps_deterministic_body():
    ai_provider = _StubAIProvider(
        "Here is the rewritten email:\n\nHi Naveen,\n\nI came across the opportunity...\n\n"
        "Candidate Details:\nName: Diwakar Jilakara\n"
    )
    generated = draft_service.generate_email(ai_provider=ai_provider, **_generate_email_kwargs())

    # rejected for the preamble - the deterministic template is kept instead
    assert generated.body.startswith("Hi,")
    assert "Here is the rewritten" not in generated.body
    assert "I'm interested in the Senior Data Engineer opportunity in Remote" in generated.body


def test_generate_email_accepts_a_clean_ai_rewrite_that_starts_with_the_greeting():
    ai_provider = _StubAIProvider(
        "Hi,\n\nI came across the Senior Data Engineer role and would love to apply.\n\n"
        "Candidate Details:\nName: Diwakar Jilakara\nExperience: 8+ years\n"
        "Work Authorization: Authorized to work in the US\nPhone: 555-123-4567\n"
        "Email: diwakar@example.com\nLinkedIn: linkedin.com/in/diwakar\n\n"
        "Best regards,\nDiwakar Jilakara\n"
    )
    generated = draft_service.generate_email(ai_provider=ai_provider, **_generate_email_kwargs())

    assert generated.body.startswith("Hi,")
    assert "would love to apply" in generated.body  # the AI rewrite was actually used


def test_generate_email_discards_ai_output_that_names_the_recruiter():
    """Even if the AI ignores the constraint and greets the recruiter by
    name, that output must be discarded and the safe deterministic 'Hi,'
    template used instead - this is a hard validation gate, not just a
    prompt suggestion."""
    ai_provider = _StubAIProvider(
        "Hi Naveen,\n\nI came across the Senior Data Engineer role and would love to apply.\n\n"
        "Candidate Details:\nName: Diwakar Jilakara\n"
    )
    generated = draft_service.generate_email(ai_provider=ai_provider, **_generate_email_kwargs())

    assert generated.body.startswith("Hi,")
    assert "Naveen" not in generated.body
