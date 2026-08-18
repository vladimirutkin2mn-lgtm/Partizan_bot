from types import SimpleNamespace
from uuid import UUID

import stripe

from app.autonomy_schemas import GrowthMandateStatus
from app.config import Settings
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.growth_balance import (
    ADVERTISING_MERCHANT_CATEGORY,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GrowthBalanceSettlementService,
)
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("22222222-2222-2222-2222-222222222222")
PRODUCT_ID = UUID("33333333-3333-3333-3333-333333333333")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        growth_balance_settlement_provider="stripe_issuing",
        stripe_secret_key="sk_test_partizan",
        stripe_issuing_cardholder_id="ich_partizan",
        stripe_issuing_currency="usd",
    )


class FakeIssuingSettlement(GrowthBalanceSettlementService):
    def __init__(self, store: MemoryRuntimeStateStore, *, available_cents: int = 1_000_000) -> None:
        super().__init__(store, settings=_settings())
        self.available_cents = available_cents
        self.created: list[dict] = []
        self.modified: list[tuple[str, dict]] = []

    def _retrieve_cardholder(self, cardholder_id: str):
        assert cardholder_id == "ich_partizan"
        return {
            "id": cardholder_id,
            "status": "active",
            "requirements": {"disabled_reason": None, "past_due": []},
        }

    def _retrieve_issuing_available_cents(self, currency: str) -> int:
        assert currency == "usd"
        return self.available_cents

    def _create_card(self, **kwargs):
        self.created.append(kwargs)
        return {
            "id": "ic_partizan_project",
            "last4": "4242",
            "status": kwargs["status"],
        }

    def _modify_card(self, card_id: str, **kwargs):
        self.modified.append((card_id, kwargs))
        return {
            "id": card_id,
            "last4": "4242",
            "status": kwargs.get("status", "inactive"),
        }


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


def test_pinned_stripe_sdk_exposes_required_issuing_resources() -> None:
    assert hasattr(stripe, "issuing")
    assert hasattr(stripe.issuing, "Card")
    assert hasattr(stripe.issuing.Card, "create")
    assert hasattr(stripe.issuing.Card, "modify")
    assert hasattr(stripe.issuing, "Cardholder")
    assert hasattr(stripe.issuing.Cardholder, "retrieve")
    assert hasattr(stripe, "Balance")
    assert hasattr(stripe.Balance, "retrieve")


def test_funding_readiness_requires_prefunded_issuing_liquidity() -> None:
    store = MemoryRuntimeStateStore()
    insufficient = FakeIssuingSettlement(store, available_cents=50_000)
    enough = FakeIssuingSettlement(store, available_cents=100_000)

    assert insufficient.funding_readiness(
        PROJECT_ID,
        required_liquidity_cents=90_909,
    ) == (False, "STRIPE_ISSUING_LIQUIDITY_INSUFFICIENT")
    assert enough.funding_readiness(
        PROJECT_ID,
        required_liquidity_cents=90_909,
    ) == (True, "READY_FOR_FUNDING")


def test_project_card_is_virtual_inactive_advertising_only_and_non_sensitive() -> None:
    store = MemoryRuntimeStateStore()
    service = FakeIssuingSettlement(store)

    rail = service.provision_or_update(PROJECT_ID, 90_909)

    request = service.created[0]
    assert request["type"] == "virtual"
    assert request["status"] == "inactive"
    assert request["currency"] == "usd"
    assert request["spending_controls"] == {
        "allowed_categories": [ADVERTISING_MERCHANT_CATEGORY],
        "spending_limits": [{"amount": 90_909, "interval": "all_time"}],
    }
    assert request["metadata"]["partizan_project_id"] == str(PROJECT_ID)
    assert rail["card_id"] == "ic_partizan_project"
    assert rail["card_last4"] == "4242"
    assert rail["binding_status"] == "UNBOUND"
    stored = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert stored is not None
    assert "number" not in stored
    assert "cvc" not in stored
    assert "exp_month" not in stored
    assert "exp_year" not in stored


def test_capture_and_refund_transactions_drive_settled_growth_spend() -> None:
    store = MemoryRuntimeStateStore()
    service = FakeIssuingSettlement(store)
    _bound_rail(store)

    assert service.record_transaction(
        {
            "id": "ipi_capture",
            "card": "ic_partizan_project",
            "amount": -60_000,
            "currency": "usd",
            "type": "capture",
            "merchant_data": {
                "category": ADVERTISING_MERCHANT_CATEGORY,
                "name": "META ADS",
            },
            "authorization": "iauth_1",
        }
    )
    assert service.settled_spend_cents(PROJECT_ID) == 60_000

    assert service.record_transaction(
        {
            "id": "ipi_refund",
            "card": "ic_partizan_project",
            "amount": 10_000,
            "currency": "usd",
            "type": "refund",
            "merchant_data": {
                "category": ADVERTISING_MERCHANT_CATEGORY,
                "name": "META ADS",
            },
            "authorization": "iauth_1",
        }
    )
    assert service.settled_spend_cents(PROJECT_ID) == 50_000


def test_real_time_authorization_requires_active_project_and_remaining_ad_capacity(monkeypatch) -> None:
    import app.autonomy_service as autonomy_service

    store = MemoryRuntimeStateStore()
    service = FakeIssuingSettlement(store)
    _bound_rail(store)
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(PROJECT_ID),
        {
            "id": str(PROJECT_ID),
            "product_id": str(PRODUCT_ID),
            "autopilot_subscription_status": "ACTIVE",
        },
    )
    monkeypatch.setattr(
        autonomy_service.growth_mandate_service,
        "get",
        lambda product_id: SimpleNamespace(status=GrowthMandateStatus.ACTIVE),
    )
    request = {
        "card": "ic_partizan_project",
        "pending_request": {"amount": 30_000, "currency": "usd"},
        "merchant_data": {"category": ADVERTISING_MERCHANT_CATEGORY},
    }

    assert service.authorize_request(request) is True
    assert service.authorize_request(
        {**request, "merchant_data": {"category": "restaurants"}}
    ) is False
    assert service.authorize_request(
        {**request, "pending_request": {"amount": 100_000, "currency": "usd"}}
    ) is False

    service.record_transaction(
        {
            "id": "ipi_prior_capture",
            "card": "ic_partizan_project",
            "amount": -70_000,
            "currency": "usd",
            "type": "capture",
            "merchant_data": {
                "category": ADVERTISING_MERCHANT_CATEGORY,
                "name": "META ADS",
            },
        }
    )
    assert service.authorize_request(request) is False


def test_unexpected_merchant_capture_immediately_pauses_partizan_card() -> None:
    store = MemoryRuntimeStateStore()
    service = FakeIssuingSettlement(store)
    _bound_rail(store)

    assert service.record_transaction(
        {
            "id": "ipi_bad_capture",
            "card": "ic_partizan_project",
            "amount": -1_000,
            "currency": "usd",
            "type": "capture",
            "merchant_data": {"category": "restaurants", "name": "NOT ADS"},
        }
    )

    assert service.modified[-1][0] == "ic_partizan_project"
    assert service.modified[-1][1]["status"] == "inactive"
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["paused_reason"] == "UNEXPECTED_MERCHANT_CATEGORY"
