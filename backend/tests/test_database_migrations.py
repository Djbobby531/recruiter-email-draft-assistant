"""
Section 37.9 - database migration tests. Simulates an existing local
data/db/app.db created by an older version of the app (missing the
dashboard-phase columns/table) and verifies the lightweight ALTER-TABLE
migration brings it up to date without data loss.
"""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

import app.database as database_module
from app.database import _add_missing_columns, _run_lightweight_migrations


def _make_old_schema_engine(tmp_path):
    db_path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE applications ("
            "id INTEGER PRIMARY KEY, source_message_id INTEGER, thread_id VARCHAR(128), "
            "recruiter_name VARCHAR(255), recruiter_email VARCHAR(255), created_at DATETIME"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO applications (id, source_message_id, thread_id, recruiter_name, recruiter_email) "
            "VALUES (1, 1, 't1', 'Naveen', 'naveen@pamten.com')"
        ))
        conn.execute(text(
            "CREATE TABLE opportunities ("
            "id INTEGER PRIMARY KEY, recruiter_id INTEGER, source_message_id INTEGER, thread_id VARCHAR(128)"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE processed_messages ("
            "id INTEGER PRIMARY KEY, gmail_message_id VARCHAR(128), thread_id VARCHAR(128), "
            "from_email VARCHAR(255), subject VARCHAR(1024)"
            ")"
        ))
    return engine


def test_migration_adds_missing_application_columns_without_dropping_existing_rows(tmp_path, monkeypatch):
    engine = _make_old_schema_engine(tmp_path)
    monkeypatch.setattr(database_module, "engine", engine)

    _run_lightweight_migrations()

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("applications")}
    for expected in ("status", "recruiter_id", "local_requirement", "implementation_partner",
                      "end_client", "sent_message_id", "sent_at", "submitted_at", "customized_resume_path"):
        assert expected in columns

    with engine.connect() as conn:
        row = conn.execute(text("SELECT recruiter_name, recruiter_email FROM applications WHERE id=1")).fetchone()
    assert row == ("Naveen", "naveen@pamten.com")  # pre-existing data preserved


def test_migration_adds_missing_opportunity_columns(tmp_path, monkeypatch):
    engine = _make_old_schema_engine(tmp_path)
    monkeypatch.setattr(database_module, "engine", engine)

    _run_lightweight_migrations()

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("opportunities")}
    assert {"local_requirement", "implementation_partner", "end_client"} <= columns


def test_migration_adds_missing_processed_message_columns(tmp_path, monkeypatch):
    engine = _make_old_schema_engine(tmp_path)
    monkeypatch.setattr(database_module, "engine", engine)

    _run_lightweight_migrations()

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("processed_messages")}
    assert "to_email" in columns


def test_migration_is_a_no_op_on_a_brand_new_schema(tmp_path, monkeypatch):
    """create_all() already creates every column on a fresh DB - the
    migration helper must not error or duplicate anything when re-run."""
    from app import models  # noqa: F401
    from app.database import Base

    db_path = tmp_path / "new.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database_module, "engine", engine)

    _run_lightweight_migrations()  # should not raise
    _run_lightweight_migrations()  # idempotent - running twice is safe

    inspector = inspect(engine)
    assert "application_events" in inspector.get_table_names()


def test_add_missing_columns_skips_nonexistent_table(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path/'empty.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database_module, "engine", engine)
    inspector = inspect(engine)
    _add_missing_columns(inspector, "applications", {"status": "VARCHAR(32)"})  # no-op, must not raise
