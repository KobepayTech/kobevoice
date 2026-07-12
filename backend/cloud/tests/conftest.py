"""Test fixtures: isolated temp database + FastAPI TestClient.

Env vars are set *before* importing the app so the SQLAlchemy engine and
cached settings bind to a throwaway SQLite file.
"""

import os
import tempfile

# Must run before any `backend.cloud` import.
_tmp_db = os.path.join(tempfile.mkdtemp(prefix="kvcloud-test-"), "cloud.db")
os.environ["KOBEVOICE_CLOUD_DATABASE_URL"] = f"sqlite:///{_tmp_db}"
os.environ["KOBEVOICE_CLOUD_AUDIO_DIR"] = os.path.join(os.path.dirname(_tmp_db), "audio")
os.environ["KOBEVOICE_CLOUD_JWT_SECRET"] = "test-secret"
os.environ["KOBEVOICE_CLOUD_ADMIN_EMAIL"] = "admin@kobevoice-admin.example.com"
os.environ["KOBEVOICE_CLOUD_ADMIN_PASSWORD"] = "adminpass123"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.cloud.db import init_db  # noqa: E402
from backend.cloud.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    init_db()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def user_token(client):
    """Register a fresh user and return their bearer token."""
    import uuid

    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "full_name": "Test User"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def admin_token(client):
    resp = client.post(
        "/api/auth/login",
        json={"email": "admin@kobevoice-admin.example.com", "password": "adminpass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
