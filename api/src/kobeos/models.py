"""Database models for the Kobe Voice call-center platform.

Two things drive the shape of this schema:

**Tenancy.** Every business row carries ``tenant_id``. One deployment serves many
companies, and a query that forgets the tenant filter leaks one company's calls
into another's dashboard — so tenancy is on the base class, not opt-in per table.

**Compliance is data, not policy.** Outbound calling (especially collections) is
regulated: consent must be provable, do-not-call requests must be honoured
permanently, calling hours are local to the *called party*, and recording consent
varies by jurisdiction. Those obligations are modelled as first-class tables
because retrofitting them into a live dialler means re-contacting everyone.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    # Timezone-aware throughout: call records are compared across time zones for
    # calling-window rules, and naive datetimes make that silently wrong.
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------


class Tenant(Base, TimestampMixin):
    """A company using the platform."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    agents: Mapped[list["VoiceAgent"]] = relationship(back_populates="tenant")
    phone_numbers: Mapped[list["PhoneNumber"]] = relationship(back_populates="tenant")


class TenantScoped:
    """Mixin for every table that belongs to a single tenant."""

    @property
    def _tenant_fk(self) -> str:  # pragma: no cover - documentation helper
        return "tenants.id"


# --------------------------------------------------------------------------
# Agent configuration (the "agent builder" persists here)
# --------------------------------------------------------------------------


class TTSProvider(str, enum.Enum):
    INFERENCE = "inference"
    CHATTERBOX = "chatterbox"
    MISO = "miso"


class VoiceAgent(Base, TimestampMixin):
    """A configured voice agent. One tenant may run several."""

    __tablename__ = "voice_agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    greeting: Mapped[str | None] = mapped_column(Text, nullable=True)

    llm_model: Mapped[str] = mapped_column(String(120), default="google/gemma-4-31b-it")
    tts_provider: Mapped[TTSProvider] = mapped_column(
        Enum(TTSProvider), default=TTSProvider.INFERENCE
    )
    tts_voice: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Path/URI of the reference clip when tts_provider clones a voice.
    voice_sample_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Number to hand off to when the caller asks for a human.
    transfer_to: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    tenant: Mapped[Tenant] = relationship(back_populates="agents")

    __table_args__ = (Index("ix_voice_agents_tenant", "tenant_id"),)


class PhoneNumber(Base, TimestampMixin):
    """An E.164 number owned by a tenant, routed to an agent for inbound calls."""

    __tablename__ = "phone_numbers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    e164: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("voice_agents.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(40), default="sip")

    tenant: Mapped[Tenant] = relationship(back_populates="phone_numbers")

    # A number can only be claimed once across the whole platform, otherwise
    # inbound routing is ambiguous.
    __table_args__ = (UniqueConstraint("e164", name="uq_phone_numbers_e164"),)


# --------------------------------------------------------------------------
# Calls, transcripts, recordings
# --------------------------------------------------------------------------


class CallDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallStatus(str, enum.Enum):
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_ANSWER = "no_answer"
    VOICEMAIL = "voicemail"
    TRANSFERRED = "transferred"


class Call(Base, TimestampMixin):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("voice_agents.id", ondelete="SET NULL"), nullable=True
    )
    contact_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )

    direction: Mapped[CallDirection] = mapped_column(Enum(CallDirection), nullable=False)
    status: Mapped[CallStatus] = mapped_column(
        Enum(CallStatus), default=CallStatus.QUEUED, nullable=False
    )
    from_number: Mapped[str] = mapped_column(String(32), nullable=False)
    to_number: Mapped[str] = mapped_column(String(32), nullable=False)

    # LiveKit room backing this call, for joining a live supervisor session.
    room_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    # Set when the agent hands off to a human, for transfer-rate reporting.
    transferred_to: Mapped[str | None] = mapped_column(String(32))
    disposition: Mapped[str | None] = mapped_column(String(120))

    transcript_turns: Mapped[list["TranscriptTurn"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )
    recording: Mapped["Recording | None"] = relationship(
        back_populates="call", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_calls_tenant_started", "tenant_id", "started_at"),
        Index("ix_calls_tenant_status", "tenant_id", "status"),
    )


class Speaker(str, enum.Enum):
    AGENT = "agent"
    CALLER = "caller"


class TranscriptTurn(Base):
    """One utterance. Rows stream in during the call to drive the live view."""

    __tablename__ = "transcript_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    speaker: Mapped[Speaker] = mapped_column(Enum(Speaker), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Seconds from call start, so turns order correctly regardless of write time.
    offset_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    call: Mapped[Call] = relationship(back_populates="transcript_turns")

    __table_args__ = (Index("ix_transcript_call_offset", "call_id", "offset_seconds"),)


class Recording(Base, TimestampMixin):
    """Audio recording of a call.

    ``consent_basis`` is required rather than nullable: in two-party-consent
    jurisdictions a recording without a recorded basis is a liability, and a
    nullable column makes it easy to create one by accident.
    """

    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    storage_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    consent_basis: Mapped[str] = mapped_column(String(80), nullable=False)

    call: Mapped[Call] = relationship(back_populates="recording")


# --------------------------------------------------------------------------
# Compliance
# --------------------------------------------------------------------------


class DoNotCall(Base):
    """A number that must never be dialled again for this tenant.

    Deliberately has no ``is_active`` flag — a DNC request is not something the
    dialler should be able to toggle off. Removal is a manual, audited action.
    """

    __tablename__ = "do_not_call"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    e164: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(40), default="caller_request")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("tenant_id", "e164", name="uq_dnc_tenant_number"),
        Index("ix_dnc_lookup", "tenant_id", "e164"),
    )


class ConsentRecord(Base):
    """Evidence that a contact agreed to be contacted.

    Kept append-only: the defence against a complaint is the record as it existed
    at dial time, so consent is superseded by a newer row rather than edited.
    """

    __tablename__ = "consent_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    e164: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), default="voice")
    basis: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_uri: Mapped[str | None] = mapped_column(String(500))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_consent_lookup", "tenant_id", "e164"),)


# --------------------------------------------------------------------------
# KobeOS domain
# --------------------------------------------------------------------------


class Contact(Base, TimestampMixin):
    """A customer of the tenant."""

    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    full_name: Mapped[str | None] = mapped_column(String(200))
    e164: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(200))
    # IANA zone, used to keep dialling inside the called party's legal hours.
    timezone_name: Mapped[str] = mapped_column(String(64), default="UTC")
    external_id: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        Index("ix_contacts_tenant_phone", "tenant_id", "e164"),
        UniqueConstraint("tenant_id", "external_id", name="uq_contact_external"),
    )


class Reservation(Base, TimestampMixin):
    __tablename__ = "reservations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    party_size: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="booked")
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_reservations_tenant_time", "tenant_id", "scheduled_for"),)


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    # Integer minor units — floats accumulate rounding error on money.
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    external_ref: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (Index("ix_orders_tenant_status", "tenant_id", "status"),)


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("orders.id", ondelete="SET NULL")
    )
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    provider_ref: Mapped[str | None] = mapped_column(String(160))

    __table_args__ = (Index("ix_payments_tenant_status", "tenant_id", "status"),)


class SupportTicket(Base, TimestampMixin):
    __tablename__ = "support_tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    call_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("calls.id", ondelete="SET NULL")
    )
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="open")
    priority: Mapped[str] = mapped_column(String(20), default="normal")

    __table_args__ = (Index("ix_tickets_tenant_status", "tenant_id", "status"),)
