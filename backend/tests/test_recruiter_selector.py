from __future__ import annotations

from app.services.email_parser import parse_gmail_message
from app.services.recruiter_selector import select_recruiter_email
from tests.conftest import make_raw_message
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


def test_selects_forwarded_recruiter_not_sender():
    raw = make_raw_message(
        message_id="r1", thread_id="rt1",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    parsed = parse_gmail_message(raw)
    selection = select_recruiter_email(parsed, my_email="diwakar@example.com")

    assert selection.selected is not None
    assert selection.selected.email == "naveen.gangupamu@pamten.com"
    assert selection.selected.email != parsed.from_email


def test_excludes_own_email_from_candidates():
    raw = make_raw_message(
        message_id="r2", thread_id="rt2",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT,
        plain_body=SAMPLE_BODY + "\ncc: diwakar@example.com\n",
    )
    parsed = parse_gmail_message(raw)
    selection = select_recruiter_email(parsed, my_email="diwakar@example.com")

    assert all(c.email != "diwakar@example.com" for c in selection.all_candidates)


def test_no_candidates_returns_none_and_zero_confidence():
    raw = make_raw_message(
        message_id="r3", thread_id="rt3",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject="Role: Data Engineer",
        plain_body="Role: Data Engineer\nNo other contact info here.",
    )
    parsed = parse_gmail_message(raw)
    selection = select_recruiter_email(parsed, my_email="diwakar@example.com")

    assert selection.selected is None
    assert selection.confidence == 0.0


def test_googlegroups_relay_address_is_never_selected_as_recruiter():
    """Real-world case: a Google Groups digest forward shows the group's own
    address in the 'From:' line before 'on behalf of' the actual recruiter -
    the group address must never be picked as the recruiter contact, even
    when it's the only email that would otherwise score highest."""
    body = (
        "Jenny\nLead Recruiter\nAlgebra IT LLC\n\n"
        "________________________________\n"
        "From: c2c-vendor-125@googlegroups.com <c2c-vendor-125@googlegroups.com> "
        "on behalf of Adarsh Tiwari <adarsh@kk-talents.com>\n"
        "Sent: Wednesday, September 9, 2026 12:17 AM\n"
        "Subject: Urgent hiring for AWS Data Engineer\n\n"
        "Role: AWS Data Engineer\nLocation: Remote\n\n"
        "Thanks,\nAdarsh Tiwari\n"
        "To unsubscribe, email c2c-vendor-125+unsubscribe@googlegroups.com\n"
    )
    raw = make_raw_message(
        message_id="r-gg1", thread_id="rt-gg1",
        from_header="Jenny <jenny@algebrait.com>",
        subject="Fw: Urgent hiring for AWS Data Engineer", plain_body=body,
    )
    parsed = parse_gmail_message(raw)
    selection = select_recruiter_email(parsed, my_email="diwakar@example.com")

    assert selection.selected is not None
    assert "googlegroups.com" not in selection.selected.email
    assert selection.selected.email == "adarsh@kk-talents.com"
    assert all("googlegroups.com" not in c.email for c in selection.all_candidates)
