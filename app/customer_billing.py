from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import stripe

from app.config import Settings


class BillingConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LaunchCheckout:
    session_id: str
    url: str


def create_launch_checkout(
    *,
    settings: Settings,
    project_id: UUID,
    public_origin: str,
) -> LaunchCheckout:
    if settings.stripe_secret_key is None or not settings.stripe_launch_price_id:
        raise BillingConfigurationError("Stripe launch checkout is not configured")

    stripe.api_key = settings.stripe_secret_key.get_secret_value()
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": settings.stripe_launch_price_id, "quantity": 1}],
        client_reference_id=str(project_id),
        metadata={
            "partizan_project_id": str(project_id),
            "partizan_entitlement": "launch_plan",
        },
        success_url=(
            f"{public_origin}/start?checkout=success&project={project_id}"
            "&session_id={CHECKOUT_SESSION_ID}"
        ),
        cancel_url=f"{public_origin}/start?checkout=cancelled&project={project_id}",
    )
    if not session.url:
        raise BillingConfigurationError("Stripe Checkout Session did not return a URL")
    return LaunchCheckout(session_id=session.id, url=session.url)


def construct_stripe_event(
    *,
    settings: Settings,
    payload: bytes,
    signature: str,
):
    if settings.stripe_webhook_secret is None:
        raise BillingConfigurationError("Stripe webhook verification is not configured")
    return stripe.Webhook.construct_event(
        payload,
        signature,
        settings.stripe_webhook_secret.get_secret_value(),
    )
