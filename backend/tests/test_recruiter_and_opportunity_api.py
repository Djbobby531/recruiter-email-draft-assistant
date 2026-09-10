"""
FastAPI TestClient coverage for the recruiter directory + opportunity/skipped
views + manual override endpoint (sections 5-7, 18, 26-27).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models import (
    CandidateProfile,
    Opportunity,
    ProcessedMessage,
    ProcessingStatus,
    Recruiter,
    Resume,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path/'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    test_settings = Settings(RESUME_DIR=str(tmp_path / "resumes"))
    app.dependency_overrides[get_settings] = lambda: test_settings

    async def _noop_polling_loop(stop_event):
        await stop_event.wait()

    monkeypatch.setattr(main_module, "polling_loop", _noop_polling_loop)

    with TestClient(app) as c:
        yield c, TestSessionLocal

    app.dependency_overrides.clear()


def _seed_recruiter(
    session_factory, email="naveen@pamten.com", name="Naveen Gangupamu", company="PAMTEN",
    role="Talent Acquisition Executive", phone="(737) 304-8920",
):
    db = session_factory()
    recruiter = Recruiter(
        normalized_email=email, display_name=name, first_name=name.split()[0], last_name=name.split()[-1],
        company=company, recruiter_role=role, phone=phone, source_email_addresses=[email],
        opportunities_count=7, drafts_count=5, skipped_count=2,
    )
    db.add(recruiter)
    db.commit()
    db.refresh(recruiter)
    rid = recruiter.id
    db.close()
    return rid


def test_list_recruiters_empty_state(client):
    c, _ = client
    resp = c.get("/api/recruiters")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_recruiters_returns_seeded_recruiter(client):
    c, session_factory = client
    _seed_recruiter(session_factory)
    resp = c.get("/api/recruiters")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["display_name"] == "Naveen Gangupamu"
    assert data[0]["company"] == "PAMTEN"
    assert data[0]["opportunities_count"] == 7


def test_search_recruiters_by_name_email_company(client):
    c, session_factory = client
    _seed_recruiter(session_factory, email="naveen@pamten.com", name="Naveen Gangupamu", company="PAMTEN")
    _seed_recruiter(session_factory, email="james@algebrait.com", name="James Smith", company="ABC Staffing")

    assert len(c.get("/api/recruiters", params={"search": "Naveen"}).json()) == 1
    assert len(c.get("/api/recruiters", params={"search": "pamten.com"}).json()) == 1
    assert len(c.get("/api/recruiters", params={"search": "ABC Staffing"}).json()) == 1
    assert len(c.get("/api/recruiters", params={"search": "nonexistent"}).json()) == 0


def test_search_recruiters_by_job_title(client):
    c, session_factory = client
    rid = _seed_recruiter(session_factory)
    db = session_factory()
    from app.models import RecruiterRole
    db.add(RecruiterRole(recruiter_id=rid, job_title="Senior Data Engineer", opportunity_count=3))
    db.commit()
    db.close()

    resp = c.get("/api/recruiters", params={"search": "Senior Data Engineer"})
    assert len(resp.json()) == 1


def test_unique_phone_filter_collapses_duplicate_phone_numbers(client):
    """The same person can legitimately exist as multiple recruiter records
    under different email addresses (section 7) while sharing one real phone
    number - unique_phone=true should collapse those down to a single row,
    keeping whichever sorts first (most-recently-seen, by default)."""
    import datetime as dt

    c, session_factory = client
    _seed_recruiter(session_factory, email="naveen@pamten.com", name="Naveen Gangupamu", phone="(737) 304-8920")
    _seed_recruiter(session_factory, email="naveen.g@otherfirm.com", name="Naveen Gangupamu", phone="(737) 304-8920")
    _seed_recruiter(session_factory, email="sarah@bigstaffing.com", name="Sarah Kim", phone="(555) 222-3344")

    db = session_factory()
    now = dt.datetime.now(dt.UTC)
    db.query(Recruiter).filter(Recruiter.normalized_email == "naveen@pamten.com").update(
        {"last_seen_at": now - dt.timedelta(days=1)}
    )
    db.query(Recruiter).filter(Recruiter.normalized_email == "naveen.g@otherfirm.com").update(
        {"last_seen_at": now}
    )
    db.commit()
    db.close()

    resp = c.get("/api/recruiters", params={"unique_phone": True})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    emails = {r["normalized_email"] for r in data}
    assert emails == {"naveen.g@otherfirm.com", "sarah@bigstaffing.com"}  # more-recent Naveen record wins


def test_unique_phone_filter_normalizes_different_formats_of_the_same_number(client):
    c, session_factory = client
    _seed_recruiter(session_factory, email="a@x.com", name="A", phone="(737) 304-8920")
    _seed_recruiter(session_factory, email="b@x.com", name="B", phone="737-304-8920")

    resp = c.get("/api/recruiters", params={"unique_phone": True})
    assert len(resp.json()) == 1


def test_unique_phone_filter_excludes_recruiters_with_no_phone_recorded(client):
    c, session_factory = client
    _seed_recruiter(session_factory, email="has-phone@x.com", name="Has Phone", phone="(737) 304-8920")
    _seed_recruiter(session_factory, email="no-phone@x.com", name="No Phone", phone=None)

    resp = c.get("/api/recruiters", params={"unique_phone": True})
    data = resp.json()
    assert len(data) == 1
    assert data[0]["normalized_email"] == "has-phone@x.com"


def test_unique_phone_filter_off_by_default_keeps_duplicates(client):
    c, session_factory = client
    _seed_recruiter(session_factory, email="a@x.com", name="A", phone="(737) 304-8920")
    _seed_recruiter(session_factory, email="b@x.com", name="B", phone="(737) 304-8920")

    resp = c.get("/api/recruiters")
    assert len(resp.json()) == 2


def test_sort_recruiters_by_opportunities_count(client):
    c, session_factory = client
    _seed_recruiter(session_factory, email="low@x.com", name="Low Volume")
    db = session_factory()
    db.query(Recruiter).filter(Recruiter.normalized_email == "low@x.com").update({"opportunities_count": 1})
    db.commit()
    db.close()
    _seed_recruiter(session_factory, email="high@x.com", name="High Volume")
    db2 = session_factory()
    db2.query(Recruiter).filter(Recruiter.normalized_email == "high@x.com").update({"opportunities_count": 99})
    db2.commit()
    db2.close()

    resp = c.get("/api/recruiters", params={"sort_by": "opportunities_count", "order": "desc"})
    data = resp.json()
    assert data[0]["normalized_email"] == "high@x.com"
    assert data[1]["normalized_email"] == "low@x.com"


def test_top_recruiters_ranks_by_volume_not_quality(client):
    c, session_factory = client
    _seed_recruiter(session_factory, email="a@x.com", name="A")
    db = session_factory()
    db.query(Recruiter).filter(Recruiter.normalized_email == "a@x.com").update({"opportunities_count": 3})
    db.commit()
    db.close()
    _seed_recruiter(session_factory, email="b@x.com", name="B")
    db2 = session_factory()
    db2.query(Recruiter).filter(Recruiter.normalized_email == "b@x.com").update({"opportunities_count": 12})
    db2.commit()
    db2.close()

    resp = c.get("/api/recruiters/top")
    data = resp.json()
    assert data[0]["normalized_email"] == "b@x.com"  # highest volume first


def test_get_recruiter_detail_includes_roles(client):
    c, session_factory = client
    rid = _seed_recruiter(session_factory)
    db = session_factory()
    from app.models import RecruiterRole
    db.add(RecruiterRole(recruiter_id=rid, job_title="Senior Data Engineer", opportunity_count=2))
    db.add(RecruiterRole(recruiter_id=rid, job_title="AWS Data Engineer", opportunity_count=1))
    db.commit()
    db.close()

    resp = c.get(f"/api/recruiters/{rid}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["roles"]) == 2
    titles = {r["job_title"] for r in data["roles"]}
    assert titles == {"Senior Data Engineer", "AWS Data Engineer"}


def test_get_recruiter_not_found_returns_404(client):
    c, _ = client
    assert c.get("/api/recruiters/99999").status_code == 404


def test_update_recruiter_notes(client):
    c, session_factory = client
    rid = _seed_recruiter(session_factory)
    resp = c.patch(f"/api/recruiters/{rid}", json={"notes": "Very responsive, prefers email."})
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Very responsive, prefers email."


def test_link_recruiter_to_another_person_record(client):
    c, session_factory = client
    rid1 = _seed_recruiter(session_factory, email="naveen@pamten.com")
    rid2 = _seed_recruiter(session_factory, email="naveen.personal@gmail.com", name="Naveen Gangupamu")

    resp = c.patch(f"/api/recruiters/{rid2}", json={"linked_recruiter_id": rid1})
    assert resp.status_code == 200
    assert resp.json()["linked_recruiter_id"] == rid1

    # the two remain SEPARATE recruiter records - linking never merges them
    assert len(c.get("/api/recruiters").json()) == 2


def test_cannot_link_recruiter_to_itself(client):
    c, session_factory = client
    rid = _seed_recruiter(session_factory)
    resp = c.patch(f"/api/recruiters/{rid}", json={"linked_recruiter_id": rid})
    assert resp.status_code == 400


def test_delete_single_recruiter(client):
    c, session_factory = client
    rid = _seed_recruiter(session_factory)
    assert c.delete(f"/api/recruiters/{rid}").status_code == 200
    assert c.get("/api/recruiters").json() == []


def test_delete_all_recruiter_data(client):
    c, session_factory = client
    _seed_recruiter(session_factory, email="a@x.com")
    _seed_recruiter(session_factory, email="b@x.com")
    resp = c.delete("/api/recruiters")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2
    assert c.get("/api/recruiters").json() == []


def _seed_skipped_opportunity(session_factory):
    db = session_factory()
    recruiter = Recruiter(
        normalized_email="naveen@pamten.com", display_name="Naveen Gangupamu", company="PAMTEN",
        recruiter_role="Talent Acquisition Executive", phone="(737) 304-8920",
        source_email_addresses=["naveen@pamten.com"], opportunities_count=1, skipped_count=1,
    )
    message = ProcessedMessage(
        gmail_message_id="skip-msg-1", thread_id="skip-thread-1", from_email="james@algebrait.com",
        subject="Role: Senior Data Engineer", status=ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW,
    )
    db.add_all([recruiter, message])
    db.commit()
    opportunity = Opportunity(
        recruiter_id=recruiter.id, source_message_id=message.id, thread_id="skip-thread-1",
        job_title="Senior Data Engineer (Databricks)", job_location="Irvine, CA",
        required_skills=["databricks", "python"], interview_type="IN_PERSON",
        requires_in_person_interview=True, interview_confidence=0.96,
        interview_reason="In-person interview requirement detected: in-person interview",
        interview_evidence="in-person interview",
        status=ProcessingStatus.SKIPPED_IN_PERSON_INTERVIEW,
        skip_reason="In-person interview requirement detected: in-person interview",
    )
    db.add(opportunity)
    db.commit()
    oid = opportunity.id
    db.close()
    return oid


def test_skipped_opportunities_list(client):
    c, session_factory = client
    _seed_skipped_opportunity(session_factory)
    resp = c.get("/api/opportunities/skipped")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "SKIPPED_IN_PERSON_INTERVIEW"
    assert data[0]["requires_in_person_interview"] is True
    assert data[0]["interview_evidence"] == "in-person interview"


def test_get_opportunity_detail(client):
    c, session_factory = client
    oid = _seed_skipped_opportunity(session_factory)
    resp = c.get(f"/api/opportunities/{oid}")
    assert resp.status_code == 200
    assert resp.json()["job_title"] == "Senior Data Engineer (Databricks)"


def test_delete_single_opportunity(client):
    c, session_factory = client
    oid = _seed_skipped_opportunity(session_factory)
    assert c.delete(f"/api/opportunities/{oid}").status_code == 200
    assert c.get("/api/opportunities/skipped").json() == []


def test_delete_all_opportunity_history(client):
    c, session_factory = client
    _seed_skipped_opportunity(session_factory)
    resp = c.delete("/api/opportunities")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1
    assert c.get("/api/opportunities/skipped").json() == []


def test_override_interview_skip_creates_draft(client, tmp_path):
    c, session_factory = client

    minimal_pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>/MediaBox[0 0 300 144]/Contents 5 0 R>>endobj\n"
        b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n5 0 obj<</Length 20>>stream\n"
        b"BT /F1 12 Tf 10 100 Td (Databricks Python SQL) Tj ET\nendstream\nendobj\n"
        b"xref\n0 6\n0000000000 65535 f \ntrailer<</Size 6/Root 1 0 R>>\nstartxref\n0\n%%EOF"
    )

    db = session_factory()
    db.add(CandidateProfile(
        name="Diwakar Jilakara", experience="8+ years", work_authorization="Authorized to work in the US",
        phone="555-123-4567", email="diwakar@example.com", linkedin="linkedin.com/in/diwakar",
    ))
    resume = Resume(
        filename="databricks_resume.pdf", file_path=str(tmp_path / "databricks_resume.pdf"),
        extracted_text="Databricks Python SQL",
        extracted_metadata={"skills": {"data_engineering_tools": ["databricks"], "languages": ["python", "sql"]},
                             "years_of_experience": 8, "job_titles": ["Senior Data Engineer"]},
        indexing_status="INDEXED",
    )
    db.add(resume)
    db.commit()
    (tmp_path / "databricks_resume.pdf").write_bytes(minimal_pdf)
    db.close()

    oid = _seed_skipped_opportunity(session_factory)

    resp = c.post(f"/api/opportunities/{oid}/override")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "DRAFT_CREATED"
    assert data["interview_overridden"] is True
    assert data["interview_override_at"] is not None
    # original classification is preserved even after the override
    assert data["original_interview_type"] == "IN_PERSON"
    assert data["original_requires_in_person_interview"] is True
    assert data["application_id"] is not None
    assert data["draft_id"] is not None

    # a second override attempt on the same (now-processed) opportunity is rejected
    resp2 = c.post(f"/api/opportunities/{oid}/override")
    assert resp2.status_code == 400


def test_override_non_skipped_opportunity_is_rejected(client):
    c, session_factory = client
    db = session_factory()
    recruiter = Recruiter(normalized_email="x@y.com", display_name="X", source_email_addresses=["x@y.com"])
    message = ProcessedMessage(gmail_message_id="m1", thread_id="t1", from_email="x@y.com", status=ProcessingStatus.DRAFT_CREATED)
    db.add_all([recruiter, message])
    db.commit()
    opportunity = Opportunity(
        recruiter_id=recruiter.id, source_message_id=message.id, thread_id="t1",
        status=ProcessingStatus.DRAFT_CREATED, interview_type="REMOTE", requires_in_person_interview=False,
    )
    db.add(opportunity)
    db.commit()
    oid = opportunity.id
    db.close()

    resp = c.post(f"/api/opportunities/{oid}/override")
    assert resp.status_code == 400


def test_dashboard_includes_in_person_interview_skipped_count(client):
    c, session_factory = client
    _seed_skipped_opportunity(session_factory)
    resp = c.get("/api/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["in_person_interview_skipped"] == 1
