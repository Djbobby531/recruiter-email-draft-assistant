"""
AIProvider abstraction. Every AI call in the app goes through this interface so
swapping OpenAI <-> Ollama <-> "none" is a one-line config change (AI_PROVIDER=...).

AI is used ONLY for the ambiguous/generative pieces:
  - classify_job_email               (ambiguous-band job/recruiter classification)
  - extract_job_details              (fallback when deterministic regex extraction is weak)
  - polish_email_body                (optional light rewrite of the deterministic template)
  - classify_interview_requirement   (fallback when deterministic interview-arrangement rules are UNKNOWN)
  - evaluate_and_customize_resume    (optional resume match scoring + truthful, format-preserving customization)

AI is NEVER used for: sender/CC assignment, reply detection, duplicate IDs,
Gmail draft creation, file attachment, or subject-line formatting - those are
deterministic per the product spec.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIProvider(ABC):
    @abstractmethod
    def classify_job_email(self, subject: str, body: str) -> dict[str, Any]:
        """Return {"is_job_email": bool, "confidence": float, "reason": str}."""

    @abstractmethod
    def extract_job_details(self, text: str) -> dict[str, Any]:
        """Return {"job_title": str|None, "job_location": str|None, "requirements": [str]}."""

    @abstractmethod
    def polish_email_body(self, draft_body: str, constraints: list[str]) -> str:
        """
        Optionally improve the naturalness of a deterministic draft body.
        `constraints` is a list of hard rules (e.g. "never mention a location",
        "never thank the recruiter for sending the opportunity") the rewrite MUST
        preserve. Implementations should return draft_body unchanged if unsure.
        """

    @abstractmethod
    def classify_interview_requirement(self, text: str) -> dict[str, Any]:
        """
        Only ever called when deterministic interview-arrangement rules
        (app.services.interview_classifier) come back UNKNOWN. Return
        {"interview_type": "IN_PERSON"|"REMOTE"|"HYBRID"|"UNKNOWN",
         "requires_in_person_interview": bool, "confidence": float,
         "reason": str, "evidence": str|None}. The caller independently
        verifies any IN_PERSON claim's `evidence` actually appears in the
        source text before trusting it - never invent evidence.
        """

    @abstractmethod
    def evaluate_and_customize_resume(
        self, resume_text: str, jd_title: str, jd_text: str, candidate_existing_skills: list[str]
    ) -> dict[str, Any]:
        """
        Acting as a senior technical recruiter AND a senior data/software
        engineer, score how well this resume matches the JD, and phrase a
        handful of resume bullet points highlighting `candidate_existing_skills`
        - skills the candidate already has per their own resume metadata but
        that don't show up prominently in the resume's prose. This must NEVER
        invent a skill, technology, or experience beyond what's listed in
        `candidate_existing_skills` or already present in `resume_text` - the
        caller independently re-validates every returned point against that
        list before trusting it. Return {"match_score": 0-100 float,
        "match_explanation": str, "additional_points": [str]}.
        """
