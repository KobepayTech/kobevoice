"""Usage accounting and plan-quota enforcement."""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Plan, Subscription, UsageRecord, User

FREE_PLAN_SLUG = "free"


def current_period() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def get_effective_plan(db: Session, user: User) -> Plan:
    """Return the plan a user is currently entitled to (their active sub, else free)."""
    sub = user.subscription
    if sub and sub.status in ("active", "trialing") and sub.plan is not None:
        return sub.plan
    return db.query(Plan).filter(Plan.slug == FREE_PLAN_SLUG).one()


def usage_for_period(db: Session, user: User, kind: str, period: str | None = None) -> int:
    period = period or current_period()
    total = (
        db.query(func.coalesce(func.sum(UsageRecord.amount), 0))
        .filter(
            UsageRecord.user_id == user.id,
            UsageRecord.kind == kind,
            UsageRecord.period == period,
        )
        .scalar()
    )
    return int(total or 0)


def _within(limit: int, used: int, requested: int) -> bool:
    if limit < 0:  # unlimited
        return True
    return used + requested <= limit


def enforce_quota(db: Session, user: User, *, characters: int) -> Plan:
    """Raise 402 if this request would exceed the user's plan limits.

    Returns the effective plan so callers can avoid a second lookup.
    """
    plan = get_effective_plan(db, user)
    period = current_period()

    gens_used = usage_for_period(db, user, "generation", period)
    chars_used = usage_for_period(db, user, "characters", period)

    if not _within(plan.monthly_generation_limit, gens_used, 1):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Monthly generation limit reached for the {plan.name} plan "
                f"({plan.monthly_generation_limit}). Upgrade to continue."
            ),
        )
    if not _within(plan.monthly_character_limit, chars_used, characters):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Monthly character limit reached for the {plan.name} plan "
                f"({plan.monthly_character_limit}). Upgrade to continue."
            ),
        )
    return plan


def record_usage(db: Session, user: User, *, characters: int) -> None:
    period = current_period()
    db.add(UsageRecord(user_id=user.id, kind="generation", amount=1, period=period))
    db.add(
        UsageRecord(
            user_id=user.id, kind="characters", amount=characters, period=period
        )
    )
    db.commit()
