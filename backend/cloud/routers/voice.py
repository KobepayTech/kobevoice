"""Authenticated voice gateway.

Sits in front of the GPU/ML engine: authenticates the caller (JWT or API key),
enforces their plan quota, proxies the request to the engine, then records
usage. This is the metered, monetizable surface of the SaaS.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_user_jwt_or_apikey
from ..models import User
from ..quotas import enforce_quota, record_usage
from ..schemas import SpeakRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/speak")
async def speak(
    payload: SpeakRequest,
    user: User = Depends(get_user_jwt_or_apikey),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    characters = len(payload.text)

    # 1. Quota gate (raises 402 if over limit).
    enforce_quota(db, user, characters=characters)

    # 2. Proxy to the voice engine.
    engine_body = {
        "text": payload.text,
        "profile_id": payload.profile_id,
        "engine": payload.engine,
        "language": payload.language,
    }
    engine_body = {k: v for k, v in engine_body.items() if v is not None}

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{settings.engine_url}/speak", json=engine_body
            )
    except httpx.RequestError as exc:
        logger.error("Voice engine unreachable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Voice engine is unavailable",
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    # 3. Record usage only after a successful generation.
    record_usage(db, user, characters=characters)

    try:
        return resp.json()
    except ValueError:
        return {"status": "ok"}
