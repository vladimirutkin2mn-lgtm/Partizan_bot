from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr

import app.growth_balance as growth_balance_module
from app.config import Settings
from app.customer_funnel import (
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerFunnelService,
)
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import (
    GROWTH_BALANCE_LOCK_NAMESPACE,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GROWTH_BALANCE_TOPUP_NAMESPACE,
    GrowthBalanceService,
)
from app.growth_balance_funding_policy import (
    CheckoutFirstGrowthBalanceSettlementService,
    _install_checkout_first_liquidity_policy,
)
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("22222222-2222-2222-2222-222222222222")
ISSUING_LIQUIDITY_LOCK_KEY = "stripe_issuing_liquidity"


def checkout_only_settlement(store: MemoryRuntimeStateStore):
    return CheckoutFirstGrowthBalanceSettlementService(
        store,
        settings=Settings(
            _env_file=None,
            stripe_secret_key=SecretStr("sk_test_checkout_only"),
            growth_balance_settlement_provider="unavailable",
        ),
    )


def _preview(funnel: CustomerFunnelService):
    return funnel.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
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


def test_checkout_only_prepare_checkout_ignores_busy_issuing_liquidity_lock(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    funnel = CustomerFunnelService(store)
    preview = _preview(funnel)
    settlement = checkout_only_settlement(store)
    service = GrowthBalanceService(store, settlement_service=settlement)
    _install_checkout_first_liquidity_policy(service)
    monkeypatch.setattr(growth_balance_module, "customer_funnel_service", funnel)
    store.put(
        GROWTH_BALANCE_LOCK_NAMESPACE,
        ISSUING_LIQUIDITY_LOCK_KEY,
        {"token": "busy-issuing-allocation", "created_at": datetime.now(UTC).isoformat()},
    )

    generation, stripe_customer_id, amount_cents = service.prepare_checkout(
        preview.project_id,
        preview.customer_token,
        1000,
    )

    assert generation == 1
    assert stripe_customer_id is None
    assert amount_cents == 100_000
    reservation = store.get(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        f"reservation:{preview.project_id}:1",
    )
    assert reservation is not None
    assert reservation["state"] == "RESERVED"
    assert store.get(GROWTH_BALANCE_LOCK_NAMESPACE, ISSUING_LIQUIDITY_LOCK_KEY) is not None


def test_issuing_mode_still_respects_busy_global_liquidity_lock(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    funnel = CustomerFunnelService(store)
    preview = _preview(funnel)
    settlement = CheckoutFirstGrowthBalanceSettlementService(
        store,
        settings=Settings(
            _env_file=None,
            stripe_secret_key=SecretStr("sk_test_issuing"),
            growth_balance_settlement_provider="stripe_issuing",
            stripe_issuing_cardholder_id="ich_test_partizan",
        ),
    )
    service = GrowthBalanceService(store, settlement_service=settlement)
    _install_checkout_first_liquidity_policy(service)
    monkeypatch.setattr(growth_balance_module, "customer_funnel_service", funnel)
    store.put(
        GROWTH_BALANCE_LOCK_NAMESPACE,
        ISSUING_LIQUIDITY_LOCK_KEY,
        {"token": "busy-issuing-allocation", "created_at": datetime.now(UTC).isoformat()},
    )

    with pytest.raises(ValueError, match="Growth Balance liquidity allocation is busy"):
        service.prepare_checkout(preview.project_id, preview.customer_token, 1000)


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
