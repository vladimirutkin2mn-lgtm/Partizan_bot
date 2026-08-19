from types import SimpleNamespace
from uuid import uuid4

import stripe

from app.config import Settings
from app.customer_billing import create_growth_balance_checkout, create_launch_checkout


def test_launch_checkout_is_idempotent_and_creates_reusable_customer(monkeypatch) -> None:
    project_id = uuid4()
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="cs_test_partizan", url="https://checkout.stripe.com/test")

    monkeypatch.setattr(stripe.checkout.Session, "create", fake_create)
    settings = Settings(
        _env_file=None,
        stripe_secret_key="sk_test_not_real",
        stripe_launch_price_id="price_launch_not_real",
    )

    checkout = create_launch_checkout(
        settings=settings,
        project_id=project_id,
        public_origin="https://partizan.example.com",
    )

    assert checkout.session_id == "cs_test_partizan"
    assert checkout.url == "https://checkout.stripe.com/test"
    assert captured["mode"] == "payment"
    assert captured["payment_method_types"] == ["card"]
    assert captured["customer_creation"] == "always"
    assert captured["line_items"] == [{"price": "price_launch_not_real", "quantity": 1}]
    assert captured["client_reference_id"] == str(project_id)
    assert captured["metadata"] == {
        "partizan_project_id": str(project_id),
        "partizan_entitlement": "launch_plan",
    }
    assert captured["payment_intent_data"]["metadata"] == captured["metadata"]
    assert captured["idempotency_key"] == f"partizan-launch-{project_id}"
    assert "checkout=success" in captured["success_url"]
    assert "{CHECKOUT_SESSION_ID}" in captured["success_url"]
    assert "checkout=cancelled" in captured["cancel_url"]


def test_growth_balance_checkout_uses_exact_dynamic_usd_amount(monkeypatch) -> None:
    project_id = uuid4()
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="cs_growth_balance", url="https://checkout.stripe.com/growth")

    monkeypatch.setattr(stripe.checkout.Session, "create", fake_create)
    monkeypatch.setattr("app.customer_billing.time.time", lambda: 1_000_000)
    settings = Settings(_env_file=None, stripe_secret_key="sk_test_not_real")

    checkout = create_growth_balance_checkout(
        settings=settings,
        project_id=project_id,
        public_origin="https://partizan.example.com",
        checkout_generation=2,
        amount_cents=100_000,
        stripe_customer_id="cus_existing",
    )

    assert checkout.session_id == "cs_growth_balance"
    assert captured["mode"] == "payment"
    assert captured["customer"] == "cus_existing"
    assert captured["line_items"][0]["price_data"]["currency"] == "usd"
    assert captured["line_items"][0]["price_data"]["unit_amount"] == 100_000
    assert captured["line_items"][0]["price_data"]["product_data"]["name"] == "Partizan Growth Balance"
    assert captured["metadata"] == {
        "partizan_project_id": str(project_id),
        "partizan_entitlement": "growth_balance_topup",
        "partizan_amount_cents": "100000",
        "partizan_checkout_generation": "2",
    }
    assert captured["payment_intent_data"]["metadata"] == captured["metadata"]
    assert captured["expires_at"] == 1_001_800
    assert captured["idempotency_key"] == f"partizan-growth-balance-{project_id}-2-100000"
    assert "growth_balance=success" in captured["success_url"]
    assert "growth_balance=cancelled" in captured["cancel_url"]


def test_billing_module_has_no_recurring_autopilot_checkout() -> None:
    import app.customer_billing as billing

    assert not hasattr(billing, "create_autopilot_checkout")
    assert not hasattr(billing, "retrieve_subscription")
