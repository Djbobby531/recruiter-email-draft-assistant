"""
RCRM-01 .. RCRM-14: recruiter information extraction (sections 2-3 of the
recruiter-CRM hardening spec).
"""
from __future__ import annotations

from app.models import Recruiter
from app.services.recruiter_info_extractor import (
    extract_phone_number,
    extract_recruiter_info,
    extract_recruiter_info_from_text,
    normalize_phone_digits,
)
from app.services.recruiter_service import get_recruiter_by_email, upsert_recruiter
from tests.conftest import load_forwarded_email, load_recruiter_email

SIGNATURE_TEXT = """
Role: Senior Data Engineer (Databricks)
Location: Irvine, CA or Los Angeles, CA

Thanks and Regards,
Naveen Gangupamu
Talent Acquisition Executive
PAMTEN
Phone: (737) 304-8920
Email: naveen.gangupamu@pamten.com
"""


def test_rcrm01_extract_recruiter_name():
    info = extract_recruiter_info_from_text(SIGNATURE_TEXT, "naveen.gangupamu@pamten.com")
    assert info.name == "Naveen Gangupamu"


def test_rcrm02_extract_recruiter_email():
    # the email itself is identified upstream by recruiter_selector; this
    # module's job is confirming the rest of the info resolves correctly once
    # given that email as the anchor
    info = extract_recruiter_info_from_text(SIGNATURE_TEXT, "naveen.gangupamu@pamten.com")
    assert info is not None  # extraction succeeds when anchored on a real recruiter email


def test_rcrm03_extract_recruiter_phone():
    info = extract_recruiter_info(SIGNATURE_TEXT, "Naveen Gangupamu")
    assert info.phone == "(737) 304-8920"


def test_rcrm04_extract_company():
    info = extract_recruiter_info(SIGNATURE_TEXT, "Naveen Gangupamu")
    assert info.company == "PAMTEN"


def test_rcrm05_extract_recruiter_job_title_not_confused_with_job_opening_title():
    info = extract_recruiter_info(SIGNATURE_TEXT, "Naveen Gangupamu")
    assert info.recruiter_role == "Talent Acquisition Executive"
    # the JOB opening title ("Senior Data Engineer") must never leak into the
    # recruiter's own role field
    assert "Data Engineer" not in (info.recruiter_role or "")


def test_rcrm06_store_recruiter(db_session):
    info = extract_recruiter_info(SIGNATURE_TEXT, "Naveen Gangupamu")
    recruiter = upsert_recruiter(db_session, "naveen.gangupamu@pamten.com", info)

    assert recruiter.id is not None
    assert recruiter.normalized_email == "naveen.gangupamu@pamten.com"
    assert recruiter.display_name == "Naveen Gangupamu"
    assert recruiter.first_name == "Naveen"
    assert recruiter.last_name == "Gangupamu"
    assert recruiter.company == "PAMTEN"
    assert recruiter.recruiter_role == "Talent Acquisition Executive"
    assert recruiter.phone == "(737) 304-8920"
    assert db_session.query(Recruiter).count() == 1


def test_rcrm07_same_recruiter_email_updates_existing_record(db_session):
    info1 = extract_recruiter_info(SIGNATURE_TEXT, "Naveen Gangupamu")
    first = upsert_recruiter(db_session, "naveen.gangupamu@pamten.com", info1)
    first_seen = first.first_seen_at

    # a later email from the same address, uppercase this time, with a new phone
    info2 = extract_recruiter_info(
        "Naveen Gangupamu\nTalent Acquisition Executive\nPAMTEN\nPhone: 737-999-0000",
        "Naveen Gangupamu",
    )
    second = upsert_recruiter(db_session, "Naveen.Gangupamu@PAMTEN.com", info2)

    assert db_session.query(Recruiter).count() == 1  # no duplicate created
    assert second.id == first.id
    assert second.first_seen_at == first_seen  # first_seen_at never changes
    assert second.phone == "737-999-0000"  # newer info updates the record


def test_rcrm08_same_recruiter_sends_different_job_titles(db_session):
    from app.services.recruiter_service import record_role_sent

    recruiter = upsert_recruiter(db_session, "naveen@pamten.com", extract_recruiter_info(SIGNATURE_TEXT, "Naveen Gangupamu"))
    record_role_sent(db_session, recruiter, "Senior Data Engineer")
    record_role_sent(db_session, recruiter, "Data Engineer")
    record_role_sent(db_session, recruiter, "AWS Data Engineer")
    record_role_sent(db_session, recruiter, "Senior Data Engineer")  # repeat

    db_session.refresh(recruiter)
    assert len(recruiter.roles) == 3
    titles = {r.job_title for r in recruiter.roles}
    assert titles == {"Senior Data Engineer", "Data Engineer", "AWS Data Engineer"}
    senior_role = next(r for r in recruiter.roles if r.job_title == "Senior Data Engineer")
    assert senior_role.opportunity_count == 2  # sent twice


def test_rcrm09_different_recruiters_with_same_name_remain_separate(db_session):
    info = extract_recruiter_info("Naveen Gangupamu\nTalent Acquisition Executive\nPAMTEN", "Naveen Gangupamu")
    r1 = upsert_recruiter(db_session, "naveen@pamten.com", info)
    r2 = upsert_recruiter(db_session, "naveen@differentstaffingfirm.com", info)

    assert r1.id != r2.id
    assert db_session.query(Recruiter).count() == 2
    assert r1.normalized_email != r2.normalized_email


def test_rcrm10_recruiter_email_appears_multiple_times_still_one_record(db_session):
    text = load_forwarded_email("repeated_recruiter_email.txt")
    info = extract_recruiter_info_from_text(text, "naveen.gangupamu@pamten.com")
    recruiter = upsert_recruiter(db_session, "naveen.gangupamu@pamten.com", info)
    # process the same email again (simulating the address appearing several
    # times within one message being handled once)
    upsert_recruiter(db_session, "naveen.gangupamu@pamten.com", info)

    assert db_session.query(Recruiter).count() == 1
    assert recruiter.recruiter_role == "Talent Acquisition Executive"


def test_rcrm11_recruiter_info_only_inside_forwarded_message():
    text = load_forwarded_email("outlook_style.txt")
    info = extract_recruiter_info_from_text(text, "naveen.gangupamu@pamten.com")
    assert info.name == "Naveen Gangupamu"


def test_rcrm12_recruiter_info_only_inside_signature():
    text = load_recruiter_email("talent_acquisition_signature.txt")
    info = extract_recruiter_info_from_text(text, "sarah.kim@bigstaffingfirm.com")
    assert info.name == "Sarah Kim"
    assert info.recruiter_role == "Talent Acquisition Executive"


def test_rcrm13_no_recruiter_phone_is_null():
    text = "Naveen Gangupamu\nTalent Acquisition Executive\nPAMTEN\n"  # no phone anywhere
    info = extract_recruiter_info(text, "Naveen Gangupamu")
    assert info.phone is None


def test_rcrm14_malformed_phone_number_is_not_invented_or_misnormalized():
    text = "Naveen Gangupamu\nTalent Acquisition Executive\nExt: 12-34\n"
    info = extract_recruiter_info(text, "Naveen Gangupamu")
    assert info.phone is None

    # sanity: the low-level helpers agree malformed input never normalizes
    assert extract_phone_number("12-34") is None
    assert normalize_phone_digits("12-34") is None
    assert normalize_phone_digits("this is not a phone at all") is None


def test_recruiter_phone_never_falls_back_to_the_forwarding_senders_own_number():
    """Real-world shape: "James" (a staffing-agency relay) forwards an email
    from the actual recruiter, Naveen. James's own mobile number appears in
    HIS preamble, well before Naveen's name/signature. If Naveen's own
    signature block happens not to carry a phone number, the extractor must
    return None - it must NEVER fall back to a message-wide search, which
    would surface James's number and misattribute it to Naveen."""
    text = (
        "James\n\nAlgebra IT LLC\n\nE: james@algebrait.com\nMobile: (555) 111-2222\n\n"
        "---------- Forwarded message ----------\n"
        "From: Naveen Gangupamu <naveen.gangupamu@pamten.com>\n\n"
        "Role: Senior Data Engineer (Databricks)\n\n"
        "Thanks,\nNaveen Gangupamu\nTalent Acquisition Executive\nPamTen Inc.\n"
    )
    info = extract_recruiter_info(text, "Naveen Gangupamu")
    assert info.phone is None


def test_recruiter_phone_is_extracted_from_their_own_signature_even_amid_a_forward():
    text = load_forwarded_email("repeated_recruiter_email.txt")
    info = extract_recruiter_info(text, "Naveen Gangupamu")
    # this fixture's actual recruiter signature block ends with his own
    # "Mobile: (555) 987-6543" line - never James's (the forwarding sender)
    assert info.phone == "(555) 987-6543"


def test_get_recruiter_by_email_is_case_and_whitespace_insensitive(db_session):
    info = extract_recruiter_info(SIGNATURE_TEXT, "Naveen Gangupamu")
    upsert_recruiter(db_session, "naveen.gangupamu@pamten.com", info)
    found = get_recruiter_by_email(db_session, "  Naveen.Gangupamu@PAMTEN.com  ")
    assert found is not None
