"""
User-editable overrides layered on top of Settings/.env defaults (see
app/models.py::AppSettingsOverride for what's covered and why).

Every override is re-validated every time it's read, never trusted just
because it was saved once - if a stored value is missing, empty (where not
explicitly allowed), or fails its check, the effective value silently falls
back to the built-in default. This mirrors the "never trust raw input,
always validate before use" pattern used for AI output elsewhere in the app.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AppSettingsOverride
from app.services.draft_service import (
    DEFAULT_CAPABILITY_SENTENCE,
    DEFAULT_CLOSING_LINE,
    DEFAULT_HOPE_LINE,
    violates_forbidden_phrases,
)

_MIN_POLL_INTERVAL_SECONDS = 10
_MAX_POLL_INTERVAL_SECONDS = 3600
_MIN_BACKOFF_SECONDS = 10
_MAX_BACKOFF_SECONDS = 3600
_MIN_SENT_SYNC_CYCLES = 1
_MAX_SENT_SYNC_CYCLES = 100
_MAX_DOMAIN_ENTRIES = 50
_MAX_EMAIL_SNIPPET_LENGTH = 400

_DOMAIN_RE_PART = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^{_DOMAIN_RE_PART}(\.{_DOMAIN_RE_PART})+$", re.IGNORECASE)


def get_or_create(db: Session) -> AppSettingsOverride:
    row = db.query(AppSettingsOverride).first()
    if row is None:
        row = AppSettingsOverride(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _valid_int(value: int | None, min_val: int, max_val: int) -> bool:
    return isinstance(value, int) and min_val <= value <= max_val


def _valid_domain_list(value: str | None) -> bool:
    if value is None:
        return False
    if value.strip() == "":
        return True  # explicit override to "no sender filtering at all"
    domains = [d.strip() for d in value.split(",") if d.strip()]
    if len(domains) > _MAX_DOMAIN_ENTRIES:
        return False
    return all(_DOMAIN_RE.match(d) for d in domains)


def _valid_email_snippet(value: str | None, *, allow_blank: bool) -> bool:
    if value is None:
        return False
    if value == "":
        return allow_blank
    if len(value) > _MAX_EMAIL_SNIPPET_LENGTH:
        return False
    if "\n" in value:
        return False
    # Must never be able to smuggle in something that breaks the structural
    # guarantees the deterministic template relies on elsewhere.
    lowered = value.lower()
    if "candidate details" in lowered or violates_forbidden_phrases(value):
        return False
    return True


@dataclass
class EffectiveSettings:
    poll_interval_seconds: int
    sent_sync_every_n_cycles: int
    poll_backoff_max_seconds: int
    allowed_sender_domains: str
    email_hope_line: str
    email_capability_sentence: str
    email_closing_line: str
    # names of fields whose stored override existed but failed validation
    # and fell back to the default this read - surfaced to the UI so a bad
    # save is visible rather than silently ignored.
    invalid_fields: list[str] = field(default_factory=list)


def get_effective_settings(db: Session, settings: Settings) -> EffectiveSettings:
    row = get_or_create(db)
    invalid: list[str] = []

    def resolve_int(override: int | None, default: int, min_val: int, max_val: int, field_name: str) -> int:
        if override is None:
            return default
        if _valid_int(override, min_val, max_val):
            return override
        invalid.append(field_name)
        return default

    def resolve_domains(override: str | None, default: str) -> str:
        if override is None:
            return default
        if _valid_domain_list(override):
            return override
        invalid.append("allowed_sender_domains")
        return default

    def resolve_snippet(override: str | None, default: str, field_name: str, *, allow_blank: bool) -> str:
        if override is None:
            return default
        if _valid_email_snippet(override, allow_blank=allow_blank):
            return override
        invalid.append(field_name)
        return default

    return EffectiveSettings(
        poll_interval_seconds=resolve_int(
            row.poll_interval_seconds, settings.POLL_INTERVAL_SECONDS,
            _MIN_POLL_INTERVAL_SECONDS, _MAX_POLL_INTERVAL_SECONDS, "poll_interval_seconds",
        ),
        sent_sync_every_n_cycles=resolve_int(
            row.sent_sync_every_n_cycles, settings.SENT_SYNC_EVERY_N_CYCLES,
            _MIN_SENT_SYNC_CYCLES, _MAX_SENT_SYNC_CYCLES, "sent_sync_every_n_cycles",
        ),
        poll_backoff_max_seconds=resolve_int(
            row.poll_backoff_max_seconds, settings.POLL_BACKOFF_MAX_SECONDS,
            _MIN_BACKOFF_SECONDS, _MAX_BACKOFF_SECONDS, "poll_backoff_max_seconds",
        ),
        allowed_sender_domains=resolve_domains(row.allowed_sender_domains, settings.ALLOWED_SENDER_DOMAINS),
        email_hope_line=resolve_snippet(
            row.email_hope_line, DEFAULT_HOPE_LINE, "email_hope_line", allow_blank=True,
        ),
        email_capability_sentence=resolve_snippet(
            row.email_capability_sentence, DEFAULT_CAPABILITY_SENTENCE,
            "email_capability_sentence", allow_blank=True,
        ),
        email_closing_line=resolve_snippet(
            row.email_closing_line, DEFAULT_CLOSING_LINE, "email_closing_line", allow_blank=False,
        ),
        invalid_fields=invalid,
    )


def allowed_sender_domains_list(effective: EffectiveSettings) -> list[str]:
    return [d.strip().lower() for d in effective.allowed_sender_domains.split(",") if d.strip()]
