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


def test_launch_price_readiness_accepts_active_49_usd_one_time(monkeypatch) -> None:
    monkeypatch.setattr(
        stripe.Price,
        "retrieve",
        lambda price_id: {
            "id": price_id,
            "active": True,
            "type": "one_time",
            "currency": "usd",
            "unit_amount": 4900,
        },
    )

    verify_launch_price(_settings())


@pytest.mark.parametrize(
    ("price", "message"),
    [
        (
            {"active": False, "type": "one_time", "currency": "usd", "unit_amount": 4900},
            "active",
        ),
        (
            {"active": True, "type": "recurring", "currency": "usd", "unit_amount": 4900},
            "one-time",
        ),
        (
            {"active": True, "type": "one_time", "currency": "eur", "unit_amount": 4900},
            "USD",
        ),
        (
            {"active": True, "type": "one_time", "currency": "usd", "unit_amount": 9900},
            "$49 USD",
        ),
    ],
)
def test_launch_price_readiness_rejects_wrong_commercial_config(monkeypatch, price, message) -> None:
    monkeypatch.setattr(stripe.Price, "retrieve", lambda price_id: price)

    with pytest.raises(StripeReadinessError, match=message.replace("$", r"\$")):
        verify_launch_price(_settings())


def test_launch_price_readiness_fails_closed_without_billing_config() -> None:
    with pytest.raises(StripeReadinessError, match="STRIPE_SECRET_KEY"):
        verify_launch_price(_settings(stripe_secret_key=None))

    with pytest.raises(StripeReadinessError, match="STRIPE_LAUNCH_PRICE_ID"):
        verify_launch_price(_settings(stripe_launch_price_id=None))
