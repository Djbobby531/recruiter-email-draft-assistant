"""
RULE B/C/G - Recruiter/contact email identification.

Given every email address found in a message, pick EXACTLY ONE "TO" recruiter
contact (never the incoming sender), and the incoming sender becomes CC.

Approach (deterministic, explainable, no AI - this is a precision-critical
decision the spec explicitly wants rule-based):

1. Build the candidate pool = all_emails - {incoming sender, my own address,
   system/no-reply/tracking addresses}.
2. If the pool is empty -> no reliable recruiter email -> manual review.
3. Score each candidate:
     +3  appears in a "From:" block of a forwarded/quoted message (this is
         very likely the original recruiter who wrote the JD)
     +2  domain differs from the incoming sender's domain (suggests a
         different company/person, i.e. the real recruiter forwarded by a
         middleman/vendor)
     +1  appears near a human name (signature-style "Name <email>" or a name
         line immediately above it)
     +1  local-part looks like a real personal name (firstname.lastname etc.)
        -1  local-part looks like a role/team alias (careers@, jobs@, hr@, team@)
     +1  appears earlier in the message (recruiters who wrote the JD tend to
         be quoted first / closest to the top)
4. Highest score wins. If the top score is below threshold or there's a tie,
   fall back to manual review rather than guessing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.email_parser import ParsedEmail
from app.utils.email_utils import first_name, guess_name_for_email, is_system_or_noreply, normalize_email

ROLE_ALIAS_LOCALPARTS = {
    "careers", "jobs", "hr", "team", "recruiting", "talent", "hiring",
    "staffing", "info", "contact", "admin", "sales",
}

PERSONAL_NAME_RE = re.compile(r"^[a-z]+([._\-][a-z]+)+$")


@dataclass
class RecruiterCandidate:
    email: str
    name: str | None
    score: float
    reasons: list[str]


@dataclass
class RecruiterSelection:
    selected: RecruiterCandidate | None
    all_candidates: list[RecruiterCandidate]
    confidence: float
    reason: str


def _domain(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def select_recruiter_email(parsed: ParsedEmail, my_email: str) -> RecruiterSelection:
    incoming_sender = normalize_email(parsed.from_email)
    my_email_norm = normalize_email(my_email) if my_email else ""

    exclude = {incoming_sender}
    if my_email_norm:
        exclude.add(my_email_norm)

    pool = [e for e in parsed.all_emails if e not in exclude and not is_system_or_noreply(e)]

    if not pool:
        # RULE B / CASE 2 (spec section 4): if the incoming sender is the only
        # email present anywhere in the message, that is NOT sufficient to
        # auto-select them as the recruiter - not even for a direct, unforwarded
        # email. This is intentionally conservative: the app must not guess.
        return RecruiterSelection(
            selected=None, all_candidates=[], confidence=0.0,
            reason="no candidate recruiter email found after excluding sender/self/system addresses",
        )

    forwarded_from_emails = {e for _, e in parsed.forwarded_from_blocks}
    sender_domain = _domain(incoming_sender)

    candidates: list[RecruiterCandidate] = []
    for idx, email in enumerate(pool):
        reasons: list[str] = []
        score = 0.0

        if email in forwarded_from_emails:
            score += 3
            reasons.append("appears in a forwarded/quoted 'From:' line")

        if _domain(email) and _domain(email) != sender_domain:
            score += 2
            reasons.append("domain differs from incoming sender (likely original recruiter/company)")

        name = guess_name_for_email(parsed.full_text, email)
        if name:
            score += 1
            reasons.append(f"associated with a name in the message ('{name}')")

        local_part = email.split("@")[0].lower()
        if PERSONAL_NAME_RE.match(local_part) or (name and "." not in local_part and len(local_part) > 2):
            score += 1
            reasons.append("local-part looks like a personal name")
        if local_part in ROLE_ALIAS_LOCALPARTS:
            score -= 1
            reasons.append("local-part looks like a generic role alias")

        position_bonus = max(0.0, 1.0 - (idx * 0.15))
        score += position_bonus
        reasons.append(f"position bonus {position_bonus:.2f} (appears at candidate index {idx})")

        candidates.append(RecruiterCandidate(email=email, name=name, score=score, reasons=reasons))

    candidates.sort(key=lambda c: c.score, reverse=True)
    top = candidates[0]

    # normalize confidence into 0..1 range against a reasonable max possible score (~7)
    confidence = max(0.0, min(1.0, top.score / 7.0))

    # tie-break check: if second place is essentially equal, that's ambiguous
    ambiguous_tie = (
        len(candidates) > 1
        and abs(candidates[0].score - candidates[1].score) < 0.25
    )

    if ambiguous_tie:
        confidence = min(confidence, 0.5)

    return RecruiterSelection(
        selected=top,
        all_candidates=candidates,
        confidence=confidence,
        reason="; ".join(top.reasons),
    )


def build_subject_prefix_name(candidate: RecruiterCandidate | None) -> str:
    return first_name(candidate.name if candidate else None)
