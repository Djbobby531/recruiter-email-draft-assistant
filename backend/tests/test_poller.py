"""Coverage for app/services/poller.py's incremental sync logic."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

import pytest
from googleapiclient.errors import HttpError

from app.models import GmailAccount, ProcessedMessage
from app.services import poller
from app.services.runtime_settings import EffectiveSettings
from tests.conftest import make_raw_message, make_resume
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


class _FakeHttpResp:
    def __init__(self, status):
        self.status = status
        self.reason = "Not Found"


def _stub_effective_settings(**overrides) -> EffectiveSettings:
    defaults = dict(
        poll_interval_seconds=30, sent_sync_every_n_cycles=1, poll_backoff_max_seconds=120,
        allowed_sender_domains="", email_hope_line="Hope you're doing well.",
        email_capability_sentence="capability sentence", email_closing_line="closing line",
    )
    defaults.update(overrides)
    return EffectiveSettings(**defaults)


def test_sender_domain_allowed_empty_allowlist_permits_everything():
    assert poller._sender_domain_allowed("anyone@anywhere.com", []) is True


def test_sender_domain_allowed_matches_exact_domain():
    assert poller._sender_domain_allowed("James <james@algebrait.com>", ["algebrait.com"]) is True


def test_sender_domain_allowed_matches_case_insensitively():
    assert poller._sender_domain_allowed("James <james@AlgebraIT.COM>", ["algebrait.com"]) is True


def test_sender_domain_allowed_matches_a_subdomain():
    assert poller._sender_domain_allowed("james@mail.algebrait.com", ["algebrait.com"]) is True


def test_sender_domain_allowed_rejects_a_different_domain():
    assert poller._sender_domain_allowed("recruiter@pamten.com", ["algebrait.com"]) is False


def test_sender_domain_allowed_rejects_when_no_email_address_found():
    assert poller._sender_domain_allowed("", ["algebrait.com"]) is False


class StubGmailClient:
    def __init__(self, messages_by_id, history_pages=None, seed_history_id="100", backfill_ids=None):
        self._messages_by_id = messages_by_id
        self._history_pages = history_pages or []
        self._seed_history_id = seed_history_id
        self._history_call_count = 0
        self._backfill_ids = backfill_ids or []
        self.list_message_ids_since_calls: list[int] = []

    def get_profile(self):
        return {"emailAddress": "diwakar@example.com", "historyId": self._seed_history_id}

    def list_recent_message_ids(self, max_results=10):
        return [], self._seed_history_id

    def list_history(self, start_history_id):
        if not self._history_pages:
            return [], start_history_id
        ids, next_id = self._history_pages[0]
        return ids, next_id

    def list_message_ids_since(self, after_epoch_seconds):
        self.list_message_ids_since_calls.append(after_epoch_seconds)
        return self._backfill_ids

    def get_message(self, message_id):
        return self._messages_by_id[message_id]

    def get_message_from_header(self, message_id):
        raw = self._messages_by_id.get(message_id, {})
        for header in raw.get("payload", {}).get("headers", []):
            if header.get("name", "").lower() == "from":
                return header.get("value", "")
        return ""

    def create_draft_with_attachment(self, **kwargs):
        return {"id": "draft-poller-1"}


def test_run_sync_cycle_no_account_returns_zero(db_session, settings):
    count = poller.run_sync_cycle(db_session, settings)
    assert count == 0


def test_run_sync_cycle_first_sync_seeds_history_without_processing(db_session, settings, monkeypatch):
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id=None)
    db_session.add(account)
    db_session.commit()

    stub = StubGmailClient(messages_by_id={}, seed_history_id="12345")
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)

    count = poller.run_sync_cycle(db_session, settings)
    assert count == 0

    db_session.refresh(account)
    assert account.last_history_id == "12345"
    assert db_session.query(ProcessedMessage).count() == 0


def test_run_sync_cycle_processes_new_messages_and_advances_history_id(
    db_session, settings, candidate_profile, monkeypatch
):
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={
            "data_engineering_tools": ["databricks", "airflow", "unity catalog"],
            "languages": ["python", "sql"], "devops_tools": ["terraform", "ci/cd"],
        },
        years=8, titles=["Senior Data Engineer"],
    )
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="100")
    db_session.add(account)
    db_session.commit()

    raw_message = make_raw_message(
        message_id="poller-msg-1", thread_id="poller-thread-1",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    stub = StubGmailClient(
        messages_by_id={"poller-msg-1": raw_message},
        history_pages=[(["poller-msg-1"], "101")],
    )
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)
    monkeypatch.setattr(poller, "get_ai_provider", lambda settings: None)

    count = poller.run_sync_cycle(db_session, settings)
    assert count == 1

    db_session.refresh(account)
    assert account.last_history_id == "101"
    assert db_session.query(ProcessedMessage).filter(ProcessedMessage.gmail_message_id == "poller-msg-1").count() == 1


def test_run_sync_cycle_advances_the_last_processed_at_watermark(
    db_session, settings, candidate_profile, monkeypatch
):
    """The explicit time watermark must advance to the newest successfully
    processed message's own Gmail internalDate, independent of historyId."""
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"data_engineering_tools": ["databricks"], "languages": ["python", "sql"]},
        years=8, titles=["Senior Data Engineer"],
    )
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="400")
    db_session.add(account)
    db_session.commit()

    raw_message = make_raw_message(
        message_id="watermark-msg-1", thread_id="watermark-thread-1",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    expected_dt = dt.datetime(2026, 9, 8, 10, 30, 0, tzinfo=dt.UTC)
    raw_message["internalDate"] = str(int(expected_dt.timestamp() * 1000))

    stub = StubGmailClient(
        messages_by_id={"watermark-msg-1": raw_message},
        history_pages=[(["watermark-msg-1"], "401")],
    )
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)
    monkeypatch.setattr(poller, "get_ai_provider", lambda settings: None)

    poller.run_sync_cycle(db_session, settings)

    db_session.refresh(account)
    # SQLite round-trips DateTime(timezone=True) as naive - compare the
    # underlying wall-clock UTC value, not tzinfo presence
    assert account.last_processed_at.replace(tzinfo=dt.UTC) == expected_dt


def test_run_sync_cycle_reseeds_on_expired_history_id(db_session, settings, monkeypatch):
    """A 404/410 from list_history means the stored historyId itself is no
    longer valid (Gmail only retains history for ~a week) - the only safe
    recovery is reseeding from the current mailbox state."""
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="expired-id")
    db_session.add(account)
    db_session.commit()

    class FailingHistoryClient(StubGmailClient):
        def list_history(self, start_history_id):
            raise HttpError(_FakeHttpResp(404), b'{"error": {"message": "historyId too old"}}')

    stub = FailingHistoryClient(messages_by_id={}, seed_history_id="999")
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)

    count = poller.run_sync_cycle(db_session, settings)
    assert count == 0
    db_session.refresh(account)
    assert account.last_history_id == "999"


def test_run_sync_cycle_backfills_from_the_watermark_when_history_expires(
    db_session, settings, candidate_profile, monkeypatch
):
    """When historyId expires but a last_processed_at watermark exists,
    everything received since that watermark must be listed and processed -
    not silently dropped by jumping straight to "now"."""
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"languages": ["python", "sql"], "data_engineering_tools": ["databricks"]},
        years=8, titles=["Senior Data Engineer"],
    )
    watermark = dt.datetime(2026, 9, 1, 12, 0, 0, tzinfo=dt.UTC)
    account = GmailAccount(
        email_address="diwakar@example.com", token_json="{}",
        last_history_id="expired-id", last_processed_at=watermark,
    )
    db_session.add(account)
    db_session.commit()

    backfilled_message = make_raw_message(
        message_id="backfilled-1", thread_id="backfilled-1-t",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )

    class ExpiredHistoryClient(StubGmailClient):
        def list_history(self, start_history_id):
            raise HttpError(_FakeHttpResp(404), b'{"error": {"message": "historyId too old"}}')

        def get_message_from_header(self, message_id):
            return "James AlgebraIT <james@algebrait.com>"

    stub = ExpiredHistoryClient(
        messages_by_id={"backfilled-1": backfilled_message},
        seed_history_id="999", backfill_ids=["backfilled-1"],
    )
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)
    monkeypatch.setattr(poller, "get_ai_provider", lambda settings: None)

    count = poller.run_sync_cycle(db_session, settings)

    assert count == 1
    assert stub.list_message_ids_since_calls == [int(watermark.timestamp())]
    assert db_session.query(ProcessedMessage).filter(ProcessedMessage.gmail_message_id == "backfilled-1").count() == 1
    db_session.refresh(account)
    assert account.last_history_id == "999"


def test_run_sync_cycle_does_not_reseed_on_a_transient_history_failure(db_session, settings, monkeypatch):
    """A non-expiry failure (network blip, unexpected error) must NOT reseed
    - reseeding discards our position and would silently skip any real
    messages that arrived in between. Just retry with the same historyId
    next cycle."""
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="still-valid-id")
    db_session.add(account)
    db_session.commit()

    class FlakyHistoryClient(StubGmailClient):
        def list_history(self, start_history_id):
            raise TimeoutError("simulated network blip")

    stub = FlakyHistoryClient(messages_by_id={}, seed_history_id="999")
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)

    count = poller.run_sync_cycle(db_session, settings)
    assert count == 0
    db_session.refresh(account)
    assert account.last_history_id == "still-valid-id"


def test_run_sync_cycle_propagates_rate_limit_without_reseeding_or_advancing(db_session, settings, monkeypatch):
    """A 403/429 while checking history must never be treated like an
    expired historyId (that would discard our position) - it must propagate
    so the caller (polling_loop) can back off, and the historyId must be
    left exactly as it was so the next cycle retries the same window."""
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="still-valid-id")
    db_session.add(account)
    db_session.commit()

    class RateLimitedHistoryClient(StubGmailClient):
        def list_history(self, start_history_id):
            raise HttpError(_FakeHttpResp(429), b'{"error": {"message": "rateLimitExceeded"}}')

    stub = RateLimitedHistoryClient(messages_by_id={}, seed_history_id="999")
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)

    with pytest.raises(HttpError):
        poller.run_sync_cycle(db_session, settings)
    db_session.refresh(account)
    assert account.last_history_id == "still-valid-id"


def test_run_sync_cycle_continues_after_one_message_fails_to_process(
    db_session, settings, candidate_profile, monkeypatch
):
    """One malformed/failing message must not prevent other messages in the
    same sync cycle from being processed."""
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"languages": ["python", "sql"], "data_engineering_tools": ["databricks"]},
        years=8, titles=["Senior Data Engineer"],
    )
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="200")
    db_session.add(account)
    db_session.commit()

    good_message = make_raw_message(
        message_id="poller-good", thread_id="poller-good-t",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )

    class PartiallyFailingClient(StubGmailClient):
        def get_message(self, message_id):
            if message_id == "poller-bad":
                raise Exception("simulated Gmail API error fetching this specific message")
            return super().get_message(message_id)

        def get_message_from_header(self, message_id):
            if message_id == "poller-bad":
                # allowed sender - so the failure below is what's under test,
                # not the sender-domain filter
                return "James AlgebraIT <james@algebrait.com>"
            return super().get_message_from_header(message_id)

    stub = PartiallyFailingClient(
        messages_by_id={"poller-good": good_message},
        history_pages=[(["poller-bad", "poller-good"], "201")],
    )
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)
    monkeypatch.setattr(poller, "get_ai_provider", lambda settings: None)

    count = poller.run_sync_cycle(db_session, settings)
    assert count == 1  # only the good message counted as processed
    assert db_session.query(ProcessedMessage).filter(ProcessedMessage.gmail_message_id == "poller-good").count() == 1

    # historyId is NOT advanced past a batch containing an unexpected
    # failure - "poller-bad" must get a fresh attempt next cycle rather than
    # being silently dropped forever (see "no emails ignored" hardening)
    db_session.refresh(account)
    assert account.last_history_id == "200"


def test_run_sync_cycle_retries_the_whole_batch_next_cycle_after_an_unexpected_failure(
    db_session, settings, candidate_profile, monkeypatch
):
    """After a cycle holds back historyId due to an unexpected failure, the
    NEXT cycle must see the exact same message_ids again (since historyId
    didn't move) - the previously-successful one is a safe no-op (dedup by
    gmail_message_id), and the previously-failed one gets a real retry."""
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"languages": ["python", "sql"], "data_engineering_tools": ["databricks"]},
        years=8, titles=["Senior Data Engineer"],
    )
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="300")
    db_session.add(account)
    db_session.commit()

    good_message = make_raw_message(
        message_id="retry-good", thread_id="retry-good-t",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    bad_message = make_raw_message(
        message_id="retry-bad", thread_id="retry-bad-t",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )

    class FlakyThenFixedClient(StubGmailClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.bad_message_attempts = 0

        def get_message(self, message_id):
            if message_id == "retry-bad":
                self.bad_message_attempts += 1
                if self.bad_message_attempts == 1:
                    raise Exception("simulated transient failure on first attempt only")
                return bad_message
            return super().get_message(message_id)

        def get_message_from_header(self, message_id):
            return "James AlgebraIT <james@algebrait.com>"

    stub = FlakyThenFixedClient(
        messages_by_id={"retry-good": good_message},
        history_pages=[(["retry-bad", "retry-good"], "301")],
    )
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)
    monkeypatch.setattr(poller, "get_ai_provider", lambda settings: None)

    # cycle 1: retry-bad fails, so historyId is held back at 300
    poller.run_sync_cycle(db_session, settings)
    db_session.refresh(account)
    assert account.last_history_id == "300"
    assert db_session.query(ProcessedMessage).filter(ProcessedMessage.gmail_message_id == "retry-good").count() == 1

    # cycle 2: same message_ids come back (list_history still keyed off "300"
    # since the stub always returns the same fixed page) - retry-bad now
    # succeeds, retry-good is a harmless duplicate no-op, historyId advances
    poller.run_sync_cycle(db_session, settings)
    db_session.refresh(account)
    assert account.last_history_id == "301"
    assert stub.bad_message_attempts == 2
    assert db_session.query(ProcessedMessage).filter(ProcessedMessage.gmail_message_id == "retry-good").count() == 1


# --- ALLOWED_SENDER_DOMAINS sender filtering ------------------------------


def test_run_sync_cycle_skips_a_message_from_a_disallowed_sender_without_a_full_fetch(
    db_session, settings, monkeypatch
):
    """Messages from senders outside ALLOWED_SENDER_DOMAINS must never even
    be fully fetched (format=full downloads body/attachments) or handed to
    the pipeline - only the lightweight metadata-only sender check runs."""
    settings.ALLOWED_SENDER_DOMAINS = "algebrait.com"
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="500")
    db_session.add(account)
    db_session.commit()

    other_message = make_raw_message(
        message_id="poller-other", thread_id="poller-other-t",
        from_header="Recruiter <recruiter@pamten.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )

    class AssertNoFullFetchClient(StubGmailClient):
        def get_message(self, message_id):
            raise AssertionError(f"full fetch must never happen for a disallowed sender: {message_id}")

    stub = AssertNoFullFetchClient(
        messages_by_id={"poller-other": other_message},
        history_pages=[(["poller-other"], "501")],
    )
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)

    count = poller.run_sync_cycle(db_session, settings)
    assert count == 0
    assert db_session.query(ProcessedMessage).count() == 0
    db_session.refresh(account)
    assert account.last_history_id == "501"  # cycle still advances normally


def test_run_sync_cycle_still_processes_a_message_from_an_allowed_sender(
    db_session, settings, candidate_profile, monkeypatch
):
    settings.ALLOWED_SENDER_DOMAINS = "algebrait.com"
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"languages": ["python", "sql"], "data_engineering_tools": ["databricks"]},
        years=8, titles=["Senior Data Engineer"],
    )
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="510")
    db_session.add(account)
    db_session.commit()

    good_message = make_raw_message(
        message_id="poller-allowed", thread_id="poller-allowed-t",
        from_header="James AlgebraIT <james@algebrait.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    stub = StubGmailClient(
        messages_by_id={"poller-allowed": good_message},
        history_pages=[(["poller-allowed"], "511")],
    )
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)
    monkeypatch.setattr(poller, "get_ai_provider", lambda settings: None)

    count = poller.run_sync_cycle(db_session, settings)
    assert count == 1
    assert db_session.query(ProcessedMessage).filter(ProcessedMessage.gmail_message_id == "poller-allowed").count() == 1


def test_run_sync_cycle_processes_every_sender_when_allowlist_is_empty(
    db_session, settings, candidate_profile, monkeypatch
):
    """Empty ALLOWED_SENDER_DOMAINS disables filtering entirely - the
    original, unfiltered behavior."""
    settings.ALLOWED_SENDER_DOMAINS = ""
    make_resume(
        db_session, "databricks_resume.pdf",
        skills={"languages": ["python", "sql"], "data_engineering_tools": ["databricks"]},
        years=8, titles=["Senior Data Engineer"],
    )
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="520")
    db_session.add(account)
    db_session.commit()

    message = make_raw_message(
        message_id="poller-anyone", thread_id="poller-anyone-t",
        from_header="Recruiter <recruiter@pamten.com>",
        subject=SAMPLE_SUBJECT, plain_body=SAMPLE_BODY,
    )
    stub = StubGmailClient(
        messages_by_id={"poller-anyone": message},
        history_pages=[(["poller-anyone"], "521")],
    )
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)
    monkeypatch.setattr(poller, "get_ai_provider", lambda settings: None)

    count = poller.run_sync_cycle(db_session, settings)
    assert count == 1


def test_run_sync_cycle_logs_404_as_a_quiet_warning_not_a_full_traceback(
    db_session, settings, monkeypatch, caplog
):
    """A history event pointing at a since-deleted message (404) is an
    expected, already-handled case (see the large-backlog scenario after
    reconnecting a stale account) - it must not spam a full ERROR-level
    traceback, just a one-line warning, and the cycle must still advance
    normally to the next message/history id."""
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="300")
    db_session.add(account)
    db_session.commit()

    class NotFoundClient(StubGmailClient):
        def get_message(self, message_id):
            raise HttpError(_FakeHttpResp(404), b'{"error": {"message": "Requested entity was not found."}}')

        def get_message_from_header(self, message_id):
            # A deleted message 404s on the metadata-only lookup too - same
            # as a real Gmail API call would.
            raise HttpError(_FakeHttpResp(404), b'{"error": {"message": "Requested entity was not found."}}')

    stub = NotFoundClient(messages_by_id={}, history_pages=[(["gone-message"], "301")])
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)
    monkeypatch.setattr(poller, "get_ai_provider", lambda settings: None)

    with caplog.at_level(logging.WARNING, logger="app.poller"):
        count = poller.run_sync_cycle(db_session, settings)

    assert count == 0
    assert any("no longer exists" in r.message for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)  # no full traceback logged
    db_session.refresh(account)
    assert account.last_history_id == "301"  # sync still advances normally


def test_run_sync_cycle_skips_sent_sync_when_check_sent_status_is_false(
    db_session, settings, monkeypatch
):
    """SENT_SYNC_EVERY_N_CYCLES: the sent-detection sweep (one Gmail call per
    still-DRAFT application) must be skippable so it doesn't run on every
    single poll cycle."""
    account = GmailAccount(email_address="diwakar@example.com", token_json="{}", last_history_id="400")
    db_session.add(account)
    db_session.commit()

    stub = StubGmailClient(messages_by_id={}, history_pages=[([], "401")])
    monkeypatch.setattr(poller, "get_gmail_client", lambda settings, token: stub)

    calls = []
    monkeypatch.setattr(poller, "run_sent_sync_cycle", lambda db, client: calls.append(1))

    poller.run_sync_cycle(db_session, settings, check_sent_status=False)
    assert calls == []

    poller.run_sync_cycle(db_session, settings, check_sent_status=True)
    assert calls == [1]


class _DummySession:
    def close(self):
        pass


@pytest.mark.asyncio
async def test_polling_loop_does_not_block_the_event_loop(monkeypatch, settings):
    """Regression test: run_sync_cycle is fully synchronous (blocking DB
    queries AND blocking Gmail API HTTP calls via google-api-python-client).
    polling_loop MUST offload it to a worker thread - calling it directly
    would freeze every other coroutine on the event loop, including FastAPI's
    own request handlers (e.g. the Gmail OAuth endpoints), for as long as the
    Gmail API call takes. This was a real bug: a connected account with a
    real historyId made every server startup hang indefinitely."""
    monkeypatch.setattr(poller, "get_settings", lambda: settings)
    monkeypatch.setattr(poller, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(
        poller.runtime_settings, "get_effective_settings",
        lambda db, settings: _stub_effective_settings(poll_interval_seconds=10),
    )

    def blocking_sync_cycle(db, settings, check_sent_status=True, allowed_domains=None):
        time.sleep(0.3)  # simulates a slow synchronous Gmail API call
        return 0

    monkeypatch.setattr(poller, "run_sync_cycle", blocking_sync_cycle)
    settings.POLL_INTERVAL_SECONDS = 10  # keep the loop from ticking again mid-test

    stop_event = asyncio.Event()
    other_task_progressed = False

    async def other_task():
        nonlocal other_task_progressed
        await asyncio.sleep(0.05)  # should complete WHILE the "Gmail call" is in flight
        other_task_progressed = True

    loop_task = asyncio.create_task(poller.polling_loop(stop_event))
    concurrent_task = asyncio.create_task(other_task())

    await asyncio.sleep(0.15)
    assert other_task_progressed, "the event loop was blocked by the synchronous sync cycle"

    stop_event.set()
    await concurrent_task
    await loop_task


@pytest.mark.asyncio
async def test_polling_loop_backs_off_after_a_rate_limit_instead_of_polling_again_immediately(
    monkeypatch, settings
):
    """If Gmail rate-limits a cycle, the loop must wait POLL_BACKOFF_MAX_SECONDS-bounded
    backoff before trying again, not the normal short POLL_INTERVAL_SECONDS -
    retrying immediately would just trip the limit again."""
    monkeypatch.setattr(poller, "get_settings", lambda: settings)
    monkeypatch.setattr(poller, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(
        poller.runtime_settings, "get_effective_settings",
        lambda db, settings: _stub_effective_settings(poll_interval_seconds=1, poll_backoff_max_seconds=5),
    )
    settings.POLL_INTERVAL_SECONDS = 1
    settings.POLL_BACKOFF_MAX_SECONDS = 5

    def rate_limited_sync_cycle(db, settings, check_sent_status=True, allowed_domains=None):
        raise HttpError(_FakeHttpResp(429), b'{"error": {"message": "rateLimitExceeded"}}')

    monkeypatch.setattr(poller, "run_sync_cycle", rate_limited_sync_cycle)

    stop_event = asyncio.Event()
    loop_task = asyncio.create_task(poller.polling_loop(stop_event))

    # give it time to run exactly one cycle and enter the backoff wait
    await asyncio.sleep(0.2)
    assert not loop_task.done()

    # normal POLL_INTERVAL_SECONDS (1s) would have already elapsed by ~0.2s
    # if backoff weren't applied - it must still be sleeping past that point
    await asyncio.sleep(1.2)
    assert not loop_task.done(), "loop ticked again at the normal interval instead of backing off"

    stop_event.set()
    await loop_task
