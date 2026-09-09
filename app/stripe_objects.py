from __future__ import annotations

from typing import Any


def stripe_field(source: Any, key: str, default: Any = None) -> Any:
    """Read one field from a Stripe API response object.

    stripe-python resources deliberately are not dicts: calling `.get()` on one raises
    AttributeError, which escapes the `stripe.StripeError` handlers that guard every
    money path and turns a payment into a 500. Item access works on real resources and
    on the plain dicts used by tests and fakes, so this is the only accessor billing,
    settlement and webhook code should use for Stripe payloads.
    """

    if source is None:
        return default
    try:
        value = source[key]
    except (AttributeError, KeyError, IndexError, TypeError):
        return default
    return default if value is None else value
