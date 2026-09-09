from __future__ import annotations

import sys
from uuid import UUID

import stripe

from app.config import Settings, get_settings
from app.growth_balance import GrowthBalanceSettlementService
from app.runtime_store import MemoryRuntimeStateStore


class StripeReadinessError(RuntimeError):
    pass


# Funding readiness is account-wide, so the probe never belongs to a customer project.
_ISSUING_PROBE_PROJECT_ID = UUID("00000000-0000-0000-0000-000000000000")


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


def verify_issuing_rail(
    settings: Settings,
    *,
    required_liquidity_cents: int = 0,
) -> None:
    """Verify the live Partizan-funded Issuing rail before customers can fund a balance.

    Production preflight only validates that the Issuing environment variables look
    right. This checks the money side against Stripe: the cardholder is usable and the
    pre-funded Issuing liquidity actually covers the acquisition capacity Partizan is
    about to promise.
    """

    _configure(settings)
    if settings.growth_balance_settlement_provider != "stripe_issuing":
        raise StripeReadinessError(
            "GROWTH_BALANCE_SETTLEMENT_PROVIDER must be stripe_issuing to verify the funded rail"
        )
    if required_liquidity_cents < 0:
        raise StripeReadinessError("required liquidity must not be negative")
    # funding_readiness never touches the runtime store, so the CLI stays database-free.
    service = GrowthBalanceSettlementService(MemoryRuntimeStateStore(), settings=settings)
    ready, status = service.funding_readiness(
        _ISSUING_PROBE_PROJECT_ID,
        required_liquidity_cents=required_liquidity_cents,
    )
    if not ready:
        raise StripeReadinessError(f"Stripe Issuing rail is not ready ({status})")


def main() -> None:
    settings = get_settings()
    argv = sys.argv[1:]
    check_issuing = "--issuing" in argv
    required_liquidity_cents = 0
    for arg in argv:
        if arg.startswith("--required-liquidity-usd="):
            check_issuing = True
            raw = arg.split("=", 1)[1]
            try:
                required_liquidity_cents = int(round(float(raw) * 100))
            except ValueError:
                print(
                    f"stripe readiness: --required-liquidity-usd must be a number (got {raw!r})",
                    file=sys.stderr,
                )
                raise SystemExit(2) from None
    try:
        verify_launch_price(settings)
        if check_issuing:
            verify_issuing_rail(
                settings,
                required_liquidity_cents=required_liquidity_cents,
            )
    except StripeReadinessError as exc:
        print(f"stripe readiness: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if check_issuing:
        print("stripe readiness: Acquisition Plan Price and Partizan-funded Issuing rail verified")
        return
    print("stripe readiness: Acquisition Plan Price verified")


if __name__ == "__main__":
    main()
