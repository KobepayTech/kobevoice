"""ORM models for the Kobevoice Cloud control-plane.

The data model is deliberately small: public end-users sign up, optionally
subscribe to a paid plan, and consume the voice engine under per-period
quotas. A single internal admin (the Kobevoice "studio" operator) oversees
accounts, plans and usage.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "cloud_users"

    id = Column(String, primary_key=True, default=_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)

    # Stripe linkage (null until the user starts a checkout).
    stripe_customer_id = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, default=_now, nullable=False)

    subscription = relationship(
        "Subscription",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    usage_records = relationship(
        "UsageRecord", back_populates="user", cascade="all, delete-orphan"
    )
    api_keys = relationship(
        "ApiKey", back_populates="user", cascade="all, delete-orphan"
    )


class Plan(Base):
    """A subscription plan. Seeded from code; mirrors landing/pricing tiers."""

    __tablename__ = "cloud_plans"

    id = Column(String, primary_key=True, default=_uuid)
    slug = Column(String, unique=True, nullable=False, index=True)  # free | cloud | pro
    name = Column(String, nullable=False)
    price_cents = Column(Integer, default=0, nullable=False)  # per interval
    interval = Column(String, default="month", nullable=False)  # month | year

    # Quotas. -1 means unlimited.
    monthly_generation_limit = Column(Integer, default=50, nullable=False)
    monthly_character_limit = Column(Integer, default=10_000, nullable=False)
    max_voice_profiles = Column(Integer, default=3, nullable=False)

    stripe_price_id = Column(String, nullable=True)
    features = Column(JSON, default=list, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    subscriptions = relationship("Subscription", back_populates="plan")


class Subscription(Base):
    __tablename__ = "cloud_subscriptions"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("cloud_users.id"), nullable=False, unique=True)
    plan_id = Column(String, ForeignKey("cloud_plans.id"), nullable=False)

    # free | trialing | active | past_due | canceled
    status = Column(String, default="free", nullable=False)
    stripe_subscription_id = Column(String, nullable=True, index=True)

    current_period_start = Column(DateTime, default=_now, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    user = relationship("User", back_populates="subscription")
    plan = relationship("Plan", back_populates="subscriptions")


class UsageRecord(Base):
    """One metered event (a generation). Summed per period for quota checks."""

    __tablename__ = "cloud_usage"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("cloud_users.id"), nullable=False, index=True)
    kind = Column(String, default="generation", nullable=False)  # generation | characters
    amount = Column(Integer, default=1, nullable=False)
    period = Column(String, nullable=False, index=True)  # "YYYY-MM"
    meta = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)

    user = relationship("User", back_populates="usage_records")


class ApiKey(Base):
    """Hashed API key so users/agents can call the cloud voice API headlessly."""

    __tablename__ = "cloud_api_keys"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("cloud_users.id"), nullable=False, index=True)
    name = Column(String, nullable=True)
    prefix = Column(String, nullable=False)  # shown to the user, e.g. "kv_live_ab12"
    hashed_key = Column(String, nullable=False, index=True)
    last_used_at = Column(DateTime, nullable=True)
    revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)

    user = relationship("User", back_populates="api_keys")
