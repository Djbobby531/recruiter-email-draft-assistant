"""Low-level email address / name extraction helpers. Pure functions, no I/O."""
from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Header line that introduces a forwarded/quoted block, e.g.
# "From: Naveen Gangupamu <naveen.gangupamu@pamten.com>"
FROM_HEADER_RE = re.compile(
    r"^\s*From:\s*(?P<name>[^<\n]*?)\s*<?(?P<email>[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>?",
    re.IGNORECASE | re.MULTILINE,
)

# A "From:" line relayed through a mailing list/group shows the LIST's own
# address first, with the actual person tacked on afterward, e.g.
# "From: c2c-vendor-125@googlegroups.com <c2c-vendor-125@googlegroups.com>
# on behalf of Adarsh Tiwari <adarsh@kk-talents.com>" - the real recruiter is
# whatever comes after "on behalf of", never the list address itself.
ON_BEHALF_OF_RE = re.compile(
    r"\bon\s+behalf\s+of\s+(?P<name>[^<\n]*?)\s*<(?P<email>[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>",
    re.IGNORECASE,
)

NOREPLY_PATTERNS = [
    "no-reply",
    "noreply",
    "donotreply",
    "do-not-reply",
    "notifications@",
    "notification@",
    "mailer-daemon",
    "postmaster@",
    "bounce",
    "tracking@",
    "unsubscribe@",
    "newsletter@",
    "info@indeed",
    "jobalerts",
    "jobs-noreply",
    "linkedin.com",
    "calendar-notification",
    # Mailing-list/distribution relay domains - the address itself is a
    # group, never an individual recruiter, so it must never be selected as
    # the recruiter contact even when it's the only email left in the pool.
    "googlegroups.com",
]

FREE_MAIL_SYSTEM_LOCALPARTS = {"admin", "support", "help", "webmaster"}

# Words that mark a line as a JOB TITLE rather than a person's name, so a
# backward scan for "whose name is this email near" doesn't mistake a title
# line ("Talent Acquisition Executive") for the name itself just because it's
# the line immediately above/below the email.
LIKELY_TITLE_KEYWORDS = [
    "talent acquisition", "recruiter", "recruitment", "staffing", "sourcer",
    "account manager", "delivery manager", "resource manager", "hiring manager",
    "human resources", "hr specialist", "hr coordinator", "hr manager", "hr generalist",
    "bench sales", "talent partner", "talent advisor",
    "manager", "executive", "director", "specialist", "coordinator", "partner", "consultant",
]


def normalize_email(addr: str) -> str:
    return addr.strip().strip("<>").lower()


def extract_all_emails(text: str) -> list[str]:
    """Extract every email-looking token from a block of text, de-duplicated, order preserved."""
    if not text:
        return []
    found = EMAIL_RE.findall(text)
    seen: list[str] = []
    seen_set = set()
    for f in found:
        norm = normalize_email(f)
        if norm not in seen_set:
            seen_set.add(norm)
            seen.append(norm)
    return seen


def is_system_or_noreply(addr: str) -> bool:
    a = addr.lower()
    return any(p in a for p in NOREPLY_PATTERNS)


def extract_forwarded_from_blocks(text: str) -> list[tuple[str, str]]:
    """
    Find `From: Name <email>` style lines that indicate a forwarded/quoted
    message embedded in the body (Outlook/Gmail forward convention).
    Returns list of (name, email) in order of appearance.

    A line relayed through a mailing list/group ("From: <list-address> ...
    on behalf of Real Name <real@email>") is resolved to the person after
    "on behalf of", never the list's own address - see ON_BEHALF_OF_RE.
    """
    if not text:
        return []
    results = []
    for m in FROM_HEADER_RE.finditer(text):
        line_end = text.find("\n", m.start())
        full_line = text[m.start(): line_end if line_end != -1 else len(text)]

        behalf_match = ON_BEHALF_OF_RE.search(full_line)
        if behalf_match:
            name = behalf_match.group("name").strip().strip('"').strip()
            email = normalize_email(behalf_match.group("email"))
        else:
            name = m.group("name").strip().strip('"').strip()
            email = normalize_email(m.group("email"))
        results.append((name, email))
    return results


def guess_name_for_email(text: str, email: str) -> str | None:
    """
    Look for a name near an email address occurrence, e.g. signature lines
    "Naveen Gangupamu" above/below "naveen.gangupamu@pamten.com", or the
    "Name <email>" pattern.
    """
    if not text or not email:
        return None

    # Same-line only ([ \t]*, never \s* which would also match newlines) - a
    # job-title line sitting on the line just above the email ("Talent
    # Acquisition Executive\nsarah.kim@x.com") must NOT be captured here just
    # because it's 1-3 capitalized words immediately "before" the email; that
    # case is handled correctly by the backward line scan below instead.
    pattern = re.compile(
        r"([A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){0,2})[ \t]*[<\[]?[ \t]*" + re.escape(email),
    )
    m = pattern.search(text)
    if m:
        return m.group(1).strip()

    # Scan a few lines backward from the email's first occurrence, skipping
    # any line that reads as a job title/role rather than a person's name.
    idx = text.lower().find(email.lower())
    if idx == -1:
        return None
    prefix = text[:idx]
    lines = [line.strip() for line in prefix.splitlines() if line.strip()]

    for candidate in reversed(lines[-6:]):
        candidate = re.sub(r"^(From|To|Cc|Sent)\s*:\s*", "", candidate, flags=re.IGNORECASE)
        candidate = candidate.strip('<>"() ')
        if "@" in candidate or len(candidate) >= 60 or len(candidate) < 2:
            continue
        if any(keyword in candidate.lower() for keyword in LIKELY_TITLE_KEYWORDS):
            continue
        # an ALL-CAPS line (e.g. "PAMTEN") is conventionally a company name in
        # a signature block, never a person's name
        letters_only = re.sub(r"[^A-Za-z]", "", candidate)
        if letters_only and letters_only.isupper():
            continue
        name_match = re.match(r"^[A-Za-z.'\-]+(?:\s+[A-Za-z.'\-]+){0,2}$", candidate)
        if name_match:
            return candidate
    return None


def first_name(full_name: str | None) -> str:
    if not full_name:
        return "there"
    return full_name.strip().split()[0]
