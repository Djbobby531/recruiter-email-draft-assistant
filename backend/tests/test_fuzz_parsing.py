"""
Section 19: fuzz/property-style tests. The parser must never crash on
malformed, weird, or adversarial input - only ever return empty/partial
results. No hypothesis dependency needed; these are deterministic
"pathological input" cases plus a lightweight randomized sweep using stdlib
`random` with a fixed seed for reproducibility.
"""
from __future__ import annotations

import random
import string

import pytest

from app.services.email_parser import html_to_text, parse_gmail_message, split_quoted_sections
from app.services.jd_extractor import extract_job_details
from app.services.job_classifier import classify
from app.utils.email_utils import extract_all_emails, guess_name_for_email
from tests.conftest import make_raw_message

PATHOLOGICAL_STRINGS = [
    "",
    " ",
    "\n\n\n\n\n",
    "@" * 500,
    "a" * 100000,  # very long body, no structure
    "<" * 1000,
    "<html><body><div><div><div><div>" * 50 + "unclosed tags galore",
    "\x00\x01\x02 binary-looking garbage \xff\xfe",
    "😀😃😄 emoji only subject 🚀🔥",
    "混合语言邮件内容 mixed language content",
    "a@b@c@d@e.com not really an email",
    "From: \nFrom: \nFrom: \n" * 200,  # forwarded-header spam
    "-" * 5000,
    "\t\t\t\t\r\n\r\n   \t",
    "SELECT * FROM users; DROP TABLE resumes; --",
    "<script>alert(1)</script> job opportunity role: engineer",
]


@pytest.mark.parametrize("text", PATHOLOGICAL_STRINGS, ids=[f"case{i}" for i in range(len(PATHOLOGICAL_STRINGS))])
def test_extract_all_emails_never_crashes(text):
    result = extract_all_emails(text)
    assert isinstance(result, list)


@pytest.mark.parametrize("text", PATHOLOGICAL_STRINGS, ids=[f"case{i}" for i in range(len(PATHOLOGICAL_STRINGS))])
def test_html_to_text_never_crashes(text):
    result = html_to_text(text)
    assert isinstance(result, str)


@pytest.mark.parametrize("text", PATHOLOGICAL_STRINGS, ids=[f"case{i}" for i in range(len(PATHOLOGICAL_STRINGS))])
def test_split_quoted_sections_never_crashes(text):
    result = split_quoted_sections(text)
    assert isinstance(result, list)


@pytest.mark.parametrize("text", PATHOLOGICAL_STRINGS, ids=[f"case{i}" for i in range(len(PATHOLOGICAL_STRINGS))])
def test_extract_job_details_never_crashes(text):
    jd = extract_job_details(text=text, subject=text[:200])
    assert jd.job_title is None or isinstance(jd.job_title, str)
    assert jd.job_location is None or isinstance(jd.job_location, str)
    assert isinstance(jd.requirements, list)


@pytest.mark.parametrize("text", PATHOLOGICAL_STRINGS, ids=[f"case{i}" for i in range(len(PATHOLOGICAL_STRINGS))])
def test_classify_never_crashes(text):
    result = classify(subject=text[:200], body_text=text, threshold=0.5, ai_provider=None)
    assert isinstance(result.is_job_email, bool)


@pytest.mark.parametrize("text", PATHOLOGICAL_STRINGS, ids=[f"case{i}" for i in range(len(PATHOLOGICAL_STRINGS))])
def test_guess_name_for_email_never_crashes(text):
    result = guess_name_for_email(text, "test@example.com")
    assert result is None or isinstance(result, str)


def test_full_pipeline_parse_never_crashes_on_malformed_gmail_payload():
    """A Gmail message payload with missing/malformed parts must not crash
    parse_gmail_message - it should degrade to empty fields."""
    malformed_payloads = [
        {"id": "x1", "threadId": "t1", "payload": {}},
        {"id": "x2", "threadId": "t2", "payload": {"headers": []}},
        {"id": "x3", "threadId": "t3", "payload": {"headers": None, "parts": None}} ,
        {"id": "x4", "threadId": "t4", "payload": {"headers": [{"name": "From"}]}},  # missing "value"
        {"id": "x5", "threadId": "t5", "payload": {"parts": [{"mimeType": "text/plain", "body": {"data": "not-valid-base64!!!"}}]}},
    ]
    for raw in malformed_payloads:
        parsed = parse_gmail_message(raw)
        assert isinstance(parsed.all_emails, list)


@pytest.mark.parametrize("seed", range(10))
def test_randomized_whitespace_and_casing_never_crashes(seed):
    """A lightweight fuzz sweep: take a realistic email and randomly perturb
    whitespace/casing/punctuation, then run it through the full parser +
    classifier + JD extractor and assert only that nothing crashes."""
    rng = random.Random(seed)
    base = (
        "From: James AlgebraIT <james@algebrait.com>\n"
        "Role: Senior Data Engineer (Databricks)\n"
        "Location: Irvine, CA or Los Angeles, CA\n"
        "Required Skills: Databricks, Python, SQL, Airflow, Terraform\n"
        "Contact: naveen.gangupamu@pamten.com\n"
    )
    chars = list(base)
    for _ in range(50):
        idx = rng.randrange(len(chars))
        choice = rng.random()
        if choice < 0.3:
            chars[idx] = chars[idx].upper() if chars[idx].islower() else chars[idx].lower()
        elif choice < 0.6:
            chars.insert(idx, rng.choice(string.whitespace))
        elif choice < 0.8:
            chars.insert(idx, rng.choice("!@#$%^&*()[]{}"))
        else:
            if len(chars) > 10:
                del chars[idx]
    mutated = "".join(chars)

    raw = make_raw_message(
        message_id=f"fuzz{seed}", thread_id=f"fuzz{seed}-t",
        from_header="james@algebrait.com", subject="Fwd: role", plain_body=mutated,
    )
    parsed = parse_gmail_message(raw)
    classify(parsed.subject, parsed.full_text, threshold=0.5, ai_provider=None)
    extract_job_details(text=parsed.full_text, subject=parsed.subject)
    # the only assertion is that none of the above raised


def test_very_long_email_body_completes_quickly():
    """A pathologically long body (simulating a huge quoted thread history)
    must not cause catastrophic slowdown (e.g. regex backtracking blowup)."""
    import time

    long_body = ("Role: Senior Data Engineer\nLocation: Remote\n" + ("Lorem ipsum dolor sit amet. " * 20000))
    raw = make_raw_message(
        message_id="long1", thread_id="long1-t", from_header="james@algebrait.com",
        subject="Role", plain_body=long_body,
    )
    start = time.monotonic()
    parsed = parse_gmail_message(raw)
    extract_job_details(text=parsed.full_text, subject=parsed.subject)
    classify(parsed.subject, parsed.full_text, threshold=0.5, ai_provider=None)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, f"parsing a long body took too long: {elapsed:.2f}s"
