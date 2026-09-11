"""
Interview-arrangement classification (sections 9-13 of the recruiter-CRM spec).

This is deliberately separate from job-location/work-arrangement classification:
a job can be onsite/hybrid/remote while the INTERVIEW itself is conducted
differently, and only a clearly-identified in-person/face-to-face/F2F INTERVIEW
requirement may ever trigger a skip. Every pattern below is anchored to an
interview-context word ("interview"/"round"/"call") specifically so that
job-attribute phrases like "onsite position" or "local candidates preferred"
never match on their own (section 12/22).

By explicit configuration choice, "onsite" wording is its own type (ONSITE)
and never skips - only literal in-person/face-to-face/F2F/physical-presence
wording does. This is deliberate: a lot of JD boilerplate says "onsite
interview" loosely (e.g. describing where the office is) without it actually
being a hard in-person requirement, so it is treated the same as HYBRID/
UNKNOWN - it proceeds through the normal pipeline instead of auto-skipping.

All proximity gaps use `[^.]{0,N}` (never `.{0,N}`) so a match can never span
across a sentence boundary - this is what correctly separates "Onsite
position. Interview conducted remotely." (two independent sentences, must NOT
be read as one signal) from "Final interview onsite." (one sentence, a single
real signal).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.base import AIProvider

INTERVIEW_TYPES = ("IN_PERSON", "ONSITE", "REMOTE", "HYBRID", "UNKNOWN")

# (compiled pattern, short human-readable description used in the `reason` field)
# IN_PERSON_PATTERNS trigger a skip - reserved strictly for literal
# in-person/face-to-face/F2F/physical-presence wording.
IN_PERSON_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bin[-\s]?person\b[^.]{0,60}\binterview", re.IGNORECASE), "in-person interview"),
    (re.compile(r"\binterview[^.]{0,60}\bin[-\s]?person\b", re.IGNORECASE), "in-person interview"),
    (re.compile(r"\bface[-\s]to[-\s]face\b[^.]{0,60}\binterview", re.IGNORECASE), "face-to-face interview"),
    (re.compile(r"\binterview[^.]{0,60}\bface[-\s]to[-\s]face\b", re.IGNORECASE), "face-to-face interview"),
    # "F2F" is a very common recruiter-email abbreviation for face-to-face -
    # word-boundary anchored so it never matches inside an unrelated token.
    (re.compile(r"\bf2f\b[^.]{0,60}\binterview", re.IGNORECASE), "F2F (face-to-face) interview"),
    (re.compile(r"\binterview[^.]{0,60}\bf2f\b", re.IGNORECASE), "F2F (face-to-face) interview"),
    (
        re.compile(r"\bfinal\s+(interview|round)\b[^.]{0,80}\bin[-\s]?person\b", re.IGNORECASE),
        "final interview/round requires in-person attendance",
    ),
    (
        re.compile(r"\bin[-\s]?person\b[^.]{0,80}\bfinal\s+(interview|round)\b", re.IGNORECASE),
        "final interview/round requires in-person attendance",
    ),
    (
        re.compile(r"\bphysical(ly)?\s+presen(t|ce)\b[^.]{0,60}\binterview\b", re.IGNORECASE),
        "physical presence required for interview",
    ),
    (
        re.compile(r"\binterview\b[^.]{0,60}\bphysical(ly)?\s+presen(t|ce)\b", re.IGNORECASE),
        "physical presence required for interview",
    ),
]

# ONSITE_PATTERNS never skip (same treatment as HYBRID/UNKNOWN) - "onsite"
# wording, including "at the office/client location," is common JD boilerplate
# and is not treated as a hard in-person requirement on its own.
ONSITE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bon[-\s]?site\b[^.]{0,60}\binterview", re.IGNORECASE), "onsite interview"),
    (re.compile(r"\binterview[^.]{0,60}\bon[-\s]?site\b", re.IGNORECASE), "onsite interview"),
    (
        re.compile(r"\bfinal\s+(interview|round)\b[^.]{0,80}\b(onsite|on-site)\b", re.IGNORECASE),
        "final interview/round requires onsite attendance",
    ),
    (
        re.compile(r"\b(onsite|on-site)\b[^.]{0,80}\bfinal\s+(interview|round)\b", re.IGNORECASE),
        "final interview/round requires onsite attendance",
    ),
    (
        re.compile(
            r"\b(final\s+(interview|round)|interview)\b[^.]{0,60}\bat\s+(the\s+|our\s+)?(client'?s?\s+)?(office|location)\b",
            re.IGNORECASE,
        ),
        "interview held at an office/client location",
    ),
    (
        re.compile(
            r"\bmust\s+(interview|attend|come|be\s+available)\b[^.]{0,60}\bon[-\s]?site\b", re.IGNORECASE
        ),
        "candidate must attend onsite",
    ),
    (re.compile(r"\brequired\s+to\s+interview\s+on[-\s]?site\b", re.IGNORECASE), "required to interview onsite"),
    (
        re.compile(r"\blocal\s+candidates?\s+required\s+for\s+interview\b", re.IGNORECASE),
        "local candidates required for interview",
    ),
    (re.compile(r"\bcandidates?\s+must\s+come\s+on[-\s]?site\b", re.IGNORECASE), "candidates must come onsite"),
]

REMOTE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"\b(interview|round|call)s?\b[^.]{0,60}\b(zoom|(?:microsoft\s+)?teams|google\s+meet|skype|webex)\b",
            re.IGNORECASE,
        ),
        "interview conducted over a video call platform",
    ),
    (
        re.compile(
            r"\b(zoom|(?:microsoft\s+)?teams|google\s+meet|skype|webex)\b[^.]{0,60}\b(interview|round|call)s?\b",
            re.IGNORECASE,
        ),
        "interview conducted over a video call platform",
    ),
    (
        re.compile(r"\b(interview|round)s?\b[^.]{0,60}\b(virtual(ly)?|remote(ly)?|video\s*call)\b", re.IGNORECASE),
        "interview conducted virtually/remotely",
    ),
    (
        re.compile(r"\b(virtual(ly)?|remote(ly)?)\b[^.]{0,60}\b(interview|round)s?\b", re.IGNORECASE),
        "interview conducted virtually/remotely",
    ),
    (re.compile(r"\bphone\s+interview\b", re.IGNORECASE), "phone interview"),
]

_MENTIONS_INTERVIEW_RE = re.compile(r"\binterview", re.IGNORECASE)

# An AI-claimed IN_PERSON evidence excerpt must itself be ABOUT the interview -
# never just a job-location/work-arrangement line like "Location: Charlotte,
# NC - Onsite" (real bug: that phrase genuinely appears in the source text,
# so the plain "evidence appears in the text" check alone let it through even
# though it says nothing about the interview at all). Same anchor words the
# deterministic patterns above already require.
_INTERVIEW_CONTEXT_WORD_RE = re.compile(r"\b(interview|round|call)s?\b", re.IGNORECASE)


@dataclass
class InterviewClassification:
    interview_type: str  # one of INTERVIEW_TYPES
    requires_in_person_interview: bool
    confidence: float
    reason: str
    evidence: str | None = None
    source: str = "deterministic"


def _clean_evidence(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()


def _deterministic_classify(text: str) -> InterviewClassification:
    if not text or not text.strip():
        return InterviewClassification(
            "UNKNOWN", False, 0.3, "No interview information found in the message"
        )

    for pattern, description in IN_PERSON_PATTERNS:
        m = pattern.search(text)
        if m:
            return InterviewClassification(
                "IN_PERSON", True, 0.96,
                f"In-person interview requirement detected: {description}",
                _clean_evidence(m.group(0)),
            )

    for pattern, description in ONSITE_PATTERNS:
        m = pattern.search(text)
        if m:
            return InterviewClassification(
                "ONSITE", False, 0.9,
                f"Onsite interview mentioned (does not skip): {description}",
                _clean_evidence(m.group(0)),
            )

    for pattern, description in REMOTE_PATTERNS:
        m = pattern.search(text)
        if m:
            return InterviewClassification(
                "REMOTE", False, 0.95,
                f"Remote interview format detected: {description}",
                _clean_evidence(m.group(0)),
            )

    if _MENTIONS_INTERVIEW_RE.search(text):
        return InterviewClassification(
            "UNKNOWN", False, 0.52, "Interview is mentioned but the format is not specified"
        )

    return InterviewClassification("UNKNOWN", False, 0.3, "No interview information found in the message")


def classify_interview_requirement(
    text: str, ai_provider: AIProvider | None = None
) -> InterviewClassification:
    """
    Deterministic rules run first and are trusted outright whenever they find
    a match (IN_PERSON or REMOTE) - AI is only ever consulted per section 11
    ("use AI classification when necessary") when the deterministic pass comes
    back UNKNOWN, and even then its output is validated before being trusted:
    an invalid/malformed AI response, or one that claims IN_PERSON without a
    supporting `evidence` string actually present in the source text AND
    itself about the interview (not just the job's location - see
    _INTERVIEW_CONTEXT_WORD_RE), is discarded in favor of the safe
    deterministic UNKNOWN result. UNKNOWN is never auto-escalated to
    IN_PERSON (section 13).
    """
    deterministic = _deterministic_classify(text)
    if deterministic.interview_type != "UNKNOWN" or ai_provider is None:
        return deterministic

    try:
        ai_result = ai_provider.classify_interview_requirement(text[:8000])
        ai_type = ai_result.get("interview_type")
        if ai_type not in INTERVIEW_TYPES:
            return deterministic

        ai_requires_in_person = bool(ai_result.get("requires_in_person_interview", False))
        if ai_type == "IN_PERSON" and not ai_requires_in_person:
            return deterministic  # internally inconsistent AI response - don't trust it

        ai_evidence = ai_result.get("evidence")
        if ai_type == "IN_PERSON":
            # never let AI trigger a skip without a real, quotable excerpt
            # from the actual source text backing it up, AND that excerpt
            # must itself be about the interview - a job-location/work-
            # arrangement statement alone (e.g. "Location: Charlotte, NC -
            # Onsite") is never sufficient, even though it genuinely appears
            # verbatim in the text.
            if (
                not ai_evidence
                or ai_evidence.lower() not in text.lower()
                or not _INTERVIEW_CONTEXT_WORD_RE.search(ai_evidence)
            ):
                return deterministic

        confidence = float(ai_result.get("confidence", 0.6))
        return InterviewClassification(
            interview_type=ai_type,
            requires_in_person_interview=ai_requires_in_person,
            confidence=confidence,
            reason=str(ai_result.get("reason", "AI-classified interview arrangement")),
            evidence=ai_evidence,
            source="ai",
        )
    except Exception:
        return deterministic
