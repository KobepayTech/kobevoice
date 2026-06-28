"""Usage + API-key management for the signed-in user."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import ApiKey, User
from ..quotas import current_period, get_effective_plan, usage_for_period
from ..schemas import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
    UsageResponse,
)
from ..security import generate_api_key

router = APIRouter(prefix="/api", tags=["usage"])


@router.get("/usage", response_model=UsageResponse)
def get_usage(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> UsageResponse:
    plan = get_effective_plan(db, user)
    period = current_period()
    return UsageResponse(
        period=period,
        generations_used=usage_for_period(db, user, "generation", period),
        generations_limit=plan.monthly_generation_limit,
        characters_used=usage_for_period(db, user, "characters", period),
        characters_limit=plan.monthly_character_limit,
    )


@router.get("/api-keys", response_model=list[ApiKeyResponse])
def list_api_keys(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ApiKey]:
    return db.query(ApiKey).filter(ApiKey.user_id == user.id).all()


@router.post("/api-keys", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: ApiKeyCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyCreatedResponse:
    full, prefix, hashed = generate_api_key()
    record = ApiKey(user_id=user.id, name=payload.name, prefix=prefix, hashed_key=hashed)
    db.add(record)
    db.commit()
    db.refresh(record)
    return ApiKeyCreatedResponse(
        id=record.id,
        name=record.name,
        prefix=record.prefix,
        last_used_at=record.last_used_at,
        revoked=record.revoked,
        created_at=record.created_at,
        key=full,
    )


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    record = db.get(ApiKey, key_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    record.revoked = True
    db.commit()
