"""
Central application configuration, loaded from environment variables / .env.
Nothing else in the codebase should call os.environ directly - go through Settings.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    # --- General ---
    APP_ENV: Literal["development", "production", "test"] = "development"
    LOG_LEVEL: str = "INFO"
    DEBUG_LOG_EMAIL_CONTENT: bool = False  # never log full email bodies unless explicitly on

    # --- Paths (local-first storage) ---
    DATA_DIR: str = str(PROJECT_ROOT / "data")
    DB_PATH: str = str(PROJECT_ROOT / "data" / "db" / "app.db")
    RESUME_DIR: str = str(PROJECT_ROOT / "data" / "resumes")
    TOKEN_STORE_PATH: str = str(PROJECT_ROOT / "data" / "db" / "gmail_token.json")

    # --- Gmail / Google OAuth ---
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/gmail/oauth/callback"
    GMAIL_SCOPES: str = (
        "https://www.googleapis.com/auth/gmail.readonly,"
        "https://www.googleapis.com/auth/gmail.compose"
    )
    MY_EMAIL: str = ""  # the mailbox owner's own address, excluded from recruiter candidates

    # If set, only messages whose sender's domain is in this comma-separated
    # list are ever fetched/processed - everything else is skipped after a
    # lightweight sender-only check, without downloading the full message or
    # running it through the pipeline at all. Empty (default) = no filtering.
    ALLOWED_SENDER_DOMAINS: str = "algebrait.com"

    # --- Email detection mode ---
    EMAIL_MODE: Literal["polling", "pubsub"] = "polling"
    # A new recruiter email must turn into a Gmail draft quickly (target:
    # within 2 minutes) - kept well under that so a normal cycle plus
    # processing time never gets close to the limit.
    POLL_INTERVAL_SECONDS: int = 30
    # Sent-status detection (services/sent_detection.py) costs one Gmail API
    # call per still-DRAFT application - running it on every single poll
    # cycle scales badly once several applications are awaiting send
    # confirmation. It only needs to run periodically, not every cycle.
    # Decoupling this (rather than the message poll itself) is what actually
    # avoids Gmail rate limits, so the message-polling cadence above can stay
    # fast without retripping them.
    SENT_SYNC_EVERY_N_CYCLES: int = 5
    # After a Gmail rate-limit (403/429) response, back off before the next
    # poll cycle instead of retrying at the normal cadence and tripping the
    # limit again immediately. Doubles each consecutive rate-limited cycle,
    # capped here - kept within the 2-minute draft-creation target since
    # decoupling sent-sync above already removes the main rate-limit driver,
    # so hitting this cap in normal use should be rare. Resets to
    # POLL_INTERVAL_SECONDS on the next clean run.
    POLL_BACKOFF_MAX_SECONDS: int = 120

    # --- AI provider abstraction ---
    AI_PROVIDER: Literal["openai", "ollama", "none"] = "none"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.1"

    # --- Classification thresholds ---
    # How much job-related keyword signal an email needs to be treated as a
    # job/recruiter email at all (see job_classifier.py). Lowered from a
    # stricter default so most real recruiter emails get processed rather
    # than skipped as "not a job".
    JOB_CLASSIFICATION_THRESHOLD: float = 0.5
    # NOTE: by explicit configuration choice, this no longer gates whether the
    # pipeline auto-selects a recruiter and creates a draft (see
    # services/pipeline.py) - a low-confidence pick, or even a same-sender
    # fallback when no other candidate email exists, still produces a draft.
    # Kept only for display/tuning purposes (e.g. a future stricter mode).
    RECRUITER_EMAIL_CONFIDENCE_THRESHOLD: float = 0.55
    # NOTE: by explicit configuration choice, this no longer gates whether a
    # resume gets attached (see services/pipeline.py) - the single
    # highest-scoring resume is always attached and a draft created, as long
    # as at least one resume is indexed. match_score/match_explanation are
    # still recorded on the application so a poor match stays visible.
    RESUME_MATCH_CONFIDENCE_THRESHOLD: float = 30.0

    # --- Frontend ---
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    @property
    def gmail_scopes_list(self) -> list[str]:
        return [s.strip() for s in self.GMAIL_SCOPES.split(",") if s.strip()]

    @property
    def allowed_sender_domains_list(self) -> list[str]:
        return [d.strip().lower() for d in self.ALLOWED_SENDER_DOMAINS.split(",") if d.strip()]

    def ensure_dirs(self) -> None:
        Path(self.DATA_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(self.RESUME_DIR).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
