from __future__ import annotations

from uuid import UUID

import pytest

from app.config import Settings
from app.growth_balance import (
    ADVERTISING_MERCHANT_CATEGORY,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GROWTH_BALANCE_TRANSACTION_NAMESPACE,
    GrowthBalanceSettlementService,
)
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        growth_balance_settlement_provider="stripe_issuing",
        stripe_secret_key="sk_test_partizan",
        stripe_issuing_cardholder_id="ich_partizan",
        stripe_issuing_currency="usd",
    )


class FakeSettlement(GrowthBalanceSettlementService):
    def __init__(self, store: MemoryRuntimeStateStore) -> None:
        super().__init__(store, settings=_settings())
        self.modified: list[tuple[str, dict]] = []

    def _retrieve_cardholder(self, cardholder_id: str):
        return {
            "id": cardholder_id,
            "status": "active",
            "requirements": {"disabled_reason": None, "past_due": []},
        }

    def _retrieve_issuing_available_cents(self, currency: str) -> int:
        return 1_000_000

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
        "card_id": "ic_project",
        "card_last4": "4242",
        "card_status": "active",
        "currency": "usd",
        "allowed_categories": [ADVERTISING_MERCHANT_CATEGORY],
        "acquisition_limit_cents": 100_000,
        "binding_status": "BOUND",
        "bound_provider": "meta",
        "bound_provider_account_id": "act_123",
    }
    store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID), rail)
    return rail


def test_hard_financial_pause_is_not_ready_and_cannot_be_overwritten() -> None:
    store = MemoryRuntimeStateStore()
    service = FakeSettlement(store)
    _bound_rail(store)

    service.pause(PROJECT_ID, "UNEXPECTED_MERCHANT_CATEGORY")
    assert service.readiness(PROJECT_ID) == (
        False,
        "FINANCIAL_RECONCILIATION_REQUIRED:UNEXPECTED_MERCHANT_CATEGORY",
    )

    service.pause(PROJECT_ID, "FUNDING")
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["paused_reason"] == "UNEXPECTED_MERCHANT_CATEGORY"
    assert service.funding_readiness(
        PROJECT_ID,
        required_liquidity_cents=1,
    ) == (
        False,
        "FINANCIAL_RECONCILIATION_REQUIRED:UNEXPECTED_MERCHANT_CATEGORY",
    )


def test_hard_financial_pause_blocks_all_automatic_reactivation_paths() -> None:
    store = MemoryRuntimeStateStore()
    service = FakeSettlement(store)
    _bound_rail(store)
    service.pause(PROJECT_ID, "UNEXPECTED_TRANSACTION_CURRENCY")

    service.provision_or_update(PROJECT_ID, 125_000)
    assert service.modified[-1][1]["status"] == "inactive"

    with pytest.raises(ValueError, match="financial reconciliation"):
        service.activate(PROJECT_ID)
    with pytest.raises(ValueError, match="financial reconciliation"):
        service.confirm_meta_binding(PROJECT_ID, "act_123")

    view = service.rail_view(PROJECT_ID)
    assert view["financial_reconciliation_required"] is True
    assert view["paused_reason"] == "UNEXPECTED_TRANSACTION_CURRENCY"


def test_operator_resolution_keeps_card_inactive_until_later_customer_resume() -> None:
    store = MemoryRuntimeStateStore()
    service = FakeSettlement(store)
    _bound_rail(store)
    service.pause(PROJECT_ID, "UNEXPECTED_MERCHANT_CATEGORY")

    resolved = service.resolve_financial_pause(
        PROJECT_ID,
        expected_reason="UNEXPECTED_MERCHANT_CATEGORY",
    )

    assert resolved["card_status"] == "inactive"
    assert resolved["paused_reason"] is None
    assert resolved["financial_reconciliation_resolved_reason"] == (
        "UNEXPECTED_MERCHANT_CATEGORY"
    )
    assert service.readiness(PROJECT_ID) == (True, "READY")
    assert service.modified[-1][1]["status"] == "inactive"

    service.activate(PROJECT_ID)
    assert service.modified[-1][1]["status"] == "active"


def test_unexpected_currency_is_stored_as_non_spend_anomaly_and_acknowledged() -> None:
    store = MemoryRuntimeStateStore()
    service = FakeSettlement(store)
    _bound_rail(store)
    transaction = {
        "id": "ipi_bad_currency",
        "card": "ic_project",
        "amount": -2_500,
        "currency": "eur",
        "type": "capture",
        "merchant_data": {
            "category": ADVERTISING_MERCHANT_CATEGORY,
            "name": "META ADS",
        },
        "authorization": "iauth_bad_currency",
    }

    assert service.record_transaction(transaction) is True
    assert service.settled_spend_cents(PROJECT_ID) == 0
    stored = store.get(GROWTH_BALANCE_TRANSACTION_NAMESPACE, "ipi_bad_currency")
    assert stored is not None
    assert stored["spend_delta_cents"] == 0
    assert stored["safety_anomaly"] == "UNEXPECTED_TRANSACTION_CURRENCY"
    assert stored["requires_financial_reconciliation"] is True
    assert service.readiness(PROJECT_ID)[0] is False


def test_duplicate_anomaly_event_does_not_reopen_operator_resolved_pause() -> None:
    store = MemoryRuntimeStateStore()
    service = FakeSettlement(store)
    _bound_rail(store)
    transaction = {
        "id": "ipi_bad_currency",
        "card": "ic_project",
        "amount": -2_500,
        "currency": "eur",
        "type": "capture",
        "merchant_data": {
            "category": ADVERTISING_MERCHANT_CATEGORY,
            "name": "META ADS",
        },
    }

    assert service.record_transaction(transaction) is True
    service.resolve_financial_pause(
        PROJECT_ID,
        expected_reason="UNEXPECTED_TRANSACTION_CURRENCY",
    )
    modifications_after_resolution = len(service.modified)

    assert service.record_transaction(transaction) is True
    assert len(service.modified) == modifications_after_resolution
    assert service.readiness(PROJECT_ID) == (True, "READY")
