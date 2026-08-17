from __future__ import annotations

import sys

import stripe

from app.config import Settings, get_settings


class StripeReadinessError(RuntimeError):
    pass


def _configure(settings: Settings) -> None:
    if settings.stripe_secret_key is None:
        raise StripeReadinessError("STRIPE_SECRET_KEY is not configured")
    stripe.api_key = settings.stripe_secret_key.get_secret_value()


def verify_launch_price(settings: Settings) -> None:
    _configure(settings)
    if not settings.stripe_launch_price_id:
        raise StripeReadinessError("STRIPE_LAUNCH_PRICE_ID is not configured")
    try:
        price = stripe.Price.retrieve(settings.stripe_launch_price_id)
    except stripe.StripeError as exc:
        raise StripeReadinessError("Stripe launch Price could not be retrieved") from exc

    expected_amount = settings.partizan_launch_price_usd * 100
    if not getattr(price, "active", False):
        raise StripeReadinessError("Stripe launch Price must be active")
    if getattr(price, "type", None) != "one_time":
        raise StripeReadinessError("Stripe launch Price must be one-time")
    if str(getattr(price, "currency", None) or "").lower() != "usd":
        raise StripeReadinessError("Stripe launch Price must use USD")
    if getattr(price, "unit_amount", None) != expected_amount:
        raise StripeReadinessError(
            f"Stripe launch Price must be ${settings.partizan_launch_price_usd} USD"
        )


def verify_autopilot_price(settings: Settings) -> None:
    _configure(settings)
    if not settings.stripe_autopilot_price_id:
        raise StripeReadinessError("STRIPE_AUTOPILOT_PRICE_ID is not configured")
    try:
        price = stripe.Price.retrieve(settings.stripe_autopilot_price_id)
    except stripe.StripeError as exc:
        raise StripeReadinessError("Stripe Autopilot Price could not be retrieved") from exc

    expected_amount = settings.partizan_autopilot_price_usd * 100
    recurring = getattr(price, "recurring", None)
    interval = getattr(recurring, "interval", None) if recurring is not None else None
    if not getattr(price, "active", False):
        raise StripeReadinessError("Stripe Autopilot Price must be active")
    if getattr(price, "type", None) != "recurring":
        raise StripeReadinessError("Stripe Autopilot Price must be recurring")
    if interval != "month":
        raise StripeReadinessError("Stripe Autopilot Price must recur monthly")
    if str(getattr(price, "currency", None) or "").lower() != "usd":
        raise StripeReadinessError("Stripe Autopilot Price must use USD")
    if getattr(price, "unit_amount", None) != expected_amount:
        raise StripeReadinessError(
            f"Stripe Autopilot Price must be ${settings.partizan_autopilot_price_usd} USD"
        )


def main() -> None:
    settings = get_settings()
    try:
        verify_launch_price(settings)
        verify_autopilot_price(settings)
    except StripeReadinessError as exc:
        print(f"stripe readiness: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("stripe readiness: launch and Autopilot Prices verified")


if __name__ == "__main__":
    main()
