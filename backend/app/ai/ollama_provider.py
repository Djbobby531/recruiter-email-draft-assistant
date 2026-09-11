from __future__ import annotations

import json
from typing import Any

import httpx

from app.ai.base import EMAIL_SKILLS_PITCH_SYSTEM_PROMPT, RESUME_CUSTOMIZATION_PLAN_SYSTEM_PROMPT, AIProvider


class OllamaProvider(AIProvider):
    """Talks to a local Ollama server (http://localhost:11434 by default). Zero cost."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1",
        timeout: float = 60.0,
        resume_timeout: float | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # The resume-customization prompt (full resume text + JD text + the
        # existing structure summary, asking for a much longer structured
        # JSON response) is far heavier than the other short classification
        # calls - the general `timeout` above is frequently too short for it
        # on a local CPU-bound model and was silently causing every real
        # customization attempt to time out and fall back to the
        # deterministic path. Defaults to `timeout` when not given so every
        # other call site's behavior is unchanged.
        self.resume_timeout = resume_timeout if resume_timeout is not None else timeout

    def _generate_json(self, system: str, user: str, timeout: float | None = None) -> dict[str, Any]:
        resp = httpx.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "system": system,
                "prompt": user,
                "format": "json",
                "stream": False,
            },
            timeout=timeout if timeout is not None else self.timeout,
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
            "interview is REMOTE, not IN_PERSON. IMPORTANT: a statement about where the JOB is "
            "located or performed (e.g. \"Location: Charlotte, NC - Onsite\", \"Onsite role\", "
            "\"Hybrid position\") is NEVER by itself evidence of an interview requirement - it "
            "describes the job, not the interview. Only classify IN_PERSON if the text explicitly "
            "talks about the INTERVIEW/ROUND/CALL itself requiring in-person attendance; if the "
            "only thing mentioned is the job's location/work-arrangement with no separate mention "
            "of the interview process, use UNKNOWN. "
            'Respond with strict JSON only: {"interview_type": "IN_PERSON"|"REMOTE"|"HYBRID"|"UNKNOWN", '
            '"requires_in_person_interview": bool, "confidence": 0-1 float, "reason": str, '
            '"evidence": str|null}. evidence must be copied verbatim from the text, never invented, '
            "and must itself mention the interview/round/call - never just the job's location."
        )
        return self._generate_json(system, text[:8000])

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
        system = RESUME_CUSTOMIZATION_PLAN_SYSTEM_PROMPT
        user = (
            f"Job title: {jd_title}\n\n"
            f"Job description (may include the full recruiter email):\n{jd_text[:6000]}\n\n"
            f"Job requirements list: {', '.join(jd_requirements) or 'none extracted'}\n\n"
            f"APPROVED candidate skills (the ONLY skills you may ever surface or reference - "
            f"nothing outside this list): {', '.join(approved_skills) or 'none'}\n\n"
            f"Existing resume structure (what this exact resume already has - only reference "
            f"things listed here):\n{json.dumps(resume_structure, indent=2)}\n\n"
            f"APPROVED experience identifiers (the ONLY values valid for "
            f"experience_identifier): {', '.join(approved_experience_identifiers) or 'none'}\n\n"
            f"Full resume text (for context only - do not invent anything beyond it):\n"
            f"{resume_text[:6000]}"
        )
        return self._generate_json(system, user, timeout=self.resume_timeout)

    def generate_email_skills_pitch(
        self, job_title: str, jd_text: str, top_skills: list[str], candidate_experience: str,
    ) -> str:
        system = EMAIL_SKILLS_PITCH_SYSTEM_PROMPT
        user = (
            f"Job title: {job_title}\n\n"
            f"Job description (for context on what to emphasize - do not quote it back):\n{jd_text[:3000]}\n\n"
            f"Candidate's verified skills (the ONLY skills you may mention): {', '.join(top_skills) or 'none'}\n\n"
            f"Candidate's years of experience: {candidate_experience}"
        )
        resp = httpx.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "system": system, "prompt": user, "stream": False},
            timeout=self.resume_timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()

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
