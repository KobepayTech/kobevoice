"""Database engine and session management for Kobevoice Cloud.

Kept separate from the local single-user engine in ``backend/database`` so the
control-plane can run as its own service (SQLite for dev, Postgres for prod)
without importing the ML backend.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import get_settings

Base = declarative_base()

_settings = get_settings()

# SQLite needs check_same_thread=False under the FastAPI threadpool; Postgres
# and other backends ignore the arg.
_connect_args = (
    {"check_same_thread": False}
    if _settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(_settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db() -> None:
    """Create tables and seed reference data (plans + admin user)."""
    # Import models so they register on Base.metadata before create_all.
    from . import models  # noqa: F401

    # Ensure the sqlite data dir exists.
    if _settings.database_url.startswith("sqlite:///"):
        from pathlib import Path

        path = _settings.database_url.removeprefix("sqlite:///")
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)

    from .seed import seed_admin, seed_plans

    db = SessionLocal()
    try:
        seed_plans(db)
        seed_admin(db)
        db.commit()
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
