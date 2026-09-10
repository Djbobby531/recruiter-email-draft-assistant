from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.logging_conf import configure_logging
from app.routers import (
    applications,
    dashboard,
    gmail,
    messages,
    opportunities,
    profile,
    recruiters,
    resumes,
    settings_router,
)
from app.services.poller import polling_loop

logger = logging.getLogger("app.main")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    init_db()

    # Created fresh per app lifecycle (not module-level) - an asyncio.Event/Task
    # is bound to whichever event loop is running when first used, so a
    # module-level instance breaks the moment a second lifecycle starts on a
    # different loop (e.g. every FastAPI TestClient in a test suite).
    stop_event = asyncio.Event()
    poller_task = asyncio.create_task(polling_loop(stop_event))
    logger.info("Recruiter Email Draft Assistant backend started (AI_PROVIDER=%s, EMAIL_MODE=%s)",
                settings.AI_PROVIDER, settings.EMAIL_MODE)
    yield

    stop_event.set()
    await poller_task


app = FastAPI(title="Recruiter Email Draft Assistant", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(gmail.router)
app.include_router(profile.router)
app.include_router(resumes.router)
app.include_router(messages.router)
app.include_router(applications.router)
app.include_router(dashboard.router)
app.include_router(settings_router.router)
app.include_router(recruiters.router)
app.include_router(opportunities.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
