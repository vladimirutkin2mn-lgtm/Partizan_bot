"""Stripe sends resource objects, not dicts.

`stripe.StripeObject.get` raises AttributeError, so any billing code that treats a
Stripe payload like a mapping fails at runtime on exactly the paths that move money.
These tests exercise the money paths with real Stripe objects instead of the plain
dicts the other suites use as fixtures.
"""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
import stripe
from fastapi.testclient import TestClient

from app.config import Settings
from app.growth_balance import (
    ADVERTISING_MERCHANT_CATEGORY,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GrowthBalanceSettlementService,
)
from app.main import app
from app.runtime_store import MemoryRuntimeStateStore
from app.stripe_objects import stripe_field

ROOT = Path(__file__).resolve().parent.parent
client = TestClient(app)
PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _issuing_settings() -> Settings:
    return Settings(
        _env_file=None,
        stripe_secret_key="sk_test_not_real",
        growth_balance_settlement_provider="stripe_issuing",
        stripe_issuing_cardholder_id="ich_not_real",
        stripe_issuing_currency="usd",
    )


def _bound_rail(store: MemoryRuntimeStateStore) -> dict:
    rail = {
        "project_id": str(PROJECT_ID),
        "provider": "stripe_issuing",
        "card_id": "ic_partizan_project",
        "card_last4": "4242",
        "card_status": "active",
        "currency": "usd",
        "allowed_categories": [ADVERTISING_MERCHANT_CATEGORY],
        "acquisition_limit_cents": 90_909,
        "binding_status": "BOUND",
        "bound_provider": "meta",
        "bound_provider_account_id": "act_123",
    }
    store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID), rail)
    return rail


def test_stripe_resources_reject_mapping_access_but_stripe_field_reads_them() -> None:
    session = stripe.checkout.Session.construct_from(
        {"id": "cs_live_1", "metadata": {"partizan_entitlement": "growth_balance_topup"}},
        "sk_test_not_real",
    )

    with pytest.raises(AttributeError):
        session.get("metadata")

    assert stripe_field(session, "id") == "cs_live_1"
    assert stripe_field(stripe_field(session, "metadata"), "partizan_entitlement") == (
        "growth_balance_topup"
    )
    assert stripe_field(session, "customer") is None
    assert stripe_field(session, "amount_total", 0) == 0
    # Plain dicts stay supported so existing fakes keep describing real behaviour.
    assert stripe_field({"amount": 250}, "amount") == 250


def test_growth_balance_webhook_credits_a_real_stripe_event(monkeypatch) -> None:
    project_id = uuid4()
    event = stripe.Event.construct_from(
        {
            "id": "evt_live_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_live_topup",
                    "object": "checkout.session",
                    "payment_status": "paid",
                    "amount_total": 100_000,
                    "currency": "usd",
                    "customer": "cus_live_1",
                    "metadata": {
                        "partizan_project_id": str(project_id),
                        "partizan_entitlement": "growth_balance_topup",
                    },
                }
            },
        },
        "sk_test_not_real",
    )
    credited: list[dict] = []

    def _credit(project, *, session_id, amount_cents, currency, stripe_customer_id):
        credited.append(
            {
                "project_id": project,
                "session_id": session_id,
                "amount_cents": amount_cents,
                "currency": currency,
                "stripe_customer_id": stripe_customer_id,
            }
        )
        return True

    monkeypatch.setattr("app.customer_routes.construct_stripe_event", lambda **kwargs: event)
    monkeypatch.setattr(
        "app.customer_routes.growth_balance_service.credit_paid_checkout",
        _credit,
    )

    response = client.post(
        "/v1/billing/stripe/webhook",
        content=b"signed-payload",
        headers={"Stripe-Signature": "t=1,v1=test"},
    )

    assert response.status_code == 200
    assert credited == [
        {
            "project_id": project_id,
            "session_id": "cs_live_topup",
            "amount_cents": 100_000,
            "currency": "usd",
            "stripe_customer_id": "cus_live_1",
        }
    ]


def test_funding_readiness_reads_a_real_cardholder_and_balance(monkeypatch) -> None:
    service = GrowthBalanceSettlementService(MemoryRuntimeStateStore(), settings=_issuing_settings())
    monkeypatch.setattr(
        stripe.issuing.Cardholder,
        "retrieve",
        lambda cardholder_id: stripe.issuing.Cardholder.construct_from(
            {"id": cardholder_id, "status": "active", "requirements": {}},
            "sk_test_not_real",
        ),
    )
    monkeypatch.setattr(
        stripe.Balance,
        "retrieve",
        lambda: stripe.Balance.construct_from(
            {"issuing": {"available": [{"amount": 100_000, "currency": "usd"}]}},
            "sk_test_not_real",
        ),
    )

    assert service.funding_readiness(PROJECT_ID, required_liquidity_cents=100_000) == (
        True,
        "READY_FOR_FUNDING",
    )
    assert service.funding_readiness(PROJECT_ID, required_liquidity_cents=100_001) == (
        False,
        "STRIPE_ISSUING_LIQUIDITY_INSUFFICIENT",
    )


def test_authorization_decision_reads_a_real_issuing_authorization() -> None:
    store = MemoryRuntimeStateStore()
    _bound_rail(store)
    service = GrowthBalanceSettlementService(store, settings=_issuing_settings())
    authorization = stripe.issuing.Authorization.construct_from(
        {
            "id": "iauth_live_1",
            "card": {"id": "ic_partizan_project"},
            "currency": "usd",
            "pending_request": {"amount": 2_500, "currency": "usd"},
            "merchant_data": {"category": ADVERTISING_MERCHANT_CATEGORY},
        },
        "sk_test_not_real",
    )

    # No ACTIVE subscription or mandate is stored, so the decision must be a clean decline
    # rather than an AttributeError escaping the webhook.
    assert service.authorize_request(authorization) is False


def test_issuing_transaction_capture_reads_a_real_transaction() -> None:
    store = MemoryRuntimeStateStore()
    _bound_rail(store)
    service = GrowthBalanceSettlementService(store, settings=_issuing_settings())
    transaction = stripe.issuing.Transaction.construct_from(
        {
            "id": "ipi_live_1",
            "card": {"id": "ic_partizan_project"},
            "currency": "usd",
            "type": "capture",
            "amount": -2_500,
            "merchant_data": {"category": ADVERTISING_MERCHANT_CATEGORY, "name": "Meta Ads"},
            "authorization": {"id": "iauth_live_1"},
        },
        "sk_test_not_real",
    )

    assert service.record_transaction(transaction) is True
    assert service.settled_spend_cents(PROJECT_ID) == 2_500


def test_billing_code_never_treats_stripe_payloads_as_mappings() -> None:
    """Guard the whole class of bug, not just the sites fixed once."""

    # Only the locals that hold a Stripe payload. Partizan's own runtime-store records
    # (rail, project, reservation, pending top-up) are ordinary dicts and stay mappings.
    stripe_payloads = {
        "app/customer_routes.py": ("session", "subscription", "event", "obj", "metadata"),
        "app/growth_balance.py": (
            "cardholder",
            "requirements",
            "authorization",
            "transaction",
            "merchant_data",
            "balance",
            "issuing",
            "card",
        ),
        "app/growth_balance_rail_routes.py": ("event",),
        "app/customer_billing.py": ("session", "subscription", "event"),
    }
    for module, names in stripe_payloads.items():
        source = (ROOT / module).read_text(encoding="utf-8")
        for name in names:
            assert f"{name}.get(" not in source, f"{module} calls .get() on Stripe payload {name}"
