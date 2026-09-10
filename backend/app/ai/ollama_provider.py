from __future__ import annotations

import json
from typing import Any

import httpx

from app.ai.base import AIProvider


class OllamaProvider(AIProvider):
    """Talks to a local Ollama server (http://localhost:11434 by default). Zero cost."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.1", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _generate_json(self, system: str, user: str) -> dict[str, Any]:
        resp = httpx.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "system": system,
                "prompt": user,
                "format": "json",
                "stream": False,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "{}")
        return json.loads(text)

    def classify_job_email(self, subject: str, body: str) -> dict[str, Any]:
        system = (
            "You classify emails as job/recruiter opportunity emails or not. "
            'Respond with strict JSON only: {"is_job_email": bool, "confidence": 0-1 float, "reason": str}.'
        )
        user = f"Subject: {subject}\n\nBody:\n{body}"
        return self._generate_json(system, user)

    def extract_job_details(self, text: str) -> dict[str, Any]:
        system = (
            "Extract structured job posting details from the email text. "
            'Respond with strict JSON only: {"job_title": str|null, "job_location": str|null, '
            '"requirements": [str]}. job_location must be copied verbatim from the text, never invented.'
        )
        return self._generate_json(system, text[:8000])

    def classify_interview_requirement(self, text: str) -> dict[str, Any]:
        system = (
            "Classify the interview arrangement described in this job/recruiter email. "
            "Distinguish job location/work-arrangement (onsite/hybrid/remote job) from the "
            "INTERVIEW arrangement itself - only classify IN_PERSON if the interview process "
            "clearly requires physical attendance. A hybrid or onsite JOB with a remote "
            "interview is REMOTE, not IN_PERSON. "
            'Respond with strict JSON only: {"interview_type": "IN_PERSON"|"REMOTE"|"HYBRID"|"UNKNOWN", '
            '"requires_in_person_interview": bool, "confidence": 0-1 float, "reason": str, '
            '"evidence": str|null}. evidence must be copied verbatim from the text, never invented.'
        )
        return self._generate_json(system, text[:8000])

    def evaluate_and_customize_resume(
        self, resume_text: str, jd_title: str, jd_text: str, candidate_existing_skills: list[str]
    ) -> dict[str, Any]:
        system = (
            "You are acting as BOTH a senior technical recruiter AND a senior data/software "
            "engineer, reviewing how well a candidate's resume matches a job description. "
            "Score the match honestly (0-100). Then write 2-3 short, professional resume bullet "
            "points that highlight ONLY the skills listed in candidate_existing_skills below - "
            "these are skills the candidate genuinely already has, just not prominently written "
            "into their resume text yet. You must NEVER invent, assume, or add any skill, "
            "technology, tool, certification, or experience that is not explicitly listed in "
            "candidate_existing_skills or already present in the resume text - doing so would be "
            "resume fraud. If candidate_existing_skills is empty, return an empty list. "
            'Respond with strict JSON only: {"match_score": 0-100 float, "match_explanation": '
            'str, "additional_points": [str]}.'
        )
        user = (
            f"Job Title: {jd_title}\n\nJob Description:\n{jd_text[:6000]}\n\n"
            f"Candidate's existing (but underemphasized) skills: {', '.join(candidate_existing_skills) or 'none'}\n\n"
            f"Resume text:\n{resume_text[:6000]}"
        )
        return self._generate_json(system, user)

    def polish_email_body(self, draft_body: str, constraints: list[str]) -> str:
        system = (
            "You lightly rewrite a job-application email to sound natural and concise, in plain "
            "text. You MUST preserve every constraint given. If unsure, return the text unchanged. "
            "Output ONLY the rewritten email itself, starting directly with 'Hi,' (never a name) - "
            "never add any preamble, commentary, explanation, or markdown before or after it."
        )
        constraint_text = "\n".join(f"- {c}" for c in constraints)
        user = f"Constraints (must never be violated):\n{constraint_text}\n\nDraft email:\n{draft_body}"
        resp = httpx.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "system": system, "prompt": user, "stream": False},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", draft_body).strip()
