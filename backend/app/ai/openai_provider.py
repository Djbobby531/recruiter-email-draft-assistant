from __future__ import annotations

import json
from typing import Any

import httpx

from app.ai.base import AIProvider

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(AIProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", timeout: float = 30.0):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required to use AI_PROVIDER=openai")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _chat_json(self, system: str, user: str) -> dict[str, Any]:
        resp = httpx.post(
            OPENAI_CHAT_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)

    def classify_job_email(self, subject: str, body: str) -> dict[str, Any]:
        system = (
            "You classify emails as job/recruiter opportunity emails or not. "
            'Respond with strict JSON: {"is_job_email": bool, "confidence": 0-1 float, "reason": str}.'
        )
        user = f"Subject: {subject}\n\nBody:\n{body}"
        return self._chat_json(system, user)

    def extract_job_details(self, text: str) -> dict[str, Any]:
        system = (
            "Extract structured job posting details from the email text. "
            'Respond with strict JSON: {"job_title": str|null, "job_location": str|null, '
            '"requirements": [str]}. job_location must be copied verbatim from the text, never '
            "invented. requirements should be concrete skills/technologies/tools mentioned."
        )
        return self._chat_json(system, text[:8000])

    def classify_interview_requirement(self, text: str) -> dict[str, Any]:
        system = (
            "Classify the interview arrangement described in this job/recruiter email. "
            "Distinguish job location/work-arrangement (onsite/hybrid/remote job) from the "
            "INTERVIEW arrangement itself - only classify IN_PERSON if the interview process "
            "clearly requires physical attendance. A hybrid or onsite JOB with a remote "
            "interview is REMOTE, not IN_PERSON. "
            'Respond with strict JSON: {"interview_type": "IN_PERSON"|"REMOTE"|"HYBRID"|"UNKNOWN", '
            '"requires_in_person_interview": bool, "confidence": 0-1 float, "reason": str, '
            '"evidence": str|null}. evidence must be copied verbatim from the text, never invented. '
            "If the interview format is not mentioned or unclear, use UNKNOWN with "
            "requires_in_person_interview=false."
        )
        return self._chat_json(system, text[:8000])

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
            'Respond with strict JSON: {"match_score": 0-100 float, "match_explanation": str, '
            '"additional_points": [str]}.'
        )
        user = (
            f"Job Title: {jd_title}\n\nJob Description:\n{jd_text[:6000]}\n\n"
            f"Candidate's existing (but underemphasized) skills: {', '.join(candidate_existing_skills) or 'none'}\n\n"
            f"Resume text:\n{resume_text[:6000]}"
        )
        return self._chat_json(system, user)

    def polish_email_body(self, draft_body: str, constraints: list[str]) -> str:
        system = (
            "You lightly rewrite a job-application email to sound natural and concise, "
            "in plain text (no markdown, no JSON). You MUST preserve every constraint given. "
            "If unsure, return the text unchanged. Output ONLY the rewritten email itself, "
            "starting directly with 'Hi,' (never a name) - never add any preamble, commentary, "
            "explanation, or markdown before or after it."
        )
        constraint_text = "\n".join(f"- {c}" for c in constraints)
        user = f"Constraints (must never be violated):\n{constraint_text}\n\nDraft email:\n{draft_body}"
        resp = httpx.post(
            OPENAI_CHAT_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
