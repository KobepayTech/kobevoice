"""Kobevoice Cloud — the SaaS control-plane.

A lightweight FastAPI service (auth, billing, quotas, admin, voice gateway)
that runs independently of the GPU/ML engine. Launch with:

    uvicorn backend.cloud.main:app --reload --port 9000
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import init_db
from .routers import admin, auth, billing, usage, voice

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Kobevoice Cloud API",
        description="Accounts, billing, quotas and the metered voice gateway.",
        version="0.1.0",
    )

    origins = [settings.frontend_url, "http://localhost:5173", "http://127.0.0.1:5173"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(dict.fromkeys(origins)),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup() -> None:
        init_db()
        logger.info("Kobevoice Cloud ready (env=%s, billing=%s)", settings.environment, settings.billing_enabled)

    @app.get("/health", tags=["health"])
    def health() -> dict:
        return {"status": "ok", "service": "kobevoice-cloud", "billing": settings.billing_enabled}

    app.include_router(auth.router)
    app.include_router(billing.router)
    app.include_router(usage.router)
    app.include_router(voice.router)
    app.include_router(admin.router)
    return app


app = create_app()
