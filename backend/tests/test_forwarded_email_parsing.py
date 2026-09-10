"""
Section 5: forwarded email parsing across Gmail-style, Outlook-style, Apple
Mail ("Begin forwarded message:") style, nested forwards, HTML forwards, and
forwards where the outer sender and inner recruiter differ.
"""
from __future__ import annotations

from app.services.email_parser import parse_gmail_message
from app.services.recruiter_selector import select_recruiter_email
from tests.conftest import load_forwarded_email, make_raw_message

MY_EMAIL = "diwakar@example.com"


def test_gmail_style_forward_identifies_recruiter():
    body = load_forwarded_email("gmail_style.txt")
    raw = make_raw_message(
        message_id="fw1", thread_id="t1", from_header="james@algebrait.com",
        subject="Fwd: Senior Data Engineer (Databricks)", plain_body=body,
    )
    parsed = parse_gmail_message(raw)
    selection = select_recruiter_email(parsed, my_email=MY_EMAIL)
    assert selection.selected.email == "recruiter@example.com"
    assert parsed.from_email == "james@algebrait.com"


def test_outlook_style_forward_identifies_recruiter():
    body = load_forwarded_email("outlook_style.txt")
    raw = make_raw_message(
        message_id="fw2", thread_id="t2", from_header="james@algebrait.com",
        subject="Fwd: Role: Senior Data Engineer (Databricks)", plain_body=body,
    )
    parsed = parse_gmail_message(raw)
    selection = select_recruiter_email(parsed, my_email=MY_EMAIL)
    assert selection.selected.email == "naveen.gangupamu@pamten.com"
    assert ("Naveen Gangupamu", "naveen.gangupamu@pamten.com") in parsed.forwarded_from_blocks


def test_apple_mail_begin_forwarded_message_style():
    body = load_forwarded_email("begin_forwarded.txt")
    raw = make_raw_message(
        message_id="fw3", thread_id="t3", from_header="james@algebrait.com",
        subject="Fwd: Job Description: Senior Data Engineer (Databricks)", plain_body=body,
    )
    parsed = parse_gmail_message(raw)
    selection = select_recruiter_email(parsed, my_email=MY_EMAIL)
    assert selection.selected.email == "priya.sharma@staffingco.com"


def test_nested_forwarded_emails_finds_original_recruiter():
    body = load_forwarded_email("nested_forward.txt")
    raw = make_raw_message(
        message_id="fw4", thread_id="t4", from_header="mike@thirdpartyfirm.com",
        subject="Fwd: Fwd: Job Description: Senior Data Engineer (Databricks)", plain_body=body,
    )
    parsed = parse_gmail_message(raw)
    selection = select_recruiter_email(parsed, my_email=MY_EMAIL)
    # the innermost/original recruiter (Priya) should outrank the pass-through forwarder (Mike)
    assert selection.selected.email == "priya.sharma@staffingco.com"
    assert selection.selected.email != parsed.from_email


def test_forwarded_email_with_html_content():
    html = load_forwarded_email("html_forward.html")
    raw = make_raw_message(
        message_id="fw5", thread_id="t5", from_header="james@algebrait.com",
        subject="Fwd: Role: Senior Data Engineer (Databricks)", plain_body="", html_body=html,
    )
    parsed = parse_gmail_message(raw)
    assert "naveen.gangupamu@pamten.com" in parsed.all_emails
    selection = select_recruiter_email(parsed, my_email=MY_EMAIL)
    assert selection.selected.email == "naveen.gangupamu@pamten.com"


def test_forwarded_email_with_signature_noise():
    body = load_forwarded_email("repeated_recruiter_email.txt")
    raw = make_raw_message(
        message_id="fw6", thread_id="t6", from_header="james@algebrait.com",
        subject="Fwd: Role: Senior Data Engineer (Databricks)", plain_body=body,
    )
    parsed = parse_gmail_message(raw)
    selection = select_recruiter_email(parsed, my_email=MY_EMAIL)
    assert selection.selected.email == "naveen.gangupamu@pamten.com"


def test_forwarded_email_where_recruiter_appears_several_times_still_one_candidate():
    body = load_forwarded_email("repeated_recruiter_email.txt")
    parsed = parse_gmail_message(make_raw_message(
        message_id="fw7", thread_id="t7", from_header="james@algebrait.com",
        subject="Fwd: Role", plain_body=body,
    ))
    assert parsed.all_emails.count("naveen.gangupamu@pamten.com") == 1


def test_outer_sender_and_inner_recruiter_are_different_people():
    body = load_forwarded_email("outlook_style.txt")
    parsed = parse_gmail_message(make_raw_message(
        message_id="fw8", thread_id="t8", from_header="james@algebrait.com",
        subject="Fwd: Role", plain_body=body,
    ))
    selection = select_recruiter_email(parsed, my_email=MY_EMAIL)
    assert selection.selected.email != parsed.from_email
    assert parsed.from_email == "james@algebrait.com"
    assert selection.selected.email == "naveen.gangupamu@pamten.com"
