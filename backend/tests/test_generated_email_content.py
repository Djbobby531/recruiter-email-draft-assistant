"""
Section 12: generated email content - what it MUST say and MUST NOT say,
using fake candidate profiles and fake JDs.
"""
from __future__ import annotations

from app.services import draft_service

FORBIDDEN_PHRASES = [
    "thank you for sharing the opportunity",
    "thanks for reaching out",
    "thank you for sending this opportunity",
    "thank you for contacting me",
]


def _generate(job_title="Senior Data Engineer (Databricks)", job_location=None, recruiter_first_name="Naveen",
              top_skills=None, name="Diwakar Jilakara", experience="8+ years",
              work_auth="Authorized to work in the US", phone="555-123-4567",
              email="diwakar@example.com", linkedin="linkedin.com/in/diwakar"):
    return draft_service.generate_body(
        job_title=job_title, job_location=job_location, recruiter_first_name=recruiter_first_name,
        top_skills=top_skills or ["databricks", "python", "sql"],
        candidate_name=name, candidate_experience=experience,
        candidate_work_auth=work_auth, candidate_phone=phone,
        candidate_email=email, candidate_linkedin=linkedin,
    )


def test_body_is_concise():
    body = _generate()
    # a concise application email should not sprawl past a handful of short paragraphs
    assert len(body.splitlines()) < 25
    assert len(body) < 1500


def test_body_uses_application_language():
    body = _generate()
    assert "I'm interested in the" in body
    assert "opportunity" in body


def test_body_reads_as_a_complete_email_through_to_the_signature():
    """The body should feel like a fuller, complete email - not just a
    couple of terse sentences - and must always run all the way through to
    a proper sign-off, never trailing off early."""
    body = _generate()
    assert draft_service.DEFAULT_CAREER_FOCUS_SENTENCE in body
    assert draft_service.DEFAULT_CLOSING_APPRECIATION in body
    assert body.rstrip().endswith("Diwakar Jilakara")
    assert body.rstrip().splitlines()[-2] == "Best regards,"
    # meaningfully longer than a bare-bones skeleton, without becoming a wall of text
    assert len(body.splitlines()) >= 15
    assert 800 < len(body) < 1800


def test_body_mentions_job_location_when_known():
    body = _generate(job_location="Bellevue, WA")
    assert "Bellevue, WA" in body


def test_body_omits_location_clause_when_job_location_unknown():
    body = _generate(job_location=None)
    assert " in None" not in body


def test_body_mentions_relevant_jd_skills():
    body = _generate(top_skills=["databricks", "python", "sql", "airflow"])
    assert "Databricks" in body
    assert "Python" in body


def test_body_includes_all_candidate_detail_fields():
    body = _generate(name="Jane Doe", experience="10 years", work_auth="H1B",
                      phone="111-222-3333", email="jane@example.com", linkedin="linkedin.com/in/jane")
    assert "Name: Jane Doe" in body
    assert "Experience: 10 years" in body
    assert "Work Authorization: H1B" in body
    assert "Phone: 111-222-3333" in body
    assert "Email: jane@example.com" in body


def test_body_expands_bare_experience_figures_with_the_word_years():
    """The candidate profile may store a bare figure like '10+' - the body
    must read naturally ('10+ years of experience'), not '10+ of experience'."""
    body = _generate(experience="10+")
    assert "10+ years of experience" in body
    assert "Experience: 10+ years" in body


def test_body_does_not_double_up_years_when_already_present():
    body = _generate(experience="8+ years")
    assert "8+ years years" not in body


def test_body_phrases_a_bare_visa_type_as_authorized():
    body = _generate(work_auth="H1B")
    assert "I'm H1B authorized and available" in body


def test_body_leaves_a_descriptive_work_auth_phrase_unchanged():
    body = _generate(work_auth="Authorized to work in the US")
    assert "I'm Authorized to work in the US and available" in body


def test_body_mentions_attached_resume():
    body = _generate()
    assert "resume attached" in body.lower()


def test_body_never_addresses_the_recruiter_by_name():
    """The greeting is always a plain 'Hi,' - the recruiter's name is never
    used, even when a confident one is available, since it's often
    unreliable (pulled from a forwarded signature, a generic alias, etc.)."""
    body = _generate(recruiter_first_name="Naveen")
    assert body.startswith("Hi,")
    assert "Naveen" not in body


def test_body_greeting_is_identical_regardless_of_recruiter_name_input():
    with_name = _generate(recruiter_first_name="Naveen")
    without_name = _generate(recruiter_first_name="there")
    assert with_name == without_name


def test_body_never_contains_any_forbidden_gratitude_phrase():
    body = _generate()
    lowered = body.lower()
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in lowered, f"forbidden phrase found: {phrase!r}"


def test_body_does_not_claim_unsupported_experience():
    # the generator only ever echoes the candidate profile fields it was given -
    # it must never append extra claims (certifications, employers, years) that
    # weren't part of the input.
    body = _generate(experience="3 years")
    assert "10+ years" not in body
    assert "certified" not in body.lower()


def test_body_does_not_invent_skills_beyond_top_skills_list():
    body = _generate(top_skills=["python"])
    # only what was passed in should appear in the skills sentence
    assert "Kubernetes" not in body
    assert "Java" not in body


def test_body_does_not_invent_employers():
    body = _generate()
    for fake_employer in ["Google", "Amazon", "Microsoft", "Meta"]:
        assert fake_employer not in body


def test_body_does_not_invent_certifications():
    body = _generate()
    for cert in ["AWS Certified", "PMP Certified", "Certified Scrum Master"]:
        assert cert not in body


def test_violates_forbidden_phrases_detector_catches_all_listed_phrases():
    for phrase in FORBIDDEN_PHRASES:
        text = f"Hi Naveen,\n\n{phrase.capitalize()}. Looking forward to it.\n"
        assert draft_service.violates_forbidden_phrases(text) is not None
