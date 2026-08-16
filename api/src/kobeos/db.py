"""Engine and session management."""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+pysqlite:///./kobevoice.db")


def make_engine(url: str | None = None):
    url = url or DATABASE_URL
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # SQLite defaults to one-thread-per-connection, which breaks under the
        # threadpool FastAPI uses for sync endpoints.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(bind=None) -> None:
    """Create tables. Alembic owns schema in production; this is for dev/tests."""
    Base.metadata.create_all(bind or engine)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
