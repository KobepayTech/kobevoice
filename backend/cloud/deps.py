"""Shared FastAPI dependencies: current user, admin guard, API-key auth."""

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import ApiKey, User
from .security import decode_token, hash_api_key

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise _CREDENTIALS_EXC
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise _CREDENTIALS_EXC
    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise _CREDENTIALS_EXC
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    return user


def get_user_from_api_key(
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Authenticate a headless request via the ``X-API-Key`` header."""
    if not x_api_key:
        raise _CREDENTIALS_EXC
    record = (
        db.query(ApiKey)
        .filter(ApiKey.hashed_key == hash_api_key(x_api_key), ApiKey.revoked == False)  # noqa: E712
        .first()
    )
    if record is None:
        raise _CREDENTIALS_EXC
    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise _CREDENTIALS_EXC
    return user


def get_user_jwt_or_apikey(
    token: str | None = Depends(oauth2_scheme),
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Allow either a bearer JWT (web app) or an API key (agents/CLI)."""
    if x_api_key:
        return get_user_from_api_key(x_api_key=x_api_key, db=db)
    return get_current_user(token=token, db=db)
