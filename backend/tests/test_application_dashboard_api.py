"""
DB-01..DB-15: the application-tracker dashboard API - KPI counts, search,
filters (individually and combined), sorting, detail page, recruiter link,
CSV export, and status-transition endpoint.
"""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models import Application, AppStatus, ProcessedMessage, ProcessingStatus, Recruiter, Resume


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


def _seed_message(db, msg_id, thread_id):
    message = ProcessedMessage(
        gmail_message_id=msg_id, thread_id=thread_id, from_email="james@algebrait.com",
        status=ProcessingStatus.DRAFT_CREATED,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _seed_recruiter(db, email="naveen@pamten.com", name="Naveen Gangupamu", company="PAMTEN"):
    recruiter = Recruiter(
        normalized_email=email, display_name=name, company=company, phone="(737) 304-8920",
        source_email_addresses=[email],
    )
    db.add(recruiter)
    db.commit()
    db.refresh(recruiter)
    return recruiter


def _seed_resume(db, filename="databricks_resume.pdf"):
    resume = Resume(filename=filename, file_path=f"/tmp/{filename}", indexing_status="INDEXED")
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def _seed_application(db, session_factory=None, **overrides):
    defaults = dict(
        recruiter_name="Naveen Gangupamu", recruiter_email="naveen@pamten.com",
        job_title="Senior Data Engineer", job_location="Irvine, CA",
        local_requirement="PREFERRED", implementation_partner="PAMTEN", end_client="ABC Financial",
        status=AppStatus.DRAFT,
    )
    defaults.update(overrides)
    message = _seed_message(db, overrides.pop("_msg_id", None) or f"msg-{id(overrides)}-{dt.datetime.now().timestamp()}",
                             overrides.pop("_thread_id", None) or f"thread-{dt.datetime.now().timestamp()}")
    application = Application(source_message_id=message.id, thread_id=message.thread_id, **defaults)
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


# --- DB-01: KPI counts -----------------------------------------------------


def test_db01_dashboard_kpi_counts_are_correct(client):
    c, sf = client
    db = sf()
    _seed_application(db, status=AppStatus.SENT)
    _seed_application(db, status=AppStatus.SUBMITTED)
    _seed_application(db, status=AppStatus.INTERVIEW)
    _seed_application(db, status=AppStatus.REJECTED)
    db.close()

    resp = c.get("/api/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sent_count"] == 1
    assert data["submitted_count"] == 1
    assert data["interview_count"] == 1
    assert data["rejected_count"] == 1


def test_job_opportunities_excludes_own_sent_emails(client):
    """A message the mailbox owner sent themselves was never a candidate
    opportunity in the first place - it must be excluded from
    job_opportunities the same way replies/duplicates are, not counted as a
    found-but-unhandled opportunity."""
    c, sf = client
    db = sf()
    today = dt.datetime.now(dt.UTC)
    db.add(ProcessedMessage(
        gmail_message_id="own1", thread_id="own1-t", from_email="me@example.com",
        status=ProcessingStatus.SKIPPED_OWN_SENT_EMAIL, processed_at=today,
    ))
    db.add(ProcessedMessage(
        gmail_message_id="job1", thread_id="job1-t", from_email="james@algebrait.com",
        status=ProcessingStatus.DRAFT_CREATED, processed_at=today,
    ))
    db.commit()
    db.close()

    resp = c.get("/api/dashboard/stats")
    data = resp.json()
    assert data["emails_processed"] == 2
    assert data["job_opportunities"] == 1  # only the genuine job email counts


def test_dashboard_never_shows_interview_rate_when_no_submitted_applications(client):
    c, sf = client
    resp = c.get("/api/dashboard/stats")
    assert resp.json()["interview_rate"] is None


def test_dashboard_computes_interview_rate_when_meaningful(client):
    c, sf = client
    db = sf()
    _seed_application(db, status=AppStatus.SUBMITTED)
    _seed_application(db, status=AppStatus.INTERVIEW)
    db.close()
    resp = c.get("/api/dashboard/stats")
    assert resp.json()["interview_rate"] == 50.0


# --- DB-02..DB-05: search --------------------------------------------------


def test_db02_search_by_recruiter_name(client):
    c, sf = client
    db = sf()
    _seed_application(db, recruiter_name="Naveen Gangupamu")
    _seed_application(db, recruiter_name="John Smith", recruiter_email="john@abc.com")
    db.close()
    resp = c.get("/api/applications", params={"search": "Naveen"})
    assert len(resp.json()) == 1


def test_db03_search_by_recruiter_email(client):
    c, sf = client
    db = sf()
    _seed_application(db, recruiter_email="naveen@pamten.com")
    db.close()
    resp = c.get("/api/applications", params={"search": "pamten.com"})
    assert len(resp.json()) == 1


def test_db04_search_by_status(client):
    c, sf = client
    db = sf()
    _seed_application(db, status=AppStatus.SUBMITTED)
    _seed_application(db, status=AppStatus.DRAFT)
    db.close()
    resp = c.get("/api/applications", params={"search": "SUBMITTED"})
    assert len(resp.json()) == 1


def test_db05_search_by_job_title(client):
    c, sf = client
    db = sf()
    _seed_application(db, job_title="Senior Data Engineer")
    _seed_application(db, job_title="AWS Cloud Architect")
    db.close()
    resp = c.get("/api/applications", params={"search": "Databricks Resume"})
    assert len(resp.json()) == 0
    resp2 = c.get("/api/applications", params={"search": "Data Engineer"})
    assert len(resp2.json()) == 1


def test_search_is_case_insensitive(client):
    c, sf = client
    db = sf()
    _seed_application(db, job_title="Senior Data Engineer")
    db.close()
    assert len(c.get("/api/applications", params={"search": "data engineer"}).json()) == 1


def test_empty_search_returns_everything(client):
    c, sf = client
    db = sf()
    _seed_application(db)
    _seed_application(db, recruiter_email="john@abc.com")
    db.close()
    assert len(c.get("/api/applications").json()) == 2


# --- DB-06..DB-10: filters --------------------------------------------------


def test_db06_filter_by_location(client):
    c, sf = client
    db = sf()
    _seed_application(db, job_location="Irvine, CA")
    _seed_application(db, job_location="Dallas, TX")
    db.close()
    resp = c.get("/api/applications", params={"location": "Irvine"})
    assert len(resp.json()) == 1


def test_db07_filter_by_local_requirement(client):
    c, sf = client
    db = sf()
    _seed_application(db, local_requirement="YES")
    _seed_application(db, local_requirement="NO")
    db.close()
    resp = c.get("/api/applications", params={"local_requirement": "YES"})
    assert len(resp.json()) == 1


def test_db08_filter_by_implementation_partner(client):
    c, sf = client
    db = sf()
    _seed_application(db, implementation_partner="PAMTEN")
    _seed_application(db, implementation_partner="Algebra IT")
    db.close()
    resp = c.get("/api/applications", params={"implementation_partner": "PAMTEN"})
    assert len(resp.json()) == 1


def test_db09_filter_by_end_client(client):
    c, sf = client
    db = sf()
    _seed_application(db, end_client="ABC Financial")
    _seed_application(db, end_client="JPMorgan")
    db.close()
    resp = c.get("/api/applications", params={"end_client": "JPMorgan"})
    assert len(resp.json()) == 1


def test_db10_filter_by_resume(client):
    c, sf = client
    db = sf()
    resume = _seed_resume(db)
    resume_id = resume.id
    _seed_application(db, selected_resume_id=resume_id)
    _seed_application(db)
    db.close()
    resp = c.get("/api/applications", params={"resume_id": resume_id})
    assert len(resp.json()) == 1


def test_db11_filter_by_date_range(client):
    c, sf = client
    db = sf()
    old = _seed_application(db)
    old.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=60)
    db.add(old)
    db.commit()
    _seed_application(db)  # created now
    db.close()
    resp = c.get("/api/applications", params={"date_range": "last_30_days"})
    assert len(resp.json()) == 1


def test_db12_multiple_filters_combine(client):
    c, sf = client
    db = sf()
    _seed_application(db, status=AppStatus.SUBMITTED, job_location="Irvine, CA",
                       implementation_partner="PAMTEN")
    _seed_application(db, status=AppStatus.DRAFT, job_location="Irvine, CA",
                       implementation_partner="PAMTEN")
    _seed_application(db, status=AppStatus.SUBMITTED, job_location="Dallas, TX",
                       implementation_partner="PAMTEN")
    db.close()
    resp = c.get("/api/applications", params={"status": "SUBMITTED", "location": "Irvine"})
    data = resp.json()
    assert len(data) == 1
    assert data[0]["job_location"] == "Irvine, CA"


# --- DB-13: sorting ----------------------------------------------------


def test_db13_sorting_by_job_title(client):
    c, sf = client
    db = sf()
    _seed_application(db, job_title="Zebra Engineer")
    _seed_application(db, job_title="Alpha Engineer")
    db.close()
    resp = c.get("/api/applications", params={"sort_by": "job_title", "order": "asc"})
    titles = [a["job_title"] for a in resp.json()]
    assert titles == ["Alpha Engineer", "Zebra Engineer"]


def test_default_sort_is_newest_first(client):
    c, sf = client
    db = sf()
    first = _seed_application(db, job_title="First")
    first.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    db.add(first)
    db.commit()
    _seed_application(db, job_title="Second")
    db.close()
    resp = c.get("/api/applications")
    assert resp.json()[0]["job_title"] == "Second"


# --- DB-14: detail page ------------------------------------------------


def test_db14_application_detail_page_loads_with_events(client):
    c, sf = client
    db = sf()
    app_row = _seed_application(db)
    db.close()

    resp = c.get(f"/api/applications/{app_row.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_title"] == "Senior Data Engineer"
    assert data["events"] == []  # no events for a directly-seeded row, but the field is present


def test_application_detail_not_found(client):
    c, _ = client
    assert c.get("/api/applications/999999").status_code == 404


# --- DB-15: recruiter link ----------------------------------------------


def test_db15_application_links_to_recruiter_profile(client):
    c, sf = client
    db = sf()
    recruiter = _seed_recruiter(db)
    recruiter_id = recruiter.id
    app_row = _seed_application(db, recruiter_id=recruiter_id)
    app_id = app_row.id
    db.close()

    resp = c.get(f"/api/applications/{app_id}")
    data = resp.json()
    assert data["recruiter"]["id"] == recruiter_id
    assert data["recruiter"]["phone"] == "(737) 304-8920"
    assert data["recruiter"]["company"] == "PAMTEN"


# --- CSV export ----------------------------------------------------------


def test_csv_export_contains_expected_fields(client):
    c, sf = client
    db = sf()
    recruiter = _seed_recruiter(db)
    _seed_application(db, recruiter_id=recruiter.id, status=AppStatus.SUBMITTED)
    db.close()

    resp = c.get("/api/applications/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.text
    assert "Recruiter Phone" in body
    assert "Naveen Gangupamu" in body
    assert "PAMTEN" in body
    assert "SUBMITTED" in body


def test_csv_export_never_contains_oauth_token(client):
    c, sf = client
    db = sf()
    _seed_application(db)
    db.close()
    resp = c.get("/api/applications/export.csv")
    assert "token" not in resp.text.lower()


# --- status transition endpoint -----------------------------------------


def test_status_endpoint_allows_valid_transition(client):
    c, sf = client
    db = sf()
    app_row = _seed_application(db, status=AppStatus.DRAFT)
    db.close()
    resp = c.post(f"/api/applications/{app_row.id}/status", json={"status": "SENT"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "SENT"


def test_status_endpoint_rejects_invalid_transition(client):
    c, sf = client
    db = sf()
    app_row = _seed_application(db, status=AppStatus.DRAFT)
    db.close()
    resp = c.post(f"/api/applications/{app_row.id}/status", json={"status": "INTERVIEW"})
    assert resp.status_code == 400


def test_status_endpoint_allows_override(client):
    c, sf = client
    db = sf()
    app_row = _seed_application(db, status=AppStatus.DRAFT)
    db.close()
    resp = c.post(f"/api/applications/{app_row.id}/status", json={"status": "INTERVIEW", "override": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "INTERVIEW"


def test_creating_a_draft_alone_never_marks_sent_or_submitted(client):
    """Section 2's critical tracking rule, verified through the real HTTP API."""
    c, sf = client
    db = sf()
    app_row = _seed_application(db, status=AppStatus.DRAFT)
    db.close()
    resp = c.get(f"/api/applications/{app_row.id}")
    data = resp.json()
    assert data["status"] == "DRAFT"
    assert data["sent_at"] is None
    assert data["submitted_at"] is None


def test_sync_sent_endpoint_returns_zero_when_gmail_not_connected(client):
    c, _ = client
    resp = c.post("/api/applications/sync-sent")
    assert resp.status_code == 200
    assert resp.json()["updated"] == 0


def test_dashboard_charts_endpoint(client):
    c, sf = client
    db = sf()
    _seed_application(db, status=AppStatus.DRAFT, job_location="Irvine, CA")
    _seed_application(db, status=AppStatus.SUBMITTED, job_location="Irvine, CA")
    db.close()
    resp = c.get("/api/dashboard/charts")
    assert resp.status_code == 200
    data = resp.json()
    assert sum(p["value"] for p in data["applications_by_status"]) == 2
