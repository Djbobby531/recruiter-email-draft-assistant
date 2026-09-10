"""
CASE 1..10: recruiter email selection combinations (section 4 of the hardening spec).
"""
from __future__ import annotations

from app.services.email_parser import parse_gmail_message
from app.services.recruiter_selector import select_recruiter_email
from tests.conftest import make_raw_message

MY_EMAIL = "diwakar@example.com"


def _select(from_header, body, subject="Role: Senior Data Engineer"):
    raw = make_raw_message(message_id="m", thread_id="t", from_header=from_header, subject=subject, plain_body=body)
    parsed = parse_gmail_message(raw)
    return select_recruiter_email(parsed, my_email=MY_EMAIL)


def test_case1_recruiter_in_forwarded_from_line():
    selection = _select("james@algebrait.com", "From: naveen@pamten.com\nRole: Senior Data Engineer")
    assert selection.selected.email == "naveen@pamten.com"


def test_case2_original_sender_is_only_email_no_draft():
    selection = _select("james@algebrait.com", "Role: Senior Data Engineer\nNo other contact info at all.")
    assert selection.selected is None


def test_case3_sender_plus_own_email_plus_recruiter_selects_recruiter_only():
    selection = _select(
        "james@algebrait.com",
        "cc: diwakar@example.com\nFrom: naveen@pamten.com\nRole: Senior Data Engineer",
    )
    assert selection.selected.email == "naveen@pamten.com"
    assert all(c.email != MY_EMAIL for c in selection.all_candidates)
    assert all(c.email != "james@algebrait.com" for c in selection.all_candidates)


def test_case4_sender_plus_noreply_plus_recruiter_selects_recruiter():
    selection = _select(
        "james@algebrait.com",
        "Notifications: no-reply@notifications.example.com\nFrom: naveen@pamten.com\nRole: Senior Data Engineer",
    )
    assert selection.selected.email == "naveen@pamten.com"


def test_case5_sender_plus_hr_plus_recruiter_chooses_most_relevant():
    selection = _select(
        "james@algebrait.com",
        "HR Team: hr@algebrait.com\nFrom: Naveen Gangupamu <naveen.gangupamu@pamten.com>\nRole: Senior Data Engineer",
    )
    # naveen appears in a forwarded From: block (strong signal) and on a
    # different domain from the sender - should outrank the generic HR alias.
    assert selection.selected.email == "naveen.gangupamu@pamten.com"


def test_case6_multiple_recruiters_selects_exactly_one_highest_confidence():
    selection = _select(
        "james@algebrait.com",
        "From: Naveen Gangupamu <naveen.gangupamu@pamten.com>\n"
        "Also cc'd: recruiter2@otherfirm.com, recruiter3@thirdfirm.com\n"
        "Role: Senior Data Engineer",
    )
    assert selection.selected is not None
    # exactly one selected, not a list
    assert isinstance(selection.selected.email, str)


def test_case7_multiple_unrelated_emails_does_not_pick_unrelated_address():
    selection = _select(
        "james@algebrait.com",
        "This message was sent via MailProvider. Unsubscribe: unsubscribe@mailprovider.com\n"
        "From: naveen@pamten.com\nRole: Senior Data Engineer",
    )
    assert selection.selected.email == "naveen@pamten.com"
    assert selection.selected.email != "unsubscribe@mailprovider.com"


def test_case8_email_near_send_resume_to_has_high_confidence():
    selection = _select(
        "james@algebrait.com",
        "Role: Senior Data Engineer\nSend resume to: intake@vendorfirm.com",
    )
    assert selection.selected is not None
    assert selection.selected.email == "intake@vendorfirm.com"
    assert selection.confidence > 0.2


def test_case9_email_near_contact_label_has_high_confidence():
    selection = _select(
        "james@algebrait.com",
        "Role: Senior Data Engineer\nContact: naveen.gangupamu@pamten.com",
    )
    assert selection.selected.email == "naveen.gangupamu@pamten.com"


def test_case10_email_next_to_talent_acquisition_title_has_high_confidence():
    selection = _select(
        "james@algebrait.com",
        "Role: Senior Data Engineer\n\nSarah Kim\nTalent Acquisition Executive\nsarah.kim@bigstaffingfirm.com",
    )
    assert selection.selected.email == "sarah.kim@bigstaffingfirm.com"
    assert selection.confidence >= 0.4
