"""SQLAlchemy engine/session setup. SQLite, local file, single-user."""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

logger = logging.getLogger("app.database")

settings = get_settings()

engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added to `applications` after its first release. create_all() only
# creates missing TABLES, never missing COLUMNS on a table that already
# exists - this project has no Alembic (SQLite + create_all is enough for a
# single-user local app), so a lightweight ADD COLUMN pass covers the gap for
# anyone upgrading with an existing local data/db/app.db.
_APPLICATIONS_NEW_COLUMNS = {
    "interview_type": "VARCHAR(16)",
    "requires_in_person_interview": "BOOLEAN",
    "interview_confidence": "FLOAT",
    "interview_reason": "TEXT",
    "interview_evidence": "TEXT",
    # Application-tracker / dashboard phase
    "recruiter_id": "INTEGER",
    "local_requirement": "VARCHAR(16) DEFAULT 'UNKNOWN'",
    "implementation_partner": "VARCHAR(255)",
    "end_client": "VARCHAR(255)",
    "employment_type": "VARCHAR(64)",
    "status": "VARCHAR(32) DEFAULT 'DRAFT'",
    "interview_status": "VARCHAR(32) DEFAULT 'NOT_STARTED'",
    "interview_notes": "TEXT",
    "interview_at": "DATETIME",
    "sent_message_id": "VARCHAR(128)",
    "sent_at": "DATETIME",
    "submitted_at": "DATETIME",
    "skip_reason": "TEXT",
    "updated_at": "DATETIME",
    "customized_resume_path": "VARCHAR(1024)",
    "customization_source": "VARCHAR(32)",
}

_OPPORTUNITIES_NEW_COLUMNS = {
    "local_requirement": "VARCHAR(16) DEFAULT 'UNKNOWN'",
    "implementation_partner": "VARCHAR(255)",
    "end_client": "VARCHAR(255)",
}

_PROCESSED_MESSAGES_NEW_COLUMNS = {
    "to_email": "VARCHAR(255) DEFAULT ''",
}

_GMAIL_ACCOUNTS_NEW_COLUMNS = {
    "last_processed_at": "DATETIME",
}


def _add_missing_columns(inspector, table: str, columns: dict[str, str]) -> None:
    if table not in inspector.get_table_names():
        return  # brand-new DB - create_all() already created it with every column

    existing_columns = {col["name"] for col in inspector.get_columns(table)}
    missing = {name: ddl_type for name, ddl_type in columns.items() if name not in existing_columns}
    if not missing:
        return

    with engine.begin() as conn:
        for name, ddl_type in missing.items():
            logger.info("lightweight migration: adding %s.%s (%s)", table, name, ddl_type)
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))


def _run_lightweight_migrations() -> None:
    inspector = inspect(engine)
    _add_missing_columns(inspector, "applications", _APPLICATIONS_NEW_COLUMNS)
    _add_missing_columns(inspector, "opportunities", _OPPORTUNITIES_NEW_COLUMNS)
    _add_missing_columns(inspector, "processed_messages", _PROCESSED_MESSAGES_NEW_COLUMNS)
    _add_missing_columns(inspector, "gmail_accounts", _GMAIL_ACCOUNTS_NEW_COLUMNS)


def init_db() -> None:
    # import models so they're registered on Base.metadata before create_all
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _run_lightweight_migrations()
