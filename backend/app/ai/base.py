"""
AIProvider abstraction. Every AI call in the app goes through this interface so
swapping OpenAI <-> Ollama <-> "none" is a one-line config change (AI_PROVIDER=...).

AI is used ONLY for the ambiguous/generative pieces:
  - classify_job_email                    (ambiguous-band job/recruiter classification)
  - extract_job_details                   (fallback when deterministic regex extraction is weak)
  - polish_email_body                     (optional light rewrite of the deterministic template)
  - classify_interview_requirement        (fallback when deterministic interview-arrangement rules are UNKNOWN)
  - generate_resume_customization_plan    (optional structured content plan for resume customization -
                                            the resume matcher, NOT this, still picks WHICH resume is used)

AI is NEVER used for: sender/CC assignment, reply detection, duplicate IDs,
Gmail draft creation, file attachment, or subject-line formatting - those are
deterministic per the product spec.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# Shared by every provider's generate_resume_customization_plan() so the
# truthfulness rules are worded identically regardless of AI_PROVIDER.
RESUME_CUSTOMIZATION_PLAN_SYSTEM_PROMPT = (
    "You are acting as a senior data engineer, a senior technical recruiter, and a senior "
    "resume optimization specialist. Analyze the job description against the candidate's "
    "EXISTING resume and the APPROVED candidate information provided to you. Your task is to "
    "produce a structured customization PLAN ONLY - you never write or rewrite the document "
    "itself, only decide what content should change.\n\n"
    "ABSOLUTE RULES:\n"
    "- You may only surface, rephrase, or reference skills, technologies, tools, and facts that "
    "are explicitly present in the APPROVED candidate skills list or the existing resume text "
    "given to you. Never invent a skill, technology, tool, certification, employer, project, "
    "responsibility, achievement, metric, percentage, team size, customer name, date, or scope.\n"
    "- Never assume the candidate knows a technology merely because the job description asks "
    "for it - if it is not in the approved list or resume text, leave it out entirely.\n"
    "- header_role: the job title that should replace the resume's CURRENT header role, based "
    "on the JD's role - do not change anything else about the header.\n"
    "- summary_points: at most 2 short, complete, senior-level, JD-aligned sentences using only "
    "verified information - never a large paragraph, never fabricated metrics.\n"
    "- skills_to_add: only skills that are in the approved skills list AND are relevant to this "
    "JD AND are not already prominent in the resume - for each, name the existing resume skill "
    "category (from resume_structure) it belongs in.\n"
    "- experience_updates: at most 2 concise, senior-level, action-oriented bullets per relevant "
    "EXISTING experience (identified by experience_identifier, which must be one of the approved "
    "experience identifiers given to you) - never rewrite or remove existing bullets, never "
    "reference an experience not in the approved list, never claim leadership scope, team size, "
    "or business impact that isn't already verified.\n"
    "- Do not attempt to use every JD keyword - natural, credible resume quality matters more "
    "than keyword density.\n\n"
    "Return STRICT JSON ONLY - no markdown, no code fences, no explanation, no text outside the "
    'JSON object. Shape: {"jd_role": str, "header_role": str, "summary_points": [str], '
    '"skills_to_add": [{"skill": str, "category": str, "reason": str}], '
    '"experience_updates": [{"experience_identifier": str, "points": [str]}]}. '
    "Use empty lists for any field with nothing safe to add - never pad with unsupported content."
)


# Shared by every provider's generate_email_skills_pitch() - worded with a
# concrete negative example (not just an abstract rule) because a small
# local model reliably echoes a JD's own emphasized terms (e.g. "CI/CD",
# "Kubernetes") back into its sentence even when told not to invent
# technologies - naming the exact failure mode measurably cuts how often
# the caller's independent validation has to reject the whole pitch.
EMAIL_SKILLS_PITCH_SYSTEM_PROMPT = (
    "You write ONE to two short, natural sentences for a job-application email, connecting "
    "the candidate's skills to what THIS SPECIFIC job description emphasizes.\n\n"
    "The 'Candidate's verified skills' list given to you is the COMPLETE and ONLY set of "
    "skills/technologies/tools/methodologies you may mention - copy them verbatim, do not "
    "pluralize, abbreviate, or expand them into a related term. This is true even when the "
    "job description repeatedly emphasizes something else: if the job description talks "
    "about CI/CD, Kubernetes, Docker, testing frameworks, certifications, or any other tool "
    "or methodology that is NOT itself in the verified skills list, do not mention it, not "
    "even in passing, and do not describe the candidate as experienced with it. Only use the "
    "job description to decide which of the ALREADY-VERIFIED skills to lead with and how to "
    "frame them - never to introduce a new one.\n\n"
    "Never mention a specific employer, project name, team size, or metric not given to you. "
    "Never use hedging language ('may have', 'could apply', 'likely', 'possibly'). Output "
    "ONLY the sentence(s) themselves, in plain text - no preamble, quotes, or markdown."
)


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
    def generate_resume_customization_plan(
        self,
        resume_text: str,
        resume_structure: dict[str, Any],
        jd_title: str,
        jd_text: str,
        jd_requirements: list[str],
        approved_skills: list[str],
        approved_experience_identifiers: list[str],
    ) -> dict[str, Any]:
        """
        Acting as a senior data engineer, senior technical recruiter, and
        senior resume optimization specialist, produce a STRUCTURED EDITING
        PLAN ONLY - never the document itself. `resume_structure` describes
        what this resume ALREADY has (current header role, its existing skill
        categories with their current items, its existing experience
        identifiers, whether it has a summary section) so the plan only ever
        references things that genuinely exist in this resume.

        This must NEVER invent a skill, technology, tool, certification,
        employer, project, responsibility, achievement, metric, percentage,
        team size, customer name, date, or scope beyond what's listed in
        `approved_skills` / `approved_experience_identifiers` or already
        verifiably present in `resume_text` - the caller independently
        re-validates every field before trusting it, and discards anything
        that isn't backed by verified candidate information.

        Return strict JSON only, shaped like:
        {"jd_role": str, "header_role": str,
         "summary_points": [str, ...],
         "skills_to_add": [{"skill": str, "category": str, "reason": str}, ...],
         "experience_updates": [{"experience_identifier": str, "points": [str, ...]}, ...]}
        """

    @abstractmethod
    def generate_email_skills_pitch(
        self, job_title: str, jd_text: str, top_skills: list[str], candidate_experience: str,
    ) -> str:
        """
        Writes ONE-to-two natural sentences for the application email,
        connecting the candidate's already-verified `top_skills` to what
        this specific job description actually emphasizes - replacing the
        deterministic template's generic "expertise in X, Y, Z" list with
        something that reads as genuinely tailored to this JD, not the same
        shape for every email. May ONLY mention skills/technologies present
        in `top_skills` - never invent one, even if the JD asks for it. Must
        never mention a specific employer, project name, team size, or
        metric not given here. Output ONLY the sentence(s) themselves, no
        preamble/quotes/markdown. The caller independently re-validates the
        result (hedge language, unapproved-technology mentions, length) and
        falls back to the deterministic template on any doubt.
        """
