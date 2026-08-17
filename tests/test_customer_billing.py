from types import SimpleNamespace
from uuid import uuid4

import stripe

from app.config import Settings
from app.customer_billing import create_launch_checkout


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
