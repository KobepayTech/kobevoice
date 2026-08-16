"""Kobe Voice control-plane API.

Serves the supervisor dashboard and the KobeOS business endpoints, and is the
authority the voice agent consults for agent configuration and pre-dial
compliance.

Tenancy is enforced in one place — the ``tenant`` dependency — and every query
filters on the resolved tenant. Auth is a shared header today (see
``resolve_tenant``); that is the first thing to replace before this is exposed
publicly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models as m
from .compliance import check_dialable
from .db import get_session, init_db

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Dev convenience only — Alembic owns schema in any real deployment.
    init_db()
    yield


app = FastAPI(
    title="Kobe Voice API",
    version="0.1.0",
    description="Control plane for the Kobe Voice call-center platform.",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------


def resolve_tenant(
    session: Annotated[Session, Depends(get_session)],
    x_tenant_slug: Annotated[str | None, Header()] = None,
) -> m.Tenant:
    """Resolve the caller's tenant.

    NOTE: header-based and unauthenticated — every request is trusted to declare
    its own tenant. Fine for local development, unacceptable in production; swap
    for a verified token before exposing this service.
    """
    if not x_tenant_slug:
        raise HTTPException(status_code=401, detail="X-Tenant-Slug header required")
    tenant = session.execute(
        select(m.Tenant).where(m.Tenant.slug == x_tenant_slug)
    ).scalar_one_or_none()
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=404, detail="unknown or inactive tenant")
    return tenant


TenantDep = Annotated[m.Tenant, Depends(resolve_tenant)]
SessionDep = Annotated[Session, Depends(get_session)]


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class TenantIn(BaseModel):
    name: str
    slug: str


class TenantOut(BaseModel):
    id: str
    name: str
    slug: str

    model_config = {"from_attributes": True}


class AgentIn(BaseModel):
    name: str
    system_prompt: str = ""
    greeting: str | None = None
    llm_model: str = "google/gemma-4-31b-it"
    tts_provider: m.TTSProvider = m.TTSProvider.INFERENCE
    tts_voice: str | None = None
    voice_sample_uri: str | None = None
    transfer_to: str | None = None


class AgentOut(AgentIn):
    id: str
    is_active: bool

    model_config = {"from_attributes": True}


class CallOut(BaseModel):
    id: str
    direction: m.CallDirection
    status: m.CallStatus
    from_number: str
    to_number: str
    room_name: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    transferred_to: str | None

    model_config = {"from_attributes": True}


class TurnIn(BaseModel):
    speaker: m.Speaker
    text: str
    offset_seconds: float = 0.0


class TurnOut(TurnIn):
    id: str
    model_config = {"from_attributes": True}


class DialCheckIn(BaseModel):
    e164: str
    require_consent: bool = True


class DialCheckOut(BaseModel):
    allowed: bool
    reason: str
    detail: str | None = None


class ContactIn(BaseModel):
    full_name: str | None = None
    e164: str | None = None
    email: str | None = None
    timezone_name: str = "UTC"
    external_id: str | None = None


class ContactOut(ContactIn):
    id: str
    model_config = {"from_attributes": True}


class TicketIn(BaseModel):
    subject: str
    body: str | None = None
    contact_id: str | None = None
    call_id: str | None = None
    priority: str = "normal"


class TicketOut(TicketIn):
    id: str
    status: str
    model_config = {"from_attributes": True}


class OrderIn(BaseModel):
    contact_id: str | None = None
    amount_cents: int = Field(0, ge=0)
    currency: str = "USD"
    external_ref: str | None = None


class OrderOut(OrderIn):
    id: str
    status: str
    model_config = {"from_attributes": True}


class ReservationIn(BaseModel):
    contact_id: str | None = None
    scheduled_for: datetime
    party_size: int = Field(1, ge=1)
    notes: str | None = None


class ReservationOut(ReservationIn):
    id: str
    status: str
    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------
# Health & tenants
# --------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tenants", response_model=TenantOut, status_code=201)
def create_tenant(payload: TenantIn, session: SessionDep) -> m.Tenant:
    existing = session.execute(
        select(m.Tenant).where(m.Tenant.slug == payload.slug)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="slug already in use")
    tenant = m.Tenant(name=payload.name, slug=payload.slug)
    session.add(tenant)
    session.commit()
    return tenant


# --------------------------------------------------------------------------
# Agent builder
# --------------------------------------------------------------------------


@app.post("/agents", response_model=AgentOut, status_code=201)
def create_agent(payload: AgentIn, tenant: TenantDep, session: SessionDep) -> m.VoiceAgent:
    agent = m.VoiceAgent(tenant_id=tenant.id, **payload.model_dump())
    session.add(agent)
    session.commit()
    return agent


@app.get("/agents", response_model=list[AgentOut])
def list_agents(tenant: TenantDep, session: SessionDep) -> list[m.VoiceAgent]:
    return list(
        session.execute(
            select(m.VoiceAgent).where(m.VoiceAgent.tenant_id == tenant.id)
        ).scalars()
    )


@app.get("/agents/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: str, tenant: TenantDep, session: SessionDep) -> m.VoiceAgent:
    agent = session.execute(
        select(m.VoiceAgent).where(
            m.VoiceAgent.id == agent_id, m.VoiceAgent.tenant_id == tenant.id
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


# --------------------------------------------------------------------------
# Calls, transcripts (supervisor dashboard reads these)
# --------------------------------------------------------------------------


@app.get("/calls", response_model=list[CallOut])
def list_calls(
    tenant: TenantDep,
    session: SessionDep,
    status: m.CallStatus | None = None,
    limit: int = Query(50, le=200),
) -> list[m.Call]:
    stmt = select(m.Call).where(m.Call.tenant_id == tenant.id)
    if status is not None:
        stmt = stmt.where(m.Call.status == status)
    stmt = stmt.order_by(m.Call.created_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


@app.get("/calls/{call_id}/transcript", response_model=list[TurnOut])
def get_transcript(call_id: str, tenant: TenantDep, session: SessionDep) -> list[m.TranscriptTurn]:
    call = session.execute(
        select(m.Call).where(m.Call.id == call_id, m.Call.tenant_id == tenant.id)
    ).scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=404, detail="call not found")
    return list(
        session.execute(
            select(m.TranscriptTurn)
            .where(m.TranscriptTurn.call_id == call_id)
            .order_by(m.TranscriptTurn.offset_seconds)
        ).scalars()
    )


@app.post("/calls/{call_id}/transcript", response_model=TurnOut, status_code=201)
def append_turn(
    call_id: str, payload: TurnIn, tenant: TenantDep, session: SessionDep
) -> m.TranscriptTurn:
    """Called by the voice agent as the conversation happens."""
    call = session.execute(
        select(m.Call).where(m.Call.id == call_id, m.Call.tenant_id == tenant.id)
    ).scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=404, detail="call not found")
    turn = m.TranscriptTurn(call_id=call_id, **payload.model_dump())
    session.add(turn)
    session.commit()
    return turn


@app.get("/stats")
def stats(tenant: TenantDep, session: SessionDep) -> dict[str, object]:
    """Headline numbers for the supervisor dashboard."""
    total = session.execute(
        select(func.count()).select_from(m.Call).where(m.Call.tenant_id == tenant.id)
    ).scalar_one()
    live = session.execute(
        select(func.count())
        .select_from(m.Call)
        .where(m.Call.tenant_id == tenant.id, m.Call.status == m.CallStatus.IN_PROGRESS)
    ).scalar_one()
    transferred = session.execute(
        select(func.count())
        .select_from(m.Call)
        .where(m.Call.tenant_id == tenant.id, m.Call.status == m.CallStatus.TRANSFERRED)
    ).scalar_one()
    avg_duration = session.execute(
        select(func.avg(m.Call.duration_seconds)).where(
            m.Call.tenant_id == tenant.id, m.Call.duration_seconds.is_not(None)
        )
    ).scalar_one()
    return {
        "calls_total": total,
        "calls_live": live,
        "calls_transferred": transferred,
        "avg_duration_seconds": float(avg_duration) if avg_duration is not None else None,
    }


# --------------------------------------------------------------------------
# Compliance
# --------------------------------------------------------------------------


@app.post("/compliance/check-dialable", response_model=DialCheckOut)
def check_number(payload: DialCheckIn, tenant: TenantDep, session: SessionDep) -> DialCheckOut:
    """The dialler calls this before every outbound attempt."""
    contact = session.execute(
        select(m.Contact).where(
            m.Contact.tenant_id == tenant.id, m.Contact.e164 == payload.e164
        )
    ).scalar_one_or_none()
    decision = check_dialable(
        session,
        tenant_id=tenant.id,
        e164=payload.e164,
        contact=contact,
        require_consent=payload.require_consent,
    )
    return DialCheckOut(
        allowed=decision.allowed, reason=decision.reason, detail=decision.detail
    )


@app.post("/compliance/dnc", status_code=201)
def add_dnc(payload: DialCheckIn, tenant: TenantDep, session: SessionDep) -> dict[str, str]:
    """Record a do-not-call request. Idempotent — repeat requests are not errors."""
    existing = session.execute(
        select(m.DoNotCall).where(
            m.DoNotCall.tenant_id == tenant.id, m.DoNotCall.e164 == payload.e164
        )
    ).scalar_one_or_none()
    if existing:
        return {"status": "already_listed", "e164": payload.e164}
    session.add(m.DoNotCall(tenant_id=tenant.id, e164=payload.e164))
    session.commit()
    return {"status": "listed", "e164": payload.e164}


@app.post("/compliance/consent", status_code=201)
def add_consent(
    payload: DialCheckIn, tenant: TenantDep, session: SessionDep, basis: str = "express_written"
) -> dict[str, str]:
    session.add(
        m.ConsentRecord(
            tenant_id=tenant.id,
            e164=payload.e164,
            basis=basis,
            captured_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    return {"status": "recorded", "e164": payload.e164}


# --------------------------------------------------------------------------
# KobeOS domain
# --------------------------------------------------------------------------


@app.post("/contacts", response_model=ContactOut, status_code=201)
def create_contact(payload: ContactIn, tenant: TenantDep, session: SessionDep) -> m.Contact:
    contact = m.Contact(tenant_id=tenant.id, **payload.model_dump())
    session.add(contact)
    session.commit()
    return contact


@app.get("/contacts", response_model=list[ContactOut])
def list_contacts(tenant: TenantDep, session: SessionDep) -> list[m.Contact]:
    return list(
        session.execute(
            select(m.Contact).where(m.Contact.tenant_id == tenant.id)
        ).scalars()
    )


@app.post("/tickets", response_model=TicketOut, status_code=201)
def create_ticket(payload: TicketIn, tenant: TenantDep, session: SessionDep) -> m.SupportTicket:
    ticket = m.SupportTicket(tenant_id=tenant.id, **payload.model_dump())
    session.add(ticket)
    session.commit()
    return ticket


@app.get("/tickets", response_model=list[TicketOut])
def list_tickets(tenant: TenantDep, session: SessionDep) -> list[m.SupportTicket]:
    return list(
        session.execute(
            select(m.SupportTicket).where(m.SupportTicket.tenant_id == tenant.id)
        ).scalars()
    )


@app.post("/orders", response_model=OrderOut, status_code=201)
def create_order(payload: OrderIn, tenant: TenantDep, session: SessionDep) -> m.Order:
    order = m.Order(tenant_id=tenant.id, **payload.model_dump())
    session.add(order)
    session.commit()
    return order


@app.get("/orders", response_model=list[OrderOut])
def list_orders(tenant: TenantDep, session: SessionDep) -> list[m.Order]:
    return list(
        session.execute(select(m.Order).where(m.Order.tenant_id == tenant.id)).scalars()
    )


@app.post("/reservations", response_model=ReservationOut, status_code=201)
def create_reservation(
    payload: ReservationIn, tenant: TenantDep, session: SessionDep
) -> m.Reservation:
    reservation = m.Reservation(tenant_id=tenant.id, **payload.model_dump())
    session.add(reservation)
    session.commit()
    return reservation


@app.get("/reservations", response_model=list[ReservationOut])
def list_reservations(tenant: TenantDep, session: SessionDep) -> list[m.Reservation]:
    return list(
        session.execute(
            select(m.Reservation).where(m.Reservation.tenant_id == tenant.id)
        ).scalars()
    )
