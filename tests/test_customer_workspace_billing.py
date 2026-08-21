from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from pydantic import SecretStr

from app.config import Settings
from app.customer_billing import create_growth_balance_checkout

PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_growth_balance_checkout_can_return_to_customer_workspace(monkeypatch) -> None:
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="cs_workspace", url="https://checkout.stripe.test/session")

    monkeypatch.setattr("stripe.checkout.Session.create", fake_create)
    settings = Settings(_env_file=None, stripe_secret_key=SecretStr("sk_test_workspace"))

    checkout = create_growth_balance_checkout(
        settings=settings,
        project_id=PROJECT_ID,
        public_origin="https://partizan.example.com",
        checkout_generation=3,
        amount_cents=100_000,
        stripe_customer_id=None,
        return_path="/workspace",
    )

    assert checkout.session_id == "cs_workspace"
    assert captured["success_url"] == (
        f"https://partizan.example.com/workspace?growth_balance=success&project={PROJECT_ID}"
        "&session_id={CHECKOUT_SESSION_ID}"
    )
    assert captured["cancel_url"] == (
        f"https://partizan.example.com/workspace?growth_balance=cancelled&project={PROJECT_ID}"
    )


def test_growth_balance_checkout_keeps_start_as_default_return_path(monkeypatch) -> None:
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="cs_start", url="https://checkout.stripe.test/session")

    monkeypatch.setattr("stripe.checkout.Session.create", fake_create)
    settings = Settings(_env_file=None, stripe_secret_key=SecretStr("sk_test_start"))

    create_growth_balance_checkout(
        settings=settings,
        project_id=PROJECT_ID,
        public_origin="https://partizan.example.com",
        checkout_generation=4,
        amount_cents=50_000,
        stripe_customer_id="cus_existing",
    )

    assert "/start?growth_balance=success" in captured["success_url"]
    assert "/start?growth_balance=cancelled" in captured["cancel_url"]
    assert captured["customer"] == "cus_existing"
