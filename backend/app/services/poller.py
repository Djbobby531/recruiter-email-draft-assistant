"""
Background polling worker. EMAIL_MODE=polling (default, zero-infra) periodically
asks Gmail "what changed since last_history_id" via the History API, so we never
re-download the whole mailbox - only new messages since the last successful sync.

A simple asyncio background task is used instead of Celery/Redis: at ~100
emails/day for a single user, a lightweight in-process loop is more than
sufficient and keeps the whole stack to "python + sqlite".
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.ai.factory import get_ai_provider
from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import GmailAccount
from app.services import runtime_settings
from app.services.gmail_service import GmailClient, get_gmail_client
from app.services.pipeline import process_message
from app.services.sent_detection import run_sent_sync_cycle
from app.utils.email_utils import extract_all_emails

logger = logging.getLogger("app.poller")

_RATE_LIMIT_STATUSES = (403, 429)
_HISTORY_EXPIRED_STATUSES = (404, 410)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _as_utc(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite (this app's only DB) round-trips a DateTime(timezone=True)
    column as a naive datetime even though the stored value is always UTC
    (see _utcnow()) - this re-attaches UTC tzinfo so later .timestamp()
    calls and comparisons against a freshly-constructed aware datetime are
    always correct, regardless of what SQLite handed back."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


def _parse_internal_date(raw_message: dict) -> dt.datetime | None:
    """Gmail's own received-time for a message (epoch milliseconds, as a
    string) - used to advance the last_processed_at watermark."""
    raw = raw_message.get("internalDate")
    if not raw:
        return None
    try:
        return dt.datetime.fromtimestamp(int(raw) / 1000, tz=dt.UTC)
    except (ValueError, TypeError, OSError):
        return None


def _sender_domain_allowed(from_header: str, allowed_domains: list[str]) -> bool:
    """Empty allowlist = no filtering (process every sender, current
    behavior). Otherwise the sender's email domain must exactly match one of
    the configured domains, or be a subdomain of one."""
    if not allowed_domains:
        return True
    emails = extract_all_emails(from_header)
    if not emails:
        return False
    domain = emails[0].rsplit("@", 1)[-1].lower()
    return any(domain == d or domain.endswith(f".{d}") for d in allowed_domains)


def _is_rate_limit_error(exc: Exception) -> bool:
    if not isinstance(exc, HttpError):
        return False
    status = exc.resp.status if getattr(exc, "resp", None) else None
    return status in _RATE_LIMIT_STATUSES


def _is_history_expired_error(exc: Exception) -> bool:
    if not isinstance(exc, HttpError):
        return False
    status = exc.resp.status if getattr(exc, "resp", None) else None
    return status in _HISTORY_EXPIRED_STATUSES


class _BatchResult:
    def __init__(self) -> None:
        self.processed_count = 0
        self.hit_rate_limit = False
        self.had_unexpected_failure = False
        self.newest_internal_date: dt.datetime | None = None


def _process_message_batch(
    db: Session, settings: Settings, client: GmailClient, ai_provider, message_ids: list[str],
    allowed_domains: list[str],
) -> _BatchResult:
    """Processes one batch of message ids, shared by both the normal
    incremental-sync path and the expired-historyId backfill path."""
    result = _BatchResult()
    for message_id in message_ids:
        try:
            if allowed_domains:
                # Lightweight metadata-only check first - if the sender isn't
                # in the allowlist, skip entirely without ever doing the full
                # fetch or running it through the pipeline.
                from_header = client.get_message_from_header(message_id)
                if not _sender_domain_allowed(from_header, allowed_domains):
                    logger.info("message %s skipped (sender not in allowed domains)", message_id)
                    continue
            raw_message = client.get_message(message_id)
            process_message(db, settings, raw_message, gmail_client=client, ai_provider=ai_provider)
            result.processed_count += 1
            internal_date = _parse_internal_date(raw_message)
            if internal_date and (result.newest_internal_date is None or internal_date > result.newest_internal_date):
                result.newest_internal_date = internal_date
        except HttpError as exc:
            if _is_rate_limit_error(exc):
                # Stop working through the rest of this batch - they're
                # doomed to fail too. Whatever's left is still safe to pick
                # up next cycle since the caller won't advance its
                # watermark/historyId past an incomplete batch.
                logger.warning(
                    "gmail rate limit hit after processing %d message(s) this cycle; "
                    "stopping early, remainder will be retried next cycle",
                    result.processed_count,
                )
                result.hit_rate_limit = True
                break
            # Expected/benign: the History API can report a "messageAdded"
            # event for a message that's since been deleted/expunged (e.g. a
            # promo email auto-deleted, or the user trashed something) - a
            # 404 here is normal and already fully handled by skipping it, so
            # a full traceback is just noise. Anything else Gmail-side (auth,
            # 5xx) still gets the full traceback below AND holds back the
            # watermark so this message is never silently dropped.
            status = exc.resp.status if getattr(exc, "resp", None) else None
            if status == 404:
                logger.warning("message %s no longer exists on Gmail (skipped)", message_id)
            else:
                logger.exception("failed to process message %s - will retry next cycle", message_id)
                result.had_unexpected_failure = True
        except Exception:
            logger.exception("failed to process message %s - will retry next cycle", message_id)
            result.had_unexpected_failure = True

    return result


def _reseed_with_backfill(
    db: Session, settings: Settings, account: GmailAccount, client: GmailClient, ai_provider,
    allowed_domains: list[str], check_sent_status: bool,
) -> int:
    """The stored historyId has expired (Gmail only retains ~a week of
    history) - rather than silently jumping to "now" and dropping whatever
    arrived during the gap, ask Gmail directly for everything received after
    the last confirmed-processed watermark and process it, THEN reseed."""
    watermark = _as_utc(account.last_processed_at)
    backfill_ids: list[str] = []
    if watermark is not None:
        try:
            backfill_ids = client.list_message_ids_since(int(watermark.timestamp()))
        except Exception:
            logger.exception("backfill listing failed after historyId expired; reseeding with no backfill")

    batch = _BatchResult()
    if backfill_ids:
        logger.warning(
            "historyId expired - backfilling %d message(s) received since the last processed "
            "watermark (%s) so nothing from the gap is missed",
            len(backfill_ids), watermark.isoformat() if watermark else "unknown",
        )
        batch = _process_message_batch(db, settings, client, ai_provider, backfill_ids, allowed_domains)
        if batch.hit_rate_limit:
            # Leave the expired historyId/watermark exactly as they are -
            # next cycle retries the backfill listing + processing from
            # scratch rather than accepting partial progress.
            raise HttpError(_RateLimitedResp(), b'{"error": {"message": "rateLimitExceeded"}}')
    else:
        logger.warning("history sync failed (expired historyId); reseeding (no backfill needed)")

    _, fresh_history_id = client.list_recent_message_ids(max_results=1)
    account.last_history_id = fresh_history_id
    if not batch.had_unexpected_failure:
        account.last_processed_at = batch.newest_internal_date or _utcnow()
    else:
        logger.warning(
            "backfill had at least one unexpected failure - watermark not advanced past it; "
            "check that message manually if it doesn't show up processed later"
        )
    db.add(account)
    db.commit()

    if check_sent_status:
        try:
            run_sent_sync_cycle(db, client)
        except Exception:
            logger.exception("sent-message sync cycle failed")

    return batch.processed_count


def run_sync_cycle(
    db: Session, settings: Settings, check_sent_status: bool = True, allowed_domains: list[str] | None = None,
) -> int:
    """Runs one incremental sync cycle. Returns number of messages processed.

    `check_sent_status` lets the caller run the (comparatively expensive,
    one-Gmail-call-per-application) sent-detection sweep on a slower cadence
    than the message poll itself - see SENT_SYNC_EVERY_N_CYCLES.

    `allowed_domains` lets the caller pass the live, user-editable override
    (see services/runtime_settings.py) - if omitted, falls back to the
    static settings.ALLOWED_SENDER_DOMAINS from .env."""
    account = db.query(GmailAccount).first()
    if account is None:
        return 0

    client: GmailClient = get_gmail_client(settings, account.token_json)
    ai_provider = get_ai_provider(settings)
    if allowed_domains is None:
        allowed_domains = settings.allowed_sender_domains_list

    if not account.last_history_id:
        # first-ever sync: seed from current mailbox state, don't backfill the
        # whole inbox history - the watermark starts here too, so a future
        # historyId expiry backfills only from this point forward.
        message_ids, history_id = client.list_recent_message_ids(max_results=10)
        account.last_history_id = history_id
        account.last_processed_at = _utcnow()
        db.add(account)
        db.commit()
        logger.info("seeded gmail sync at historyId=%s (no backfill processing)", history_id)
        return 0

    try:
        message_ids, latest_history_id = client.list_history(account.last_history_id)
    except Exception as exc:
        if _is_rate_limit_error(exc):
            # Don't touch last_history_id - our position is still valid, we
            # just need to back off and retry it next cycle. Let this
            # propagate so polling_loop can slow its cadence down.
            logger.warning("gmail rate limit hit while checking history; will retry next cycle")
            raise
        if _is_history_expired_error(exc):
            return _reseed_with_backfill(
                db, settings, account, client, ai_provider, allowed_domains, check_sent_status,
            )
        # Some other transient failure (network blip, 5xx) - keep the
        # existing historyId and just retry at the normal cadence next cycle.
        logger.warning("history sync failed (%s); will retry next cycle", exc)
        return 0

    batch = _process_message_batch(db, settings, client, ai_provider, message_ids, allowed_domains)

    if batch.hit_rate_limit:
        # Don't advance last_history_id past the messages we never got to
        # process, and don't run the (also Gmail-call-heavy) sent-sync sweep
        # on top of an API that's already rate-limiting us this cycle.
        raise HttpError(_RateLimitedResp(), b'{"error": {"message": "rateLimitExceeded"}}')

    if batch.had_unexpected_failure:
        # Hold the whole batch back rather than silently losing the message
        # that failed - every message here is safe to see again next cycle,
        # since anything already successfully processed is a guaranteed
        # no-op the second time (see pipeline.py's dedup-by-gmail_message_id).
        logger.warning(
            "not advancing historyId - at least one message this cycle hit an unexpected error; "
            "the whole batch (%d message id(s)) will be retried next cycle so nothing is silently dropped",
            len(message_ids),
        )
    else:
        account.last_history_id = latest_history_id
        if batch.newest_internal_date is not None:
            existing_watermark = _as_utc(account.last_processed_at)
            account.last_processed_at = max(batch.newest_internal_date, existing_watermark or batch.newest_internal_date)
        db.add(account)
        db.commit()

    # Section 3/17: check every application still awaiting send confirmation
    # for a matching Gmail SENT message. Only on every Nth cycle
    # (SENT_SYNC_EVERY_N_CYCLES) - each still-DRAFT application costs one
    # Gmail API call, so doing this every single poll cycle scales badly.
    if check_sent_status:
        try:
            run_sent_sync_cycle(db, client)
        except Exception:
            logger.exception("sent-message sync cycle failed")

    return batch.processed_count


class _RateLimitedResp:
    status = 429
    reason = "rateLimitExceeded"


async def polling_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    if settings.EMAIL_MODE != "polling":
        logger.info("EMAIL_MODE=%s, polling loop disabled", settings.EMAIL_MODE)
        return

    logger.info("starting Gmail polling loop (defaults: interval=%ss)", settings.POLL_INTERVAL_SECONDS)
    cycle_number = 0
    backoff_seconds = settings.POLL_INTERVAL_SECONDS
    while not stop_event.is_set():
        cycle_number += 1
        rate_limited = False

        # A fast local sqlite read (single row, no network) - safe to do
        # directly on the event loop, unlike the Gmail API work below.
        settings_db = SessionLocal()
        try:
            effective = runtime_settings.get_effective_settings(settings_db, settings)
        finally:
            settings_db.close()
        if effective.invalid_fields:
            logger.warning(
                "ignoring invalid setting override(s), using defaults for: %s",
                ", ".join(effective.invalid_fields),
            )
        check_sent_status = cycle_number % effective.sent_sync_every_n_cycles == 0
        allowed_domains = runtime_settings.allowed_sender_domains_list(effective)

        db = SessionLocal()
        try:
            # run_sync_cycle is fully synchronous (blocking DB queries AND
            # blocking Gmail API HTTP calls via google-api-python-client) - it
            # MUST run in a worker thread, never awaited directly on the
            # event loop, or it freezes every other request the server is
            # trying to handle (including the Gmail OAuth endpoints
            # themselves) for as long as the Gmail API call takes.
            count = await asyncio.to_thread(
                run_sync_cycle, db, settings, check_sent_status, allowed_domains,
            )
            if count:
                logger.info("processed %d new message(s)", count)
        except HttpError as exc:
            if _is_rate_limit_error(exc):
                rate_limited = True
            else:
                logger.exception("polling cycle failed")
        except Exception:
            logger.exception("polling cycle failed")
        finally:
            db.close()

        if rate_limited:
            # Back off exponentially instead of hammering an already
            # rate-limited API again in another poll_interval_seconds - each
            # consecutive rate-limited cycle doubles the wait, capped at
            # poll_backoff_max_seconds.
            backoff_seconds = min(backoff_seconds * 2, effective.poll_backoff_max_seconds)
            logger.warning("backing off for %ds after Gmail rate limit", backoff_seconds)
        else:
            backoff_seconds = effective.poll_interval_seconds

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff_seconds)
        except TimeoutError:
            pass
