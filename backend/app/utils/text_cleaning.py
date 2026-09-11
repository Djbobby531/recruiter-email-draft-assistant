"""
Symbol stripping for a job title/role string wherever it's shown to the
candidate or a recruiter - the resume header, the generated resume filename,
and the email subject/body. Recruiter email subjects and AI-based JD
extraction frequently carry decorative junk (emoji, a stray "#", an embedded
email address's "@") that has no place in a professional title; this is the
single place that junk gets stripped before a title is ever used downstream.
"""
from __future__ import annotations

import re

# Emoji / pictographs / dingbats / flag-indicator symbols - the decorative
# junk most commonly seen in real recruiter subject lines (e.g. "🚨 URGENT").
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"  # arrows
    "\U00002B00-\U00002BFF"  # misc symbols/arrows
    "]+",
)

# Punctuation with no place in a job title - keeps the ones that actually
# carry meaning there: hyphen/en-dash/em-dash, slash, ampersand, comma,
# period, plus (e.g. "C++").
_DISALLOWED_SYMBOLS_RE = re.compile(r"[#@*|_~^`<>{}\[\]\"'!]+")


def clean_role_text(text: str | None) -> str | None:
    """Strips emoji and decorative/special-symbol punctuation from a role
    string, collapsing whatever whitespace/separators are left behind. Never
    invents or reorders anything - purely subtractive. Returns None if
    nothing meaningful survives."""
    if not text:
        return text
    cleaned = _EMOJI_RE.sub("", text)
    cleaned = _DISALLOWED_SYMBOLS_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—/,.")
    return cleaned or None
