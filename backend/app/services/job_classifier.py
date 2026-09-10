"""
Job/recruiter email classification.

Deterministic keyword-rule scoring first (fast, free, reproducible). Only falls
back to the AI provider when the deterministic score lands in an ambiguous
middle band and an AI provider is configured - per spec: "Use AI only when
necessary for ambiguous classification."
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.base import AIProvider

JOB_KEYWORDS = [
    "job opportunity", "position", "role", "hiring", "recruiter",
    "talent acquisition", "job description", "responsibilities",
    "qualifications", "required skills", "contract", "w2", "c2c", "corp to corp",
    "1099", "candidate", "resume", "interview", "job title", "years of experience",
    "rate", "remote", "hybrid", "onsite", "client", "requirement", "req id",
    "immediate need", "submit", "vendor", "bench", "opening", "opportunity",
]

NEGATIVE_KEYWORDS = [
    "unsubscribe", "% off", "sale", "invoice", "receipt", "order confirmation",
    "newsletter", "webinar", "your subscription", "verify your email",
    "password reset", "friend request", "event invitation",
]

STRONG_JOB_PATTERNS = [
    re.compile(r"\brole\s*:", re.IGNORECASE),
    re.compile(r"\bjob title\s*:", re.IGNORECASE),
    re.compile(r"\bposition\s*:", re.IGNORECASE),
    re.compile(r"\brequired skills\s*:", re.IGNORECASE),
    re.compile(r"\bresponsibilities\s*:", re.IGNORECASE),
    re.compile(r"\bc2c\b", re.IGNORECASE),
    re.compile(r"\bw2\b", re.IGNORECASE),
    re.compile(r"\bjd\b", re.IGNORECASE),
]


@dataclass
class ClassificationResult:
    is_job_email: bool
    confidence: float
    reason: str
    used_ai: bool = False


def _deterministic_score(text: str) -> float:
    lowered = text.lower()
    hits = sum(1 for kw in JOB_KEYWORDS if kw in lowered)
    strong_hits = sum(1 for pat in STRONG_JOB_PATTERNS if pat.search(text))
    negative_hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in lowered)

    # 2-3 ordinary job keywords (e.g. "responsibilities" + "qualifications" +
    # "candidate") is already a reliable signal on its own - a real job email
    # rarely needs 6 keyword hits to be recognizable, so full keyword credit is
    # reached at 4 hits rather than 6. Strong (colon-labeled/jargon) patterns
    # remain a meaningful but smaller boost since they're comparatively rare.
    score = min(hits / 4.0, 1.0) * 0.7 + min(strong_hits / 2.0, 1.0) * 0.3
    score -= negative_hits * 0.25
    return max(0.0, min(1.0, score))


def classify(
    subject: str,
    body_text: str,
    threshold: float,
    ai_provider: AIProvider | None = None,
) -> ClassificationResult:
    combined = f"{subject}\n{body_text}"
    score = _deterministic_score(combined)

    # Clear-cut cases: don't bother with AI.
    if score >= threshold + 0.2:
        return ClassificationResult(True, score, "strong deterministic keyword match")
    if score <= threshold - 0.2:
        return ClassificationResult(False, score, "low deterministic keyword match")

    # Ambiguous band -> ask AI if available.
    if ai_provider is not None:
        try:
            ai_result = ai_provider.classify_job_email(subject=subject, body=body_text[:6000])
            return ClassificationResult(
                is_job_email=ai_result.get("is_job_email", score >= threshold),
                confidence=float(ai_result.get("confidence", score)),
                reason=f"AI classification: {ai_result.get('reason', '')}",
                used_ai=True,
            )
        except Exception as exc:  # AI failure must never block deterministic fallback
            return ClassificationResult(
                score >= threshold, score, f"AI classification failed ({exc}); used deterministic score"
            )

    return ClassificationResult(score >= threshold, score, "deterministic score, no AI configured")
