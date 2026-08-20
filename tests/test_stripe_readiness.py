import pytest
import stripe

from app.config import Settings
from app.stripe_readiness import StripeReadinessError, verify_launch_price


def _settings(**overrides) -> Settings:
    values = {
        "stripe_secret_key": "sk_test_not_real",
        "stripe_launch_price_id": "price_launch_not_real",
        "partizan_launch_price_usd": 49,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _price(**fields) -> stripe.Price:
    """Build the object shape `stripe.Price.retrieve` actually returns."""
    payload = {
        "id": "price_launch_not_real",
        "active": True,
        "type": "one_time",
        "currency": "usd",
        "unit_amount": 4900,
    }
    payload.update(fields)
    return stripe.Price.construct_from(payload, "sk_test_not_real")


def test_retrieved_price_is_not_a_mapping() -> None:
    with pytest.raises(AttributeError):
        _price().get("active")


def test_launch_price_readiness_accepts_active_49_usd_one_time(monkeypatch) -> None:
    monkeypatch.setattr(stripe.Price, "retrieve", lambda price_id: _price(id=price_id))

    verify_launch_price(_settings())


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"active": False}, "active"),
        ({"type": "recurring"}, "one-time"),
        ({"currency": "eur"}, "USD"),
        ({"unit_amount": 9900}, "$49 USD"),
    ],
)
def test_launch_price_readiness_rejects_wrong_commercial_config(
    monkeypatch, fields, message
) -> None:
    monkeypatch.setattr(stripe.Price, "retrieve", lambda price_id: _price(**fields))

    with pytest.raises(StripeReadinessError, match=message.replace("$", r"\$")):
        verify_launch_price(_settings())


def test_launch_price_readiness_rejects_a_price_missing_expected_fields(monkeypatch) -> None:
    bare = stripe.Price.construct_from({"id": "price_launch_not_real"}, "sk_test_not_real")
    monkeypatch.setattr(stripe.Price, "retrieve", lambda price_id: bare)

    with pytest.raises(StripeReadinessError, match="active"):
        verify_launch_price(_settings())


def test_launch_price_readiness_fails_closed_without_billing_config() -> None:
    with pytest.raises(StripeReadinessError, match="STRIPE_SECRET_KEY"):
        verify_launch_price(_settings(stripe_secret_key=None))

    with pytest.raises(StripeReadinessError, match="STRIPE_LAUNCH_PRICE_ID"):
        verify_launch_price(_settings(stripe_launch_price_id=None))


def test_readiness_module_has_no_autopilot_subscription_price_contract() -> None:
    import app.stripe_readiness as readiness

    assert not hasattr(readiness, "verify_autopilot_price")
