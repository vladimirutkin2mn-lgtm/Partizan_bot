import pytest
import stripe

from app.config import Settings
from app.stripe_readiness import (
    StripeReadinessError,
    verify_issuing_rail,
    verify_launch_price,
)


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


def _issuing_settings(**overrides) -> Settings:
    values = {
        "stripe_secret_key": "sk_test_not_real",
        "stripe_launch_price_id": "price_launch_not_real",
        "growth_balance_settlement_provider": "stripe_issuing",
        "stripe_issuing_cardholder_id": "ich_not_real",
        "stripe_issuing_currency": "usd",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _cardholder(**fields) -> stripe.issuing.Cardholder:
    payload = {
        "id": "ich_not_real",
        "status": "active",
        "requirements": {"disabled_reason": None, "past_due": None},
    }
    payload.update(fields)
    return stripe.issuing.Cardholder.construct_from(payload, "sk_test_not_real")


def _balance(issuing_available_cents: int) -> stripe.Balance:
    payload = {
        "issuing": {"available": [{"amount": issuing_available_cents, "currency": "usd"}]},
    }
    return stripe.Balance.construct_from(payload, "sk_test_not_real")


def _patch_issuing(monkeypatch, *, cardholder=None, available_cents: int = 100_000) -> None:
    monkeypatch.setattr(
        stripe.issuing.Cardholder,
        "retrieve",
        lambda cardholder_id: cardholder or _cardholder(id=cardholder_id),
    )
    monkeypatch.setattr(stripe.Balance, "retrieve", lambda: _balance(available_cents))


def test_issuing_readiness_accepts_an_active_prefunded_rail(monkeypatch) -> None:
    _patch_issuing(monkeypatch, available_cents=100_000)

    verify_issuing_rail(_issuing_settings(), required_liquidity_cents=100_000)


def test_issuing_readiness_rejects_insufficient_prefunded_liquidity(monkeypatch) -> None:
    _patch_issuing(monkeypatch, available_cents=50_000)

    with pytest.raises(StripeReadinessError, match="STRIPE_ISSUING_LIQUIDITY_INSUFFICIENT"):
        verify_issuing_rail(_issuing_settings(), required_liquidity_cents=100_000)


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"status": "inactive"}, "STRIPE_ISSUING_CARDHOLDER_INACTIVE"),
        (
            {"requirements": {"disabled_reason": "requirements.past_due"}},
            "STRIPE_ISSUING_CARDHOLDER_REQUIREMENTS_DUE",
        ),
    ],
)
def test_issuing_readiness_rejects_an_unusable_cardholder(monkeypatch, fields, status) -> None:
    _patch_issuing(monkeypatch, cardholder=_cardholder(**fields))

    with pytest.raises(StripeReadinessError, match=status):
        verify_issuing_rail(_issuing_settings())


def test_issuing_readiness_reports_stripe_outage_instead_of_raising(monkeypatch) -> None:
    def _boom(cardholder_id):
        raise stripe.APIConnectionError("issuing is unreachable")

    monkeypatch.setattr(stripe.issuing.Cardholder, "retrieve", _boom)

    with pytest.raises(StripeReadinessError, match="STRIPE_ISSUING_UNAVAILABLE"):
        verify_issuing_rail(_issuing_settings())


def test_issuing_readiness_fails_closed_while_the_rail_is_disabled() -> None:
    with pytest.raises(StripeReadinessError, match="GROWTH_BALANCE_SETTLEMENT_PROVIDER"):
        verify_issuing_rail(_issuing_settings(growth_balance_settlement_provider="unavailable"))


def test_issuing_readiness_fails_closed_without_a_cardholder(monkeypatch) -> None:
    _patch_issuing(monkeypatch)

    with pytest.raises(StripeReadinessError, match="STRIPE_ISSUING_CARDHOLDER_NOT_CONFIGURED"):
        verify_issuing_rail(_issuing_settings(stripe_issuing_cardholder_id=None))
