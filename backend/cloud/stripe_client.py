"""Thin Stripe wrapper that degrades gracefully when no key is configured.

Every function raises ``BillingUnavailable`` if Stripe isn't set up, so the
rest of the app can run (and be tested) without Stripe credentials.
"""

from __future__ import annotations

from .config import get_settings


class BillingUnavailable(RuntimeError):
    """Raised when a billing action is attempted without Stripe configured."""


def _client():
    settings = get_settings()
    if not settings.billing_enabled:
        raise BillingUnavailable("Stripe is not configured (set KOBEVOICE_CLOUD_STRIPE_SECRET_KEY)")
    import stripe  # imported lazily so the dep is optional at runtime

    stripe.api_key = settings.stripe_secret_key
    return stripe


def ensure_customer(*, email: str, existing_id: str | None) -> str:
    if existing_id:
        return existing_id
    stripe = _client()
    customer = stripe.Customer.create(email=email)
    return customer.id


def create_checkout_session(
    *, customer_id: str, price_id: str, success_url: str, cancel_url: str
) -> str:
    stripe = _client()
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        allow_promotion_codes=True,
    )
    return session.url


def create_billing_portal(*, customer_id: str, return_url: str) -> str:
    stripe = _client()
    session = stripe.billing_portal.Session.create(
        customer=customer_id, return_url=return_url
    )
    return session.url


def verify_webhook(payload: bytes, signature: str):
    settings = get_settings()
    stripe = _client()
    if not settings.stripe_webhook_secret:
        raise BillingUnavailable("Webhook secret not configured")
    return stripe.Webhook.construct_event(
        payload, signature, settings.stripe_webhook_secret
    )
