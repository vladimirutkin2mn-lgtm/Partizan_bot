from uuid import UUID

from pydantic import SecretStr

from app.config import Settings
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.growth_balance import (
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GROWTH_BALANCE_TOPUP_NAMESPACE,
    GrowthBalanceService,
)
from app.growth_balance_funding_policy import CheckoutFirstGrowthBalanceSettlementService
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("22222222-2222-2222-2222-222222222222")


def checkout_only_settlement(store: MemoryRuntimeStateStore):
    return CheckoutFirstGrowthBalanceSettlementService(
        store,
        settings=Settings(
            stripe_secret_key=SecretStr("sk_test_checkout_only"),
            growth_balance_settlement_provider="unavailable",
        ),
    )


def test_stripe_checkout_can_fund_before_issuing_while_spend_stays_blocked() -> None:
    store = MemoryRuntimeStateStore()
    settlement = checkout_only_settlement(store)

    funding_ready, funding_status = settlement.funding_readiness(
        PROJECT_ID,
        required_liquidity_cents=90_909,
    )
    spend_ready, spend_status = settlement.readiness(PROJECT_ID)

    assert funding_ready is True
    assert funding_status == "STRIPE_CHECKOUT_READY_SPEND_RAIL_DEFERRED"
    assert spend_ready is False
    assert spend_status == "PARTIZAN_FUNDED_PAYMENT_RAIL_NOT_CONFIGURED"

    staged = settlement.provision_or_update(PROJECT_ID, 90_909)
    assert staged["provider"] == "checkout_only"
    assert staged["settlement_ready"] is False
    assert staged["settlement_status"] == "SPEND_RAIL_DEFERRED"
    assert store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID)) is None


def test_paid_checkout_credits_balance_without_creating_fake_spend_rail() -> None:
    store = MemoryRuntimeStateStore()
    settlement = checkout_only_settlement(store)
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(PROJECT_ID),
        {
            "id": str(PROJECT_ID),
            "status": "PREVIEW",
            "launch_unlocked": False,
        },
    )
    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        "cs_checkout_only_paid",
        {
            "session_id": "cs_checkout_only_paid",
            "project_id": str(PROJECT_ID),
            "checkout_generation": 1,
            "amount_cents": 100_000,
            "currency": "usd",
            "state": "PENDING",
        },
    )
    service = GrowthBalanceService(store, settlement_service=settlement)

    credited = service.credit_paid_checkout(
        PROJECT_ID,
        session_id="cs_checkout_only_paid",
        amount_cents=100_000,
        currency="usd",
        stripe_customer_id="cus_checkout_only",
    )
    summary = service.summary(PROJECT_ID, 0.0)

    assert credited is True
    assert summary.funded_usd == 1000.0
    assert summary.available_usd == 1000.0
    assert summary.settlement_ready is False
    assert summary.settlement_status == "PARTIZAN_FUNDED_PAYMENT_RAIL_NOT_CONFIGURED"
    assert store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID)) is None

    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(PROJECT_ID))
    assert project is not None
    assert project["launch_unlocked"] is True
    assert project["launch_entitlement_source"] == "GROWTH_BALANCE"
    assert project["stripe_customer_id"] == "cus_checkout_only"


def test_checkout_first_ui_does_not_intercept_growth_balance_submission() -> None:
    source = open("app/web/start.channels.v1.js", encoding="utf-8").read()

    assert "Fund securely with Stripe now" in source
    assert "event.stopImmediatePropagation" not in source
    assert "Paid acquisition will stay paused" in source
