from __future__ import annotations

from app.services.email_parser import parse_gmail_message
from tests.conftest import make_raw_message
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


def test_extracts_all_emails_and_forwarded_from_block():
    raw = make_raw_message(
        message_id="p1", thread_id="pt1",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    parsed = parse_gmail_message(raw)

    assert parsed.from_email == "james@algebrait.com"
    assert "naveen.gangupamu@pamten.com" in parsed.all_emails
    assert "james@algebrait.com" in parsed.all_emails
    assert ("Naveen Gangupamu", "naveen.gangupamu@pamten.com") in parsed.forwarded_from_blocks


def test_extracts_to_email_from_the_to_header():
    raw = make_raw_message(
        message_id="p1b", thread_id="pt1b",
        from_header="James AlgebraIT <james@algebrait.com>",
        to_header="Diwakar Jilakara <diwakar@example.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    parsed = parse_gmail_message(raw)
    assert parsed.to_email == "diwakar@example.com"


def test_to_email_is_empty_string_when_no_to_header_present():
    raw = {"id": "p1c", "threadId": "pt1c", "payload": {"headers": [{"name": "From", "value": "a@b.com"}], "mimeType": "text/plain", "parts": []}}
    parsed = parse_gmail_message(raw)
    assert parsed.to_email == ""


def test_html_body_is_converted_to_text():
    html = "<html><body><p>Role: Data Engineer</p><p>Location: Remote</p></body></html>"
    raw = make_raw_message(
        message_id="p2", thread_id="pt2",
        from_header="a@b.com", subject="Job", plain_body="", html_body=html,
    )
    parsed = parse_gmail_message(raw)
    assert "Role: Data Engineer" in parsed.html_text
    assert "Location: Remote" in parsed.html_text
