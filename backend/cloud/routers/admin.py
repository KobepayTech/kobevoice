"""Internal Kobevoice studio backoffice — admin-only operations."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Plan, Subscription, User
from ..quotas import current_period, usage_for_period
from ..schemas import AdminStats, AdminUserRow, UserResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats", response_model=AdminStats)
def stats(
    _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> AdminStats:
    total_users = db.query(User).count()
    active_subs = (
        db.query(Subscription)
        .filter(Subscription.status.in_(("active", "trialing")))
        .all()
    )
    period = current_period()
    gens = 0
    mrr = 0
    for sub in active_subs:
        if sub.plan:
            mrr += sub.plan.price_cents if sub.plan.interval == "month" else sub.plan.price_cents // 12
    for user in db.query(User).all():
        gens += usage_for_period(db, user, "generation", period)
    return AdminStats(
        total_users=total_users,
        active_subscriptions=len(active_subs),
        generations_this_period=gens,
        mrr_cents=mrr,
    )


@router.get("/users", response_model=list[AdminUserRow])
def list_users(
    _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[AdminUserRow]:
    period = current_period()
    rows: list[AdminUserRow] = []
    for user in db.query(User).order_by(User.created_at.desc()).all():
        sub = user.subscription
        rows.append(
            AdminUserRow(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_admin=user.is_admin,
                is_active=user.is_active,
                created_at=user.created_at,
                plan=sub.plan.slug if sub and sub.plan else None,
                subscription_status=sub.status if sub else None,
                generations_this_period=usage_for_period(db, user, "generation", period),
            )
        )
    return rows


@router.post("/users/{user_id}/disable", response_model=UserResponse)
def disable_user(
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot disable yourself")
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/enable", response_model=UserResponse)
def enable_user(
    user_id: str,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_active = True
    db.commit()
    db.refresh(user)
    return user
