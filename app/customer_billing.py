from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import stripe

from app.config import Settings


class BillingConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class StripeCheckout:
    session_id: str
    url: str


def _stripe_secret(settings: Settings) -> str:
    if settings.stripe_secret_key is None:
        raise BillingConfigurationError("Stripe Checkout is not configured")
    secret = settings.stripe_secret_key.get_secret_value()
    stripe.api_key = secret
    return secret


def create_launch_checkout(
    *,
    settings: Settings,
    project_id: UUID,
    public_origin: str,
) -> StripeCheckout:
    price_id = settings.stripe_launch_price_id
    if not price_id:
        raise BillingConfigurationError("Stripe launch checkout is not configured")
    _stripe_secret(settings)
    metadata = {
        "partizan_project_id": str(project_id),
        "partizan_entitlement": "launch_plan",
    }
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        customer_creation="always",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(project_id),
        metadata=metadata,
        payment_intent_data={"metadata": metadata},
        success_url=(
            f"{public_origin}/start?checkout=success&project={project_id}"
            "&session_id={CHECKOUT_SESSION_ID}"
        ),
        cancel_url=f"{public_origin}/start?checkout=cancelled&project={project_id}",
        idempotency_key=f"partizan-launch-{project_id}",
    )
    return StripeCheckout(session_id=str(session.id), url=str(session.url))


def create_growth_balance_checkout(
    *,
    settings: Settings,
    project_id: UUID,
    public_origin: str,
    checkout_generation: int,
    amount_cents: int,
    stripe_customer_id: str | None,
) -> StripeCheckout:
    _stripe_secret(settings)
    if amount_cents <= 0:
        raise ValueError("Growth Balance amount must be positive")
    metadata = {
        "partizan_project_id": str(project_id),
        "partizan_entitlement": "growth_balance_topup",
        "partizan_amount_cents": str(amount_cents),
        "partizan_checkout_generation": str(checkout_generation),
    }
    kwargs: dict = {
        "mode": "payment",
        "payment_method_types": ["card"],
        "line_items": [
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "product_data": {
                        "name": "Partizan Growth Balance",
                        "description": (
                            "Prepaid all-in growth budget for acquisition spend and "
                            "Partizan management fees."
                        ),
                    },
                },
                "quantity": 1,
            }
        ],
        "client_reference_id": str(project_id),
        "metadata": metadata,
        "payment_intent_data": {"metadata": metadata},
        "expires_at": int(time.time()) + (30 * 60),
        "success_url": (
            f"{public_origin}/start?growth_balance=success&project={project_id}"
            "&session_id={CHECKOUT_SESSION_ID}"
        ),
        "cancel_url": f"{public_origin}/start?growth_balance=cancelled&project={project_id}",
        "idempotency_key": (
            f"partizan-growth-balance-{project_id}-{checkout_generation}-{amount_cents}"
        ),
    }
    if stripe_customer_id:
        kwargs["customer"] = stripe_customer_id
    else:
        kwargs["customer_creation"] = "always"
    session = stripe.checkout.Session.create(**kwargs)
    return StripeCheckout(session_id=str(session.id), url=str(session.url))


def retrieve_launch_checkout(*, settings: Settings, session_id: str):
    _stripe_secret(settings)
    return stripe.checkout.Session.retrieve(session_id)


def construct_stripe_event(*, settings: Settings, payload: bytes, signature: str):
    if settings.stripe_webhook_secret is None:
        raise BillingConfigurationError("Stripe webhook is not configured")
    return stripe.Webhook.construct_event(
        payload,
        signature,
        settings.stripe_webhook_secret.get_secret_value(),
    )


def usd_to_cents(amount_usd: float) -> int:
    return int(
        Decimal(str(amount_usd)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        * 100
    )
