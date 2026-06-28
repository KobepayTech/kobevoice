"""Quota enforcement, usage metering, API keys, and admin backoffice."""

import uuid

from backend.cloud.db import SessionLocal
from backend.cloud.models import Plan, User
from backend.cloud.quotas import current_period
from backend.cloud import models

from .conftest import auth


def _register(client) -> tuple[str, str]:
    email = f"q-{uuid.uuid4().hex[:8]}@example.com"
    token = client.post(
        "/api/auth/register", json={"email": email, "password": "password123"}
    ).json()["access_token"]
    return email, token


def test_plans_listed(client):
    r = client.get("/api/billing/plans")
    assert r.status_code == 200
    slugs = {p["slug"] for p in r.json()}
    assert {"free", "cloud", "pro"} <= slugs


def test_usage_starts_empty(client):
    _, token = _register(client)
    r = client.get("/api/usage", headers=auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["generations_used"] == 0
    assert body["generations_limit"] == 30  # free plan


def test_quota_blocks_when_generation_limit_exhausted(client):
    """Pre-fill usage to the free limit, then a speak call must be rejected 402."""
    email, token = _register(client)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        free = db.query(Plan).filter(Plan.slug == "free").one()
        period = current_period()
        for _ in range(free.monthly_generation_limit):
            db.add(models.UsageRecord(user_id=user.id, kind="generation", amount=1, period=period))
        db.commit()
    finally:
        db.close()

    # Engine is never reached because the quota gate fires first.
    r = client.post("/api/voice/speak", headers=auth(token), json={"text": "hello"})
    assert r.status_code == 402


def test_api_key_create_list_revoke(client):
    _, token = _register(client)
    created = client.post("/api/api-keys", headers=auth(token), json={"name": "cli"})
    assert created.status_code == 201
    body = created.json()
    assert body["key"].startswith("kv_live_")
    assert body["prefix"].startswith("kv_live_")

    listed = client.get("/api/api-keys", headers=auth(token))
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    # The full secret is never returned again.
    assert "key" not in listed.json()[0]

    key_id = body["id"]
    assert client.delete(f"/api/api-keys/{key_id}", headers=auth(token)).status_code == 204
    assert client.get("/api/api-keys", headers=auth(token)).json()[0]["revoked"] is True


def test_admin_guard_blocks_regular_user(client):
    _, token = _register(client)
    assert client.get("/api/admin/users", headers=auth(token)).status_code == 403


def test_admin_can_list_users_and_stats(client, admin_token):
    _register(client)  # ensure at least one non-admin user exists
    users = client.get("/api/admin/users", headers=auth(admin_token))
    assert users.status_code == 200
    assert len(users.json()) >= 1

    stats = client.get("/api/admin/stats", headers=auth(admin_token))
    assert stats.status_code == 200
    assert stats.json()["total_users"] >= 1


def test_admin_disable_and_enable_user(client, admin_token):
    email, token = _register(client)
    db = SessionLocal()
    try:
        uid = db.query(User).filter(User.email == email).one().id
    finally:
        db.close()

    assert client.post(f"/api/admin/users/{uid}/disable", headers=auth(admin_token)).status_code == 200
    # Disabled user can no longer authenticate.
    assert client.get("/api/auth/me", headers=auth(token)).status_code == 401
    assert client.post(f"/api/admin/users/{uid}/enable", headers=auth(admin_token)).status_code == 200


def test_voice_requires_auth(client):
    assert client.post("/api/voice/speak", json={"text": "hi"}).status_code == 401
