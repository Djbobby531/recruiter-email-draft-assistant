"""
EA-01 .. EA-15: email address extraction edge cases (section 3 of the hardening spec).
"""
from __future__ import annotations

from app.services.email_parser import parse_gmail_message
from app.utils.email_utils import (
    extract_all_emails,
    extract_forwarded_from_blocks,
    guess_name_for_email,
    is_system_or_noreply,
)
from tests.conftest import load_forwarded_email, make_raw_message


def test_ea01_plain_email_in_body():
    assert extract_all_emails("Please reach out to naveen@pamten.com for details.") == ["naveen@pamten.com"]


def test_ea02_email_inside_html_anchor_mailto():
    raw = make_raw_message(
        message_id="ea02", thread_id="t-ea02", from_header="james@algebrait.com",
        subject="Role", plain_body="",
        html_body='<p>Contact <a href="mailto:naveen.gangupamu@pamten.com">Naveen Gangupamu</a> for this role.</p>',
    )
    parsed = parse_gmail_message(raw)
    assert "naveen.gangupamu@pamten.com" in parsed.all_emails


def test_ea03_email_inside_forwarded_email():
    text = load_forwarded_email("gmail_style.txt")
    assert "recruiter@example.com" in extract_all_emails(text)


def test_ea04_email_inside_quoted_reply():
    text = (
        "Sounds good.\n\n"
        "> On Fri, Sep 4, 2026, Naveen Gangupamu <naveen.gangupamu@pamten.com> wrote:\n"
        "> Let's proceed with the interview."
    )
    assert "naveen.gangupamu@pamten.com" in extract_all_emails(text)


def test_ea05_email_inside_signature():
    text = (
        "Let me know if you have questions.\n\n"
        "--\nNaveen Gangupamu\nTalent Acquisition Executive\nnaveen.gangupamu@pamten.com\n(555) 987-6543"
    )
    assert "naveen.gangupamu@pamten.com" in extract_all_emails(text)


def test_ea06_multiple_email_addresses():
    text = "cc: a@x.com, b@y.com, and c@z.com are all on this thread."
    found = extract_all_emails(text)
    assert set(found) == {"a@x.com", "b@y.com", "c@z.com"}


def test_ea07_same_email_repeated_is_deduplicated():
    text = "naveen@pamten.com wrote this. Please reply to naveen@pamten.com. Again: naveen@pamten.com."
    assert extract_all_emails(text) == ["naveen@pamten.com"]


def test_ea08_email_with_uppercase_is_normalized_lowercase():
    text = "Contact Naveen.Gangupamu@PAMTEN.COM about this role."
    assert extract_all_emails(text) == ["naveen.gangupamu@pamten.com"]


def test_ea09_email_with_plus_addressing():
    text = "Apply via jobs+databricks@pamten.com"
    assert extract_all_emails(text) == ["jobs+databricks@pamten.com"]


def test_ea10_email_with_subdomain():
    text = "Recruiter: naveen@careers.pamten.com"
    assert extract_all_emails(text) == ["naveen@careers.pamten.com"]


def test_ea11_malformed_email_looking_text_is_ignored():
    text = "Version 2.5@build is not an email. Neither is @nobody or somebody@."
    assert extract_all_emails(text) == []


def test_ea12_tracking_no_reply_address_is_flagged():
    assert is_system_or_noreply("no-reply@notifications.linkedin.com") is True
    assert is_system_or_noreply("jobs-noreply@indeed.com") is True
    assert is_system_or_noreply("naveen.gangupamu@pamten.com") is False


def test_ea13_original_sender_appears_multiple_times_in_forwarded_content():
    text = load_forwarded_email("outlook_style.txt") + "\n\njames@algebrait.com\njames@algebrait.com"
    found = extract_all_emails(text)
    assert found.count("james@algebrait.com") <= 1  # de-duplicated
    assert "james@algebrait.com" in found


def test_ea14_users_own_email_appears_inside_body():
    raw = make_raw_message(
        message_id="ea14", thread_id="t-ea14", from_header="james@algebrait.com",
        subject="Role", plain_body="cc: diwakar@example.com (candidate), naveen@pamten.com (recruiter)",
    )
    parsed = parse_gmail_message(raw)
    assert "diwakar@example.com" in parsed.all_emails
    assert "naveen@pamten.com" in parsed.all_emails


def test_ea15_email_displayed_with_name_extracts_both():
    text = "Naveen Gangupamu naveen.gangupamu@pamten.com"
    emails = extract_all_emails(text)
    assert "naveen.gangupamu@pamten.com" in emails
    name = guess_name_for_email(text, "naveen.gangupamu@pamten.com")
    assert name == "Naveen Gangupamu"


def test_ea16_googlegroups_relay_address_is_flagged_as_system():
    assert is_system_or_noreply("c2c-vendor-125@googlegroups.com") is True
    assert is_system_or_noreply("c2c-vendor-125+unsubscribe@googlegroups.com") is True
    assert is_system_or_noreply("adarsh@kk-talents.com") is False


def test_ea17_forwarded_from_block_resolves_on_behalf_of_to_the_real_person():
    text = (
        "From: c2c-vendor-125@googlegroups.com <c2c-vendor-125@googlegroups.com> "
        "on behalf of Adarsh Tiwari <adarsh@kk-talents.com>\n"
        "Sent: Wednesday, September 9, 2026 12:17 AM\n"
    )
    blocks = extract_forwarded_from_blocks(text)
    assert blocks == [("Adarsh Tiwari", "adarsh@kk-talents.com")]


def test_ea18_forwarded_from_block_without_on_behalf_of_is_unaffected():
    """The 'on behalf of' handling must not change the normal, common case."""
    text = "From: Naveen Gangupamu <naveen.gangupamu@pamten.com>\nSent: Friday\n"
    blocks = extract_forwarded_from_blocks(text)
    assert blocks == [("Naveen Gangupamu", "naveen.gangupamu@pamten.com")]
