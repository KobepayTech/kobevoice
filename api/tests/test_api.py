"""Tests for the Kobe Voice control plane.

Focus is on the things that are expensive to get wrong: tenant isolation and the
pre-dial compliance gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from kobeos import models as m
from kobeos.compliance import check_dialable, within_calling_window
from kobeos.db import get_session, init_db, make_engine
from kobeos.main import app


@pytest.fixture()
def client(tmp_path):
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path/'test.db'}")
    init_db(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        c._SessionFactory = TestSession  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def _tenant(client, slug="acme"):
    r = client.post("/tenants", json={"name": slug.title(), "slug": slug})
    assert r.status_code == 201, r.text
    return r.json()


def h(slug):
    return {"X-Tenant-Slug": slug}


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_tenant_header_required(client):
    assert client.get("/agents").status_code == 401


def test_unknown_tenant_rejected(client):
    assert client.get("/agents", headers=h("nope")).status_code == 404


def test_duplicate_slug_rejected(client):
    _tenant(client, "acme")
    assert client.post("/tenants", json={"name": "X", "slug": "acme"}).status_code == 409


def test_agents_are_isolated_between_tenants(client):
    """The single most important guarantee: no cross-tenant data leakage."""
    _tenant(client, "acme")
    _tenant(client, "globex")

    client.post("/agents", json={"name": "Acme Bot"}, headers=h("acme"))
    client.post("/agents", json={"name": "Globex Bot"}, headers=h("globex"))

    acme = client.get("/agents", headers=h("acme")).json()
    globex = client.get("/agents", headers=h("globex")).json()

    assert [a["name"] for a in acme] == ["Acme Bot"]
    assert [a["name"] for a in globex] == ["Globex Bot"]

    # Direct fetch of another tenant's agent by id must 404, not succeed.
    other_id = globex[0]["id"]
    assert client.get(f"/agents/{other_id}", headers=h("acme")).status_code == 404


def test_contacts_isolated(client):
    _tenant(client, "acme")
    _tenant(client, "globex")
    client.post("/contacts", json={"full_name": "A", "e164": "+15551110000"}, headers=h("acme"))
    assert client.get("/contacts", headers=h("globex")).json() == []


# --------------------------------------------------------------------------
# Agent builder
# --------------------------------------------------------------------------


def test_create_agent_with_chatterbox_voice(client):
    _tenant(client)
    r = client.post(
        "/agents",
        json={
            "name": "Cloned",
            "tts_provider": "chatterbox",
            "voice_sample_uri": "s3://voices/agent.wav",
            "transfer_to": "+15551234567",
        },
        headers=h("acme"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["tts_provider"] == "chatterbox"
    assert body["voice_sample_uri"] == "s3://voices/agent.wav"


# --------------------------------------------------------------------------
# Compliance
# --------------------------------------------------------------------------


def test_dnc_blocks_dialing(client):
    _tenant(client)
    num = "+15551230000"
    client.post("/compliance/consent", json={"e164": num}, headers=h("acme"))
    client.post("/compliance/dnc", json={"e164": num}, headers=h("acme"))

    r = client.post("/compliance/check-dialable", json={"e164": num}, headers=h("acme"))
    assert r.json()["allowed"] is False
    assert r.json()["reason"] == "dnc_listed"


def test_dnc_is_idempotent(client):
    _tenant(client)
    client.post("/compliance/dnc", json={"e164": "+1555"}, headers=h("acme"))
    second = client.post("/compliance/dnc", json={"e164": "+1555"}, headers=h("acme"))
    assert second.json()["status"] == "already_listed"


def test_no_consent_blocks_dialing(client):
    _tenant(client)
    r = client.post(
        "/compliance/check-dialable", json={"e164": "+15559990000"}, headers=h("acme")
    )
    assert r.json()["allowed"] is False
    assert r.json()["reason"] == "no_consent"


def test_dnc_is_per_tenant(client):
    """One tenant's DNC entry must not suppress another tenant's calls."""
    _tenant(client, "acme")
    _tenant(client, "globex")
    num = "+15557778888"
    client.post("/compliance/dnc", json={"e164": num}, headers=h("acme"))
    client.post("/compliance/consent", json={"e164": num}, headers=h("globex"))
    client.post(
        "/contacts",
        json={"e164": num, "timezone_name": "UTC"},
        headers=h("globex"),
    )
    r = client.post("/compliance/check-dialable", json={"e164": num}, headers=h("globex"))
    assert r.json()["reason"] != "dnc_listed"


def test_calling_window_uses_contact_timezone():
    """21:00 UTC is inside the window in London but outside it in New York."""
    at_21_utc = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)  # 21:00 London, 16:00 NY

    london = m.Contact(timezone_name="Europe/London")
    ny = m.Contact(timezone_name="America/New_York")

    assert within_calling_window(london, now=at_21_utc).allowed is False  # 21:00 == end
    assert within_calling_window(ny, now=at_21_utc).allowed is True  # 16:00

    early = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)  # 05:00 NY
    assert within_calling_window(ny, now=early).allowed is False


def test_unknown_timezone_fails_closed():
    bad = m.Contact(timezone_name="Mars/Olympus_Mons")
    d = within_calling_window(bad, now=datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc))
    assert d.allowed is False
    assert d.reason == "unknown_timezone"


def test_missing_contact_fails_closed():
    d = within_calling_window(None, now=datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc))
    assert d.allowed is False


def test_naive_datetime_treated_as_utc():
    """A naive `now` must not be reinterpreted as local time."""
    ny = m.Contact(timezone_name="America/New_York")
    naive = datetime(2026, 6, 1, 20, 0)  # 20:00 UTC -> 16:00 NY
    assert within_calling_window(ny, now=naive).allowed is True


def test_revoked_consent_is_not_resurrected(client, tmp_path):
    """Consent given, revoked, then given again must stay blocked."""
    _tenant(client)
    session = client._SessionFactory()  # type: ignore[attr-defined]
    tenant = session.query(m.Tenant).one()
    num = "+15550001111"
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            m.ConsentRecord(
                tenant_id=tenant.id, e164=num, basis="express",
                captured_at=now - timedelta(days=10),
            ),
            m.ConsentRecord(
                tenant_id=tenant.id, e164=num, basis="express",
                captured_at=now - timedelta(days=5),
                revoked_at=now - timedelta(days=5),
            ),
        ]
    )
    session.commit()

    decision = check_dialable(session, tenant_id=tenant.id, e164=num, contact=None)
    assert decision.allowed is False
    assert decision.reason == "consent_revoked"
    session.close()


def test_full_gate_allows_a_clean_number(client):
    _tenant(client)
    num = "+15552223333"
    client.post("/compliance/consent", json={"e164": num}, headers=h("acme"))
    client.post(
        "/contacts", json={"e164": num, "timezone_name": "UTC"}, headers=h("acme")
    )
    session = client._SessionFactory()  # type: ignore[attr-defined]
    tenant = session.query(m.Tenant).one()
    contact = session.query(m.Contact).filter_by(e164=num).one()
    midday = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    decision = check_dialable(
        session, tenant_id=tenant.id, e164=num, contact=contact, now=midday
    )
    assert decision.allowed is True, decision.detail
    session.close()


# --------------------------------------------------------------------------
# Calls & transcripts
# --------------------------------------------------------------------------


def test_transcript_roundtrip_and_ordering(client):
    _tenant(client)
    session = client._SessionFactory()  # type: ignore[attr-defined]
    tenant = session.query(m.Tenant).one()
    call = m.Call(
        tenant_id=tenant.id,
        direction=m.CallDirection.INBOUND,
        status=m.CallStatus.IN_PROGRESS,
        from_number="+1555",
        to_number="+1666",
    )
    session.add(call)
    session.commit()
    call_id = call.id
    session.close()

    # Deliberately out of order — the API must sort by offset, not insert order.
    client.post(
        f"/calls/{call_id}/transcript",
        json={"speaker": "caller", "text": "second", "offset_seconds": 5.0},
        headers=h("acme"),
    )
    client.post(
        f"/calls/{call_id}/transcript",
        json={"speaker": "agent", "text": "first", "offset_seconds": 1.0},
        headers=h("acme"),
    )

    turns = client.get(f"/calls/{call_id}/transcript", headers=h("acme")).json()
    assert [t["text"] for t in turns] == ["first", "second"]


def test_transcript_on_foreign_call_is_404(client):
    _tenant(client, "acme")
    _tenant(client, "globex")
    session = client._SessionFactory()  # type: ignore[attr-defined]
    acme = session.query(m.Tenant).filter_by(slug="acme").one()
    call = m.Call(
        tenant_id=acme.id,
        direction=m.CallDirection.INBOUND,
        status=m.CallStatus.IN_PROGRESS,
        from_number="+1",
        to_number="+2",
    )
    session.add(call)
    session.commit()
    call_id = call.id
    session.close()

    assert client.get(f"/calls/{call_id}/transcript", headers=h("globex")).status_code == 404


def test_stats_empty_and_populated(client):
    _tenant(client)
    empty = client.get("/stats", headers=h("acme")).json()
    assert empty["calls_total"] == 0
    assert empty["avg_duration_seconds"] is None

    session = client._SessionFactory()  # type: ignore[attr-defined]
    tenant = session.query(m.Tenant).one()
    session.add_all(
        [
            m.Call(
                tenant_id=tenant.id, direction=m.CallDirection.INBOUND,
                status=m.CallStatus.COMPLETED, from_number="+1", to_number="+2",
                duration_seconds=60,
            ),
            m.Call(
                tenant_id=tenant.id, direction=m.CallDirection.OUTBOUND,
                status=m.CallStatus.IN_PROGRESS, from_number="+1", to_number="+3",
            ),
        ]
    )
    session.commit()
    session.close()

    s = client.get("/stats", headers=h("acme")).json()
    assert s["calls_total"] == 2
    assert s["calls_live"] == 1
    assert s["avg_duration_seconds"] == 60.0


# --------------------------------------------------------------------------
# KobeOS domain
# --------------------------------------------------------------------------


def test_kobeos_crud(client):
    _tenant(client)
    c = client.post(
        "/contacts", json={"full_name": "Jane", "e164": "+1555"}, headers=h("acme")
    ).json()

    t = client.post(
        "/tickets", json={"subject": "Billing", "contact_id": c["id"]}, headers=h("acme")
    )
    assert t.status_code == 201 and t.json()["status"] == "open"

    o = client.post(
        "/orders", json={"contact_id": c["id"], "amount_cents": 2500}, headers=h("acme")
    )
    assert o.status_code == 201 and o.json()["amount_cents"] == 2500

    r = client.post(
        "/reservations",
        json={"contact_id": c["id"], "scheduled_for": "2026-09-01T18:00:00Z", "party_size": 4},
        headers=h("acme"),
    )
    assert r.status_code == 201 and r.json()["party_size"] == 4

    assert len(client.get("/tickets", headers=h("acme")).json()) == 1
    assert len(client.get("/orders", headers=h("acme")).json()) == 1
    assert len(client.get("/reservations", headers=h("acme")).json()) == 1


def test_negative_order_amount_rejected(client):
    _tenant(client)
    r = client.post("/orders", json={"amount_cents": -1}, headers=h("acme"))
    assert r.status_code == 422
