"""
Recruiter contact-detail extraction (sections 2-3 of the recruiter-CRM spec):
name, phone, company, and the RECRUITER's own job title (e.g. "Talent
Acquisition Executive") - which must never be confused with the JOB OPENING
title (e.g. "Senior Data Engineer (Databricks)"), extracted separately by
app.services.jd_extractor.

All of this is deterministic pattern-matching over the signature block
surrounding the recruiter's name/email - never invented, never guessed from
unrelated parts of the message.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.utils.email_utils import guess_name_for_email

RECRUITER_TITLE_KEYWORDS = [
    "talent acquisition", "technical recruiter", "it recruiter", "senior recruiter",
    "lead recruiter", "recruiter", "staffing specialist", "staffing manager",
    "staffing coordinator", "account manager", "delivery manager", "hr specialist",
    "human resources", "talent partner", "talent advisor", "sourcer", "resource manager",
    "bench sales", "hiring manager", "recruitment", "hr coordinator", "hr manager",
    "hr generalist",
]

COMPANY_SUFFIX_HINTS = [
    "inc", "inc.", "llc", "llc.", "corp", "corp.", "corporation", "solutions",
    "technologies", "technology", "staffing", "consulting", "systems", "group",
    "ltd", "ltd.", "services", "software", "labs", "partners", "resources",
]

# Formats supported per section 3: (737) 304-8920 / 737-304-8920 / 737.304.8920
# / +1 737 304 8920 / +1-737-304-8920
# The (?<!\d)/(?!\d) guards ensure a match can never be a substring lifted out
# of a longer, unformatted run of digits (e.g. a 15-digit garbage string) -
# only a genuinely phone-shaped token is ever accepted (RCRM-14).
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
)

_NOT_A_COMPANY_LINE_RE = re.compile(r"[@]|https?://|\d{3}[-.\s)]", re.IGNORECASE)


@dataclass
class RecruiterInfo:
    name: str | None = None
    phone: str | None = None
    company: str | None = None
    recruiter_role: str | None = None


def normalize_phone_digits(raw: str) -> str | None:
    """Returns a canonical 10-digit (US) digit string, or None if the
    candidate doesn't actually resolve to a real phone number - never invents
    or force-fits a malformed number (RCRM-14)."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return digits


def extract_phone_number(text: str) -> str | None:
    """Returns the first valid phone number found, in its original (human
    readable) formatting, or None if no valid phone number exists anywhere -
    phone is never fabricated (section 3)."""
    if not text:
        return None
    for m in PHONE_RE.finditer(text):
        candidate = m.group(0)
        if normalize_phone_digits(candidate):
            return candidate.strip()
    return None


def _lines_near_name(text: str, name: str, window: int = 8) -> list[str]:
    # A recruiter's name often appears once early (e.g. a forwarded "From:"
    # header) and again at the actual sign-off ("Thanks, Name / Title /
    # Company / phone") - the LAST occurrence is where a real signature block
    # conventionally lives, so anchor there rather than on the first mention.
    idx = text.rfind(name)
    if idx == -1:
        return []
    remainder = text[idx + len(name):]
    lines = [line.strip() for line in remainder.splitlines() if line.strip()]
    return lines[:window]


def _find_recruiter_title(lines: list[str]) -> str | None:
    for line in lines:
        lowered = line.lower()
        if any(keyword in lowered for keyword in RECRUITER_TITLE_KEYWORDS):
            return line.strip(" -|:")
    return None


def _looks_like_company_line(line: str) -> bool:
    if _NOT_A_COMPANY_LINE_RE.search(line):
        return False
    if len(line) > 80 or len(line) < 2:
        return False
    lowered = line.lower()
    if any(keyword in lowered for keyword in RECRUITER_TITLE_KEYWORDS):
        return False  # this is a title line, not a company line
    if any(hint in lowered for hint in COMPANY_SUFFIX_HINTS):
        return True
    # an ALL-CAPS short line (e.g. "PAMTEN") immediately after a title is a
    # very common signature pattern for the company name
    letters = re.sub(r"[^A-Za-z]", "", line)
    if letters and letters.isupper() and len(line.split()) <= 5:
        return True
    return False


def _find_company(lines: list[str], title_line: str | None) -> str | None:
    if title_line is not None and title_line in lines:
        title_idx = lines.index(title_line)
        for candidate in lines[title_idx + 1 : title_idx + 3]:
            if _looks_like_company_line(candidate):
                return candidate.strip(" -|:")
        return None

    # no title line found - fall back to scanning the whole nearby block for
    # a plausible company-shaped line (e.g. "PamTen Inc.")
    for line in lines:
        if _looks_like_company_line(line):
            return line.strip(" -|:")
    return None


def extract_recruiter_info(full_text: str, recruiter_name: str | None) -> RecruiterInfo:
    """
    `full_text` should be the parsed email's combined plain/HTML/forwarded
    text; `recruiter_name` is whatever name was already associated with the
    recruiter's email address (e.g. via a forwarded "From:" block or
    guess_name_for_email) - extraction here is scoped to the text immediately
    following that name, which is where a signature block conventionally sits.
    """
    if not full_text:
        return RecruiterInfo(name=recruiter_name)

    if recruiter_name:
        window_lines = _lines_near_name(full_text, recruiter_name)
    else:
        window_lines = []

    title = _find_recruiter_title(window_lines)
    company = _find_company(window_lines, title)

    # Phone: only ever trust a match inside the signature window immediately
    # following the recruiter's own name - never fall back to a full-message
    # search unscoped to them. A number found anywhere else in the message
    # (e.g. a forwarding sender's own mobile number, elsewhere in the email)
    # is NOT the recruiter's, and must never be misattributed to them.
    phone = extract_phone_number("\n".join(window_lines))

    return RecruiterInfo(name=recruiter_name, phone=phone, company=company, recruiter_role=title)


def extract_recruiter_info_from_text(full_text: str, email: str) -> RecruiterInfo:
    """Convenience entry point: guesses the recruiter's display name from
    wherever it appears near their email address, then extracts the rest of
    their signature-block details around that name."""
    name = guess_name_for_email(full_text, email)
    return extract_recruiter_info(full_text, name)


def split_display_name(display_name: str | None) -> tuple[str | None, str | None]:
    """Best-effort first/last name split of a "First Last" display name."""
    if not display_name or not display_name.strip():
        return None, None
    parts = display_name.strip().split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])
