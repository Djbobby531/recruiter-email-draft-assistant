"""Structured logging setup. Never logs OAuth tokens, API keys, or full email
bodies unless DEBUG_LOG_EMAIL_CONTENT=true is explicitly set."""
from __future__ import annotations

import logging
import sys

from app.config import Settings


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)
    root.handlers = [handler]

    # Silence noisy third-party libraries at INFO level
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("google_auth_oauthlib").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
