"""Authenticated, metered voice gateway + per-user generation history.

The engine is stateless (it just synthesizes audio); this control-plane owns
every user's history and stored audio, which is what makes the product
multi-tenant. Flow: authenticate → enforce quota → call engine `/synthesize`
→ persist audio + a CloudGeneration row for this user → record usage.
"""

import logging
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user, get_user_jwt_or_apikey
from ..models import CloudGeneration, User
from ..quotas import enforce_quota, record_usage
from ..schemas import GenerationResponse, SpeakRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])


def _audio_root() -> Path:
    root = Path(get_settings().audio_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post("/speak", response_model=GenerationResponse)
async def speak(
    payload: SpeakRequest,
    user: User = Depends(get_user_jwt_or_apikey),
    db: Session = Depends(get_db),
) -> GenerationResponse:
    settings = get_settings()
    characters = len(payload.text)

    # 1. Quota gate (raises 402 if over limit).
    enforce_quota(db, user, characters=characters)

    # 2. Synthesize on the stateless engine.
    body = {
        "text": payload.text,
        "voice": payload.profile_id,
        "language": payload.language,
    }
    body = {k: v for k, v in body.items() if v is not None}
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f"{settings.engine_url}/synthesize", json=body)
    except httpx.RequestError as exc:
        logger.error("Voice engine unreachable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Voice engine is unavailable"
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Engine error")

    audio = resp.content
    content_type = resp.headers.get("content-type", "audio/wav")

    # 3. Persist audio under a per-user directory (tenant isolation on disk).
    gen_id = str(uuid.uuid4())
    ext = "wav" if "wav" in content_type else "bin"
    rel = f"{user.id}/{gen_id}.{ext}"
    dest = _audio_root() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(audio)

    gen = CloudGeneration(
        id=gen_id,
        user_id=user.id,
        text=payload.text,
        voice=payload.profile_id,
        language=payload.language,
        engine=resp.headers.get("x-engine", "kobevoice"),
        characters=characters,
        audio_path=rel,
        content_type=content_type,
        status="complete",
    )
    db.add(gen)
    db.commit()

    # 4. Meter usage only after a successful, stored generation.
    record_usage(db, user, characters=characters)

    return GenerationResponse(
        id=gen.id,
        status=gen.status,
        text=gen.text,
        voice=gen.voice,
        characters=gen.characters,
        engine=gen.engine,
        audio_url=f"/api/voice/generations/{gen.id}/audio",
        created_at=gen.created_at,
    )


@router.get("/generations", response_model=list[GenerationResponse])
def list_generations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 50,
) -> list[GenerationResponse]:
    rows = (
        db.query(CloudGeneration)
        .filter(CloudGeneration.user_id == user.id)
        .order_by(CloudGeneration.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )
    return [
        GenerationResponse(
            id=g.id,
            status=g.status,
            text=g.text,
            voice=g.voice,
            characters=g.characters,
            engine=g.engine,
            audio_url=f"/api/voice/generations/{g.id}/audio",
            created_at=g.created_at,
        )
        for g in rows
    ]


@router.get("/generations/{gen_id}/audio")
def get_generation_audio(
    gen_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    gen = db.get(CloudGeneration, gen_id)
    # Ownership check — a user can only read their own audio.
    if gen is None or gen.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not gen.audio_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No audio")
    path = _audio_root() / gen.audio_path
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio missing")
    return FileResponse(path, media_type=gen.content_type, filename=f"{gen.id}.wav")


@router.delete("/generations/{gen_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_generation(
    gen_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    gen = db.get(CloudGeneration, gen_id)
    if gen is None or gen.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if gen.audio_path:
        path = _audio_root() / gen.audio_path
        path.unlink(missing_ok=True)
    db.delete(gen)
    db.commit()
