"""Auth flow: register, login, me, password change, duplicate guard."""

import uuid

from .conftest import auth


def test_register_and_me(client):
    email = f"a-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201
    token = r.json()["access_token"]

    me = client.get("/api/auth/me", headers=auth(token))
    assert me.status_code == 200
    assert me.json()["email"] == email
    assert me.json()["is_admin"] is False


def test_register_duplicate(client):
    email = f"dup-{uuid.uuid4().hex[:8]}@example.com"
    client.post("/api/auth/register", json={"email": email, "password": "password123"})
    r = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 409


def test_login_wrong_password(client):
    email = f"b-{uuid.uuid4().hex[:8]}@example.com"
    client.post("/api/auth/register", json={"email": email, "password": "password123"})
    r = client.post("/api/auth/login", json={"email": email, "password": "wrong"})
    assert r.status_code == 401


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_change_password(client):
    email = f"c-{uuid.uuid4().hex[:8]}@example.com"
    token = client.post(
        "/api/auth/register", json={"email": email, "password": "password123"}
    ).json()["access_token"]

    r = client.post(
        "/api/auth/change-password",
        headers=auth(token),
        json={"current_password": "password123", "new_password": "newpassword456"},
    )
    assert r.status_code == 204
    assert client.post("/api/auth/login", json={"email": email, "password": "password123"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": "newpassword456"}).status_code == 200


def test_new_user_gets_free_plan(client):
    email = f"d-{uuid.uuid4().hex[:8]}@example.com"
    token = client.post(
        "/api/auth/register", json={"email": email, "password": "password123"}
    ).json()["access_token"]
    sub = client.get("/api/billing/subscription", headers=auth(token))
    assert sub.status_code == 200
    assert sub.json()["plan"]["slug"] == "free"
