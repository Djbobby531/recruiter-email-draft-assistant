"""
I1 .. I25 plus the false-positive list (sections 20-22 of the recruiter-CRM
hardening spec): interview-arrangement classification. This is the single
highest-stakes new classifier in this phase - it gates whether a draft is
ever created at all - so it gets the widest test coverage.
"""
from __future__ import annotations

import pytest

from app.services.interview_classifier import classify_interview_requirement

IN_PERSON_CASES = [
    ("I1", "Interview will be conducted in person."),
    ("I5", "Face-to-face interview required."),
    ("I10", "Physical presence required for the interview."),
    ("I26", "GCP Data Engineer Only Local Consultant F2F Interview HYBRID"),
    ("I27", "Interview Type: F2F"),
    ("I28", "Final interview must be in-person."),
    ("I29", "In-person final round required."),
]

# By explicit configuration choice, "onsite" wording never skips - only
# literal in-person/face-to-face/F2F wording does (see interview_classifier
# module docstring). These must classify as ONSITE and NOT skip.
ONSITE_CASES = [
    ("I2", "Final interview is onsite."),
    ("I3", "Candidates must attend an onsite interview."),
    ("I4", "Interview at the client office."),
    ("I6", "Final round will be conducted at the office."),
    ("I7", "Must be available for onsite interviews."),
    ("I8", "Interview at client location."),
    ("I9", "Onsite final interview required."),
]

REMOTE_CASES = [
    ("I11", "Interview via Zoom."),
    ("I12", "Interview via Microsoft Teams."),
    ("I13", "All interviews are virtual."),
    ("I14", "Initial and final rounds will be remote."),
    ("I15", "Interview will be conducted over Google Meet."),
]

HYBRID_JOB_REMOTE_INTERVIEW_CASES = [
    ("I16", "Onsite position. Interview conducted remotely."),
    ("I17", "Hybrid role. All interviews are virtual."),
    ("I18", "Local candidate preferred. Interviews via Zoom."),
]

HYBRID_JOB_IN_PERSON_INTERVIEW_CASES = [
    ("I19", "Hybrid position. Final interview must be in-person."),
    ("I20", "Remote position but final interview must be face-to-face."),
]

# Same idea, but with "onsite" wording specifically - these must NOT skip.
HYBRID_JOB_ONSITE_INTERVIEW_CASES = [
    ("I30", "Hybrid position. Final interview onsite."),
    ("I31", "Remote position but final interview must be onsite."),
]

AMBIGUOUS_CASES = [
    ("I21", "Interview details will be discussed later."),
    ("I22", "Interview process includes multiple rounds."),
    ("I23", "Interview format not specified."),
]

NO_SOLE_TRIGGER_CASES = [
    ("I24", "Local candidates preferred."),
    ("I25", "Onsite job."),
]

FALSE_POSITIVE_CASES = [
    "Local candidates preferred",
    "Must be local",
    "Onsite position",
    "Hybrid position",
    "Work from office three days per week",
    "Client-facing role",
    "Location: Irvine, CA",
    "Candidates within commuting distance preferred",
    "Please F2F this requirement to your network",  # "F2F" used as a verb, no "interview" nearby
]


@pytest.mark.parametrize("case_id,text", IN_PERSON_CASES, ids=[c[0] for c in IN_PERSON_CASES])
def test_in_person_cases_skip(case_id, text):
    result = classify_interview_requirement(text)
    assert result.interview_type == "IN_PERSON", f"{case_id}: {text!r}"
    assert result.requires_in_person_interview is True, f"{case_id}: {text!r}"
    assert result.evidence is not None


@pytest.mark.parametrize("case_id,text", ONSITE_CASES, ids=[c[0] for c in ONSITE_CASES])
def test_onsite_cases_never_skip(case_id, text):
    """Onsite wording is recognized but, by explicit configuration choice,
    never skips - only literal in-person/face-to-face/F2F wording does."""
    result = classify_interview_requirement(text)
    assert result.interview_type == "ONSITE", f"{case_id}: {text!r}"
    assert result.requires_in_person_interview is False, f"{case_id}: {text!r}"
    assert result.evidence is not None


@pytest.mark.parametrize("case_id,text", REMOTE_CASES, ids=[c[0] for c in REMOTE_CASES])
def test_remote_cases_process(case_id, text):
    result = classify_interview_requirement(text)
    assert result.interview_type == "REMOTE", f"{case_id}: {text!r}"
    assert result.requires_in_person_interview is False, f"{case_id}: {text!r}"


@pytest.mark.parametrize(
    "case_id,text", HYBRID_JOB_REMOTE_INTERVIEW_CASES, ids=[c[0] for c in HYBRID_JOB_REMOTE_INTERVIEW_CASES]
)
def test_onsite_or_hybrid_job_with_remote_interview_processes(case_id, text):
    """The JOB can be onsite/hybrid while the INTERVIEW is remote - must not skip."""
    result = classify_interview_requirement(text)
    assert result.requires_in_person_interview is False, f"{case_id}: {text!r}"
    assert result.interview_type == "REMOTE", f"{case_id}: {text!r}"


@pytest.mark.parametrize(
    "case_id,text", HYBRID_JOB_IN_PERSON_INTERVIEW_CASES, ids=[c[0] for c in HYBRID_JOB_IN_PERSON_INTERVIEW_CASES]
)
def test_hybrid_or_remote_job_with_in_person_final_round_skips(case_id, text):
    """Even a nominally hybrid/remote JOB must skip if the interview itself
    explicitly requires an in-person/face-to-face round."""
    result = classify_interview_requirement(text)
    assert result.requires_in_person_interview is True, f"{case_id}: {text!r}"
    assert result.interview_type == "IN_PERSON", f"{case_id}: {text!r}"


@pytest.mark.parametrize(
    "case_id,text", HYBRID_JOB_ONSITE_INTERVIEW_CASES, ids=[c[0] for c in HYBRID_JOB_ONSITE_INTERVIEW_CASES]
)
def test_hybrid_or_remote_job_with_onsite_final_round_never_skips(case_id, text):
    """Same shape, but with "onsite" wording - must NOT skip."""
    result = classify_interview_requirement(text)
    assert result.requires_in_person_interview is False, f"{case_id}: {text!r}"
    assert result.interview_type == "ONSITE", f"{case_id}: {text!r}"


@pytest.mark.parametrize("case_id,text", AMBIGUOUS_CASES, ids=[c[0] for c in AMBIGUOUS_CASES])
def test_ambiguous_cases_are_unknown_and_do_not_skip(case_id, text):
    result = classify_interview_requirement(text)
    assert result.interview_type == "UNKNOWN", f"{case_id}: {text!r}"
    assert result.requires_in_person_interview is False, f"{case_id}: {text!r}"


@pytest.mark.parametrize("case_id,text", NO_SOLE_TRIGGER_CASES, ids=[c[0] for c in NO_SOLE_TRIGGER_CASES])
def test_job_attribute_alone_never_triggers_skip(case_id, text):
    result = classify_interview_requirement(text)
    assert result.requires_in_person_interview is False, f"{case_id}: {text!r}"


@pytest.mark.parametrize("text", FALSE_POSITIVE_CASES)
def test_false_positive_phrases_never_trigger_in_person_skip(text):
    result = classify_interview_requirement(text)
    assert result.requires_in_person_interview is False, f"false positive: {text!r}"
    assert result.interview_type != "IN_PERSON", f"false positive: {text!r}"


def test_unknown_confidence_is_never_treated_as_in_person():
    result = classify_interview_requirement("")
    assert result.interview_type == "UNKNOWN"
    assert result.requires_in_person_interview is False
    assert result.confidence < 0.6


def test_evidence_is_a_real_excerpt_from_the_source_text():
    text = "We are hiring. Candidates must attend an onsite interview for this role."
    result = classify_interview_requirement(text)
    assert result.evidence is not None
    assert result.evidence.lower() in text.lower()


def test_mixed_remote_first_round_and_onsite_final_round_never_skips():
    """"Onsite" wording never skips, even when it's the final round and other
    rounds are explicitly remote - only literal in-person/face-to-face/F2F
    wording does (see test_mixed_remote_first_round_and_in_person_final_round_
    is_in_person below for that case)."""
    text = (
        "First round will be conducted via Zoom and final interview will be "
        "conducted onsite at the client office."
    )
    result = classify_interview_requirement(text)
    assert result.interview_type == "ONSITE"
    assert result.requires_in_person_interview is False


def test_mixed_remote_first_round_and_in_person_final_round_is_in_person():
    """Matches the spec's canonical end-to-end example, but with literal
    in-person wording: any required in-person round anywhere in the process
    should trigger the skip, even if other rounds are explicitly remote."""
    text = (
        "First round will be conducted via Zoom and final interview will be "
        "conducted in-person at the client office."
    )
    result = classify_interview_requirement(text)
    assert result.interview_type == "IN_PERSON"
    assert result.requires_in_person_interview is True


def test_classification_output_has_the_documented_shape():
    result = classify_interview_requirement("Final interview must be conducted in-person at the client office.")
    assert result.interview_type in ("IN_PERSON", "ONSITE", "REMOTE", "HYBRID", "UNKNOWN")
    assert isinstance(result.requires_in_person_interview, bool)
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.reason, str) and result.reason


# --- AI fallback (only ever consulted when the deterministic pass is UNKNOWN) ---

class _StubAI:
    def __init__(self, response):
        self.response = response
        self.called = False

    def classify_interview_requirement(self, text):
        self.called = True
        return self.response


def test_ai_is_never_consulted_when_deterministic_rules_already_matched():
    ai = _StubAI({"interview_type": "REMOTE", "requires_in_person_interview": False, "confidence": 0.9, "reason": "x"})
    classify_interview_requirement("Candidates must attend an onsite interview.", ai_provider=ai)
    assert ai.called is False


def test_ai_fallback_used_when_deterministic_result_is_unknown():
    ai = _StubAI({
        "interview_type": "IN_PERSON", "requires_in_person_interview": True, "confidence": 0.8,
        "reason": "AI detected onsite requirement", "evidence": "come to headquarters for the interview",
    })
    text = "Interview process includes multiple rounds, come to headquarters for the interview."
    result = classify_interview_requirement(text, ai_provider=ai)
    assert ai.called is True
    assert result.interview_type == "IN_PERSON"
    assert result.source == "ai"


def test_ai_in_person_claim_without_real_evidence_in_text_is_discarded():
    """The AI must never be able to trigger a skip based on fabricated evidence
    that doesn't actually appear in the source text."""
    ai = _StubAI({
        "interview_type": "IN_PERSON", "requires_in_person_interview": True, "confidence": 0.9,
        "reason": "fabricated", "evidence": "this exact phrase is not in the source text at all",
    })
    text = "Interview process includes multiple rounds."
    result = classify_interview_requirement(text, ai_provider=ai)
    assert result.interview_type == "UNKNOWN"  # deterministic fallback wins
    assert result.source == "deterministic"


def test_ai_internally_inconsistent_response_is_discarded():
    """interview_type=IN_PERSON but requires_in_person_interview=False is
    self-contradictory - never trust it."""
    ai = _StubAI({
        "interview_type": "IN_PERSON", "requires_in_person_interview": False,
        "confidence": 0.8, "reason": "inconsistent",
    })
    result = classify_interview_requirement("Interview format not specified.", ai_provider=ai)
    assert result.source == "deterministic"


def test_ai_invalid_interview_type_is_discarded():
    ai = _StubAI({"interview_type": "SOMETHING_INVALID", "requires_in_person_interview": True, "confidence": 0.9, "reason": "x"})
    result = classify_interview_requirement("Interview format not specified.", ai_provider=ai)
    assert result.source == "deterministic"


def test_ai_provider_raising_an_exception_falls_back_to_deterministic():
    class RaisingAI:
        def classify_interview_requirement(self, text):
            raise ConnectionError("AI provider unreachable")

    result = classify_interview_requirement("Interview format not specified.", ai_provider=RaisingAI())
    assert result.interview_type == "UNKNOWN"
    assert result.source == "deterministic"


def test_ai_remote_classification_accepted_without_needing_evidence_check():
    """The stricter evidence-must-appear-in-text check only applies to
    IN_PERSON claims (since only those can trigger a skip) - a REMOTE AI
    classification is accepted more leniently."""
    ai = _StubAI({
        "interview_type": "REMOTE", "requires_in_person_interview": False,
        "confidence": 0.85, "reason": "AI inferred a remote process", "evidence": None,
    })
    result = classify_interview_requirement("Interview process includes multiple rounds.", ai_provider=ai)
    assert result.interview_type == "REMOTE"
    assert result.source == "ai"
