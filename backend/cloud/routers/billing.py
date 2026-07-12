"""Billing: list plans, start checkout, customer portal, Stripe webhook."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user
from ..models import Plan, Subscription, User
from ..schemas import (
    CheckoutRequest,
    CheckoutResponse,
    PlanResponse,
    SubscriptionResponse,
)
from .. import stripe_client
from ..quotas import get_effective_plan

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/plans", response_model=list[PlanResponse])
def list_plans(db: Session = Depends(get_db)) -> list[Plan]:
    return db.query(Plan).filter(Plan.is_active == True).all()  # noqa: E712


@router.get("/subscription", response_model=SubscriptionResponse)
def my_subscription(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SubscriptionResponse:
    plan = get_effective_plan(db, user)
    sub = user.subscription
    return SubscriptionResponse(
        status=sub.status if sub else "free",
        plan=PlanResponse.model_validate(plan),
        current_period_end=sub.current_period_end if sub else None,
        cancel_at_period_end=sub.cancel_at_period_end if sub else False,
    )


def _activate_plan_locally(db: Session, user: User, plan: Plan, *, status_value: str = "active") -> None:
    """Upsert the user's subscription to a plan without Stripe (dev simulator)."""
    from datetime import datetime, timedelta, timezone

    sub = user.subscription
    if sub is None:
        sub = Subscription(user_id=user.id, plan_id=plan.id)
        db.add(sub)
    sub.plan_id = plan.id
    sub.status = status_value
    sub.cancel_at_period_end = False
    sub.current_period_start = datetime.now(timezone.utc)
    sub.current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
    db.commit()


@router.post("/checkout", response_model=CheckoutResponse)
def create_checkout(
    payload: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CheckoutResponse:
    settings = get_settings()
    plan = db.query(Plan).filter(Plan.slug == payload.plan_slug).first()
    if plan is None or plan.slug == "free":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid plan")

    # Dev simulator: no Stripe, activate immediately so the flow is testable.
    if not settings.billing_enabled:
        if settings.billing_dev_mode:
            _activate_plan_locally(db, user, plan)
            return CheckoutResponse(url=f"{settings.frontend_url}/?checkout=success&dev=1")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured on this server",
        )

    if not plan.stripe_price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Plan '{plan.slug}' has no Stripe price configured",
        )

    customer_id = stripe_client.ensure_customer(
        email=user.email, existing_id=user.stripe_customer_id
    )
    if user.stripe_customer_id != customer_id:
        user.stripe_customer_id = customer_id
        db.commit()

    url = stripe_client.create_checkout_session(
        customer_id=customer_id,
        price_id=plan.stripe_price_id,
        success_url=f"{settings.frontend_url}/account?checkout=success",
        cancel_url=f"{settings.frontend_url}/pricing?checkout=cancel",
    )
    return CheckoutResponse(url=url)


@router.post("/portal", response_model=CheckoutResponse)
def billing_portal(
    user: User = Depends(get_current_user),
) -> CheckoutResponse:
    settings = get_settings()
    if not user.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No billing account yet"
        )
    url = stripe_client.create_billing_portal(
        customer_id=user.stripe_customer_id,
        return_url=f"{settings.frontend_url}/account",
    )
    return CheckoutResponse(url=url)


def _plan_by_price(db: Session, price_id: str | None) -> Plan | None:
    if not price_id:
        return None
    return db.query(Plan).filter(Plan.stripe_price_id == price_id).first()


def _apply_subscription_event(db: Session, sub_obj: dict) -> None:
    """Upsert a local Subscription row from a Stripe subscription object."""
    from datetime import datetime, timezone

    customer_id = sub_obj.get("customer")
    user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
    if user is None:
        logger.warning("Webhook: no user for customer %s", customer_id)
        return

    items = (sub_obj.get("items") or {}).get("data") or []
    price_id = items[0]["price"]["id"] if items else None
    plan = _plan_by_price(db, price_id)

    sub = user.subscription
    if sub is None:
        sub = Subscription(user_id=user.id, plan_id=plan.id if plan else None)
        db.add(sub)

    if plan is not None:
        sub.plan_id = plan.id
    sub.stripe_subscription_id = sub_obj.get("id")
    sub.status = sub_obj.get("status", "active")
    sub.cancel_at_period_end = bool(sub_obj.get("cancel_at_period_end"))
    end = sub_obj.get("current_period_end")
    if end:
        sub.current_period_end = datetime.fromtimestamp(end, tz=timezone.utc)
    db.commit()


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe_client.verify_webhook(payload, signature)
    except stripe_client.BillingUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    except Exception as exc:  # invalid signature / payload
        logger.warning("Stripe webhook rejected: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook")

    etype = event["type"]
    obj = event["data"]["object"]
    if etype in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        if etype == "customer.subscription.deleted":
            obj["status"] = "canceled"
        _apply_subscription_event(db, obj)
    return {"received": True}
