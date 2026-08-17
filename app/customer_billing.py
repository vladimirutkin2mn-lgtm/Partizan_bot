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


def _configure_stripe(settings: Settings) -> None:
    if settings.stripe_secret_key is None:
        raise BillingConfigurationError("Stripe billing is not configured")
    stripe.api_key = settings.stripe_secret_key.get_secret_value()


def create_launch_checkout(
    *,
    settings: Settings,
    project_id: UUID,
    public_origin: str,
) -> LaunchCheckout:
    if not settings.stripe_launch_price_id:
        raise BillingConfigurationError("Stripe launch checkout is not configured")
    _configure_stripe(settings)

    project_metadata = {
        "partizan_project_id": str(project_id),
        "partizan_entitlement": "launch_plan",
    }
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{"price": settings.stripe_launch_price_id, "quantity": 1}],
        client_reference_id=str(project_id),
        customer_creation="always",
        metadata=project_metadata,
        payment_intent_data={"metadata": project_metadata},
        success_url=(
            f"{public_origin}/start?checkout=success&project={project_id}"
            "&session_id={CHECKOUT_SESSION_ID}"
        ),
        cancel_url=f"{public_origin}/start?checkout=cancelled&project={project_id}",
        idempotency_key=f"partizan-launch-{project_id}",
    )
    if not session.url:
        raise BillingConfigurationError("Stripe Checkout Session did not return a URL")
    return LaunchCheckout(session_id=session.id, url=session.url)


def create_autopilot_checkout(
    *,
    settings: Settings,
    project_id: UUID,
    public_origin: str,
    checkout_generation: int,
    stripe_customer_id: str | None,
) -> LaunchCheckout:
    if not settings.stripe_autopilot_price_id:
        raise BillingConfigurationError("Stripe Autopilot checkout is not configured")
    _configure_stripe(settings)
    metadata = {
        "partizan_project_id": str(project_id),
        "partizan_entitlement": "autopilot",
    }
    kwargs: dict = {
        "mode": "subscription",
        "payment_method_types": ["card"],
        "line_items": [{"price": settings.stripe_autopilot_price_id, "quantity": 1}],
        "client_reference_id": str(project_id),
        "metadata": metadata,
        "subscription_data": {"metadata": metadata},
        "success_url": (
            f"{public_origin}/start?autopilot_checkout=success&project={project_id}"
            "&session_id={CHECKOUT_SESSION_ID}"
        ),
        "cancel_url": f"{public_origin}/start?autopilot_checkout=cancelled&project={project_id}",
        "idempotency_key": f"partizan-autopilot-{project_id}-{checkout_generation}",
    }
    if stripe_customer_id:
        kwargs["customer"] = stripe_customer_id
    session = stripe.checkout.Session.create(**kwargs)
    if not session.url:
        raise BillingConfigurationError("Stripe Autopilot Checkout Session did not return a URL")
    return LaunchCheckout(session_id=session.id, url=session.url)


def retrieve_launch_checkout(*, settings: Settings, session_id: str):
    _configure_stripe(settings)
    return stripe.checkout.Session.retrieve(session_id)


def retrieve_subscription(*, settings: Settings, subscription_id: str):
    _configure_stripe(settings)
    return stripe.Subscription.retrieve(subscription_id)


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
