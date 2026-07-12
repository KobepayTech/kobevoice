"""End-to-end: real espeak synthesis, metered gateway, per-user storage,
generation history/isolation, and the dev-mode billing simulator."""

import shutil
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.cloud.routers import voice as voice_router
from backend.engine_lite.main import app as engine_app

from .conftest import auth

ESPEAK = shutil.which("espeak-ng") or shutil.which("espeak")
needs_espeak = pytest.mark.skipif(not ESPEAK, reason="espeak-ng not installed")


def _register(client) -> str:
    email = f"v-{uuid.uuid4().hex[:8]}@example.com"
    return client.post(
        "/api/auth/register", json={"email": email, "password": "password123"}
    ).json()["access_token"]


@pytest.fixture(autouse=True)
def route_gateway_to_engine(monkeypatch):
    """Route the gateway's outbound httpx calls to the lite engine in-process."""
    real_async_client = httpx.AsyncClient  # capture before patching

    def _factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_async_client(
            transport=httpx.ASGITransport(app=engine_app), base_url="http://engine"
        )

    monkeypatch.setattr(voice_router.httpx, "AsyncClient", _factory)


# ---------- Lite engine ----------
@needs_espeak
def test_engine_synthesizes_wav():
    ec = TestClient(engine_app)
    assert ec.get("/health").json()["espeak"] is True
    r = ec.post("/synthesize", json={"text": "hello world", "voice": "female"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"  # real WAV bytes


# ---------- Gateway end-to-end ----------
@needs_espeak
def test_speak_generates_stores_and_meters(client):
    token = _register(client)

    r = client.post("/api/voice/speak", headers=auth(token), json={"text": "Hello there"})
    assert r.status_code == 200, r.text
    gen = r.json()
    assert gen["status"] == "complete"
    assert gen["characters"] == len("Hello there")
    assert gen["audio_url"].endswith("/audio")

    # Usage was metered.
    usage = client.get("/api/usage", headers=auth(token)).json()
    assert usage["generations_used"] == 1
    assert usage["characters_used"] == len("Hello there")

    # Audio is downloadable and is real WAV.
    audio = client.get(gen["audio_url"], headers=auth(token))
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"

    # Shows up in history.
    hist = client.get("/api/voice/generations", headers=auth(token)).json()
    assert len(hist) == 1 and hist[0]["id"] == gen["id"]


@needs_espeak
def test_tenant_isolation_on_audio(client):
    """A user cannot read another user's generation audio."""
    tok_a = _register(client)
    tok_b = _register(client)
    gen = client.post("/api/voice/speak", headers=auth(tok_a), json={"text": "secret"}).json()

    # Owner can read; the other user gets 404 (not 403 — don't leak existence).
    assert client.get(gen["audio_url"], headers=auth(tok_a)).status_code == 200
    assert client.get(gen["audio_url"], headers=auth(tok_b)).status_code == 404
    # B's history is empty.
    assert client.get("/api/voice/generations", headers=auth(tok_b)).json() == []


@needs_espeak
def test_delete_generation(client):
    token = _register(client)
    gen = client.post("/api/voice/speak", headers=auth(token), json={"text": "bye"}).json()
    assert client.delete(f"/api/voice/generations/{gen['id']}", headers=auth(token)).status_code == 204
    assert client.get(gen["audio_url"], headers=auth(token)).status_code == 404


# ---------- Billing dev-mode simulator ----------
def test_dev_billing_activates_plan(client, monkeypatch):
    from backend.cloud.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "billing_dev_mode", True)

    token = _register(client)
    # Before: on free.
    assert client.get("/api/billing/subscription", headers=auth(token)).json()["plan"]["slug"] == "free"

    r = client.post("/api/billing/checkout", headers=auth(token), json={"plan_slug": "cloud"})
    assert r.status_code == 200
    assert "checkout=success" in r.json()["url"]

    # After: upgraded and active, with higher quota.
    sub = client.get("/api/billing/subscription", headers=auth(token)).json()
    assert sub["plan"]["slug"] == "cloud"
    assert sub["status"] == "active"
    assert client.get("/api/usage", headers=auth(token)).json()["generations_limit"] == 1000


def test_checkout_without_billing_is_503(client):
    token = _register(client)
    r = client.post("/api/billing/checkout", headers=auth(token), json={"plan_slug": "cloud"})
    assert r.status_code == 503
