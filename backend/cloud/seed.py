"""Seed reference data: subscription plans and the internal admin user."""

import logging

from sqlalchemy.orm import Session

from .config import get_settings
from .models import Plan, User
from .security import hash_password

logger = logging.getLogger(__name__)

# Mirrors landing/src/lib/pricing.ts. Limits are launch placeholders — tune
# them in one place here and they flow through quota enforcement everywhere.
DEFAULT_PLANS = [
    {
        "slug": "free",
        "name": "Free",
        "price_cents": 0,
        "interval": "month",
        "monthly_generation_limit": 30,
        "monthly_character_limit": 10_000,
        "max_voice_profiles": 2,
        "stripe_price_key": None,
        "features": [
            "30 cloud generations / month",
            "2 voice profiles",
            "Standard voices",
        ],
    },
    {
        "slug": "cloud",
        "name": "Cloud",
        "price_cents": 200,  # $2/mo (≈$12/yr launch price)
        "interval": "month",
        "monthly_generation_limit": 1_000,
        "monthly_character_limit": 500_000,
        "max_voice_profiles": 25,
        "stripe_price_key": "stripe_price_cloud",
        "features": [
            "1,000 cloud generations / month",
            "25 voice profiles",
            "Backup & sync",
            "Priority queue",
        ],
    },
    {
        "slug": "pro",
        "name": "Pro",
        "price_cents": 1500,  # $15/mo
        "interval": "month",
        "monthly_generation_limit": -1,  # unlimited
        "monthly_character_limit": -1,
        "max_voice_profiles": -1,
        "stripe_price_key": "stripe_price_pro",
        "features": [
            "Unlimited generations",
            "Unlimited voice profiles",
            "API access",
            "Commercial usage rights",
        ],
    },
]


def seed_plans(db: Session) -> None:
    settings = get_settings()
    for spec in DEFAULT_PLANS:
        plan = db.query(Plan).filter(Plan.slug == spec["slug"]).first()
        price_id = (
            getattr(settings, spec["stripe_price_key"]) or None
            if spec["stripe_price_key"]
            else None
        )
        if plan is None:
            db.add(
                Plan(
                    slug=spec["slug"],
                    name=spec["name"],
                    price_cents=spec["price_cents"],
                    interval=spec["interval"],
                    monthly_generation_limit=spec["monthly_generation_limit"],
                    monthly_character_limit=spec["monthly_character_limit"],
                    max_voice_profiles=spec["max_voice_profiles"],
                    stripe_price_id=price_id,
                    features=spec["features"],
                )
            )
        else:
            # Keep limits/features/price in sync with code on each boot.
            plan.name = spec["name"]
            plan.price_cents = spec["price_cents"]
            plan.monthly_generation_limit = spec["monthly_generation_limit"]
            plan.monthly_character_limit = spec["monthly_character_limit"]
            plan.max_voice_profiles = spec["max_voice_profiles"]
            plan.features = spec["features"]
            plan.stripe_price_id = price_id


def seed_admin(db: Session) -> None:
    settings = get_settings()
    existing = db.query(User).filter(User.email == settings.admin_email).first()
    if existing:
        if not existing.is_admin:
            existing.is_admin = True
        return
    db.add(
        User(
            email=settings.admin_email,
            hashed_password=hash_password(settings.admin_password),
            full_name="Kobevoice Admin",
            is_admin=True,
        )
    )
    logger.info("Seeded admin user: %s", settings.admin_email)
