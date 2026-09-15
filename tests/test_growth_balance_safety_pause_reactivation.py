from pathlib import Path
from uuid import UUID

import pytest

from app.config import Settings
from app.growth_balance import (
    GROWTH_BALANCE_LOCK_NAMESPACE,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GrowthBalanceService,
)
from app.growth_balance_funding_policy import CheckoutFirstGrowthBalanceSettlementService
from app.growth_balance_rail_safety import GrowthBalanceRailSafetyService
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


class FakeSettlement(CheckoutFirstGrowthBalanceSettlementService):
    def __init__(self, store: MemoryRuntimeStateStore) -> None:
        super().__init__(store, settings=_settings())
        self.modified: list[tuple[str, dict]] = []

    def _modify_card(self, card_id: str, **kwargs):
        self.modified.append((card_id, kwargs))
        return {
            "id": card_id,
            "last4": "4242",
            "status": kwargs.get("status", "inactive"),
        }


def _rail(store: MemoryRuntimeStateStore, paused_reason: str) -> dict:
    payload = {
        "project_id": str(PROJECT_ID),
        "provider": "stripe_issuing",
        "card_id": "ic_project",
        "card_last4": "4242",
        "card_status": "inactive",
        "currency": "usd",
        "acquisition_limit_cents": 50_000,
        "binding_status": "BOUND",
        "bound_provider": "meta",
        "bound_provider_account_id": "act_123",
        "paused_reason": paused_reason,
    }
    store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID), payload)
    return payload


def _services(paused_reason: str):
    store = MemoryRuntimeStateStore()
    settlement = FakeSettlement(store)
    balance = GrowthBalanceService(store, settlement_service=settlement)
    _rail(store, paused_reason)
    return store, settlement, balance, GrowthBalanceRailSafetyService(balance)


@pytest.mark.parametrize("reason", ["CUSTOMER", "CHANNELS", "SETUP", "FUNDING"])
def test_normal_pause_reasons_remain_customer_or_system_resumeable(reason: str) -> None:
    store, settlement, balance, _safety = _services(reason)

    balance.activate_rail(PROJECT_ID)

    assert settlement.modified == [
        (
            "ic_project",
            {
                "status": "active",
                "spending_controls": {
                    "allowed_categories": ["advertising_services"],
                    "spending_limits": [{"amount": 50_000, "interval": "all_time"}],
                },
            },
        )
    ]
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["card_status"] == "active"
    assert rail["paused_reason"] is None


@pytest.mark.parametrize(
    "reason",
    [
        "STRIPE_GROWTH_BALANCE_REFUND_ch_1_1000",
        "STRIPE_GROWTH_BALANCE_DISPUTE_dp_1_1000",
        "ISSUING_FALLBACK_iauth_1_webhook_timeout",
        "UNEXPECTED_MERCHANT_CATEGORY",
    ],
)
def test_safety_pause_cannot_reactivate_through_normal_balance_api(reason: str) -> None:
    store, settlement, balance, _safety = _services(reason)

    with pytest.raises(ValueError, match="explicit operator reconciliation"):
        balance.activate_rail(PROJECT_ID)

    assert settlement.modified == []
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["card_status"] == "inactive"
    assert rail["paused_reason"] == reason


def test_operator_reactivation_requires_exact_current_pause_reason() -> None:
    reason = "STRIPE_GROWTH_BALANCE_REFUND_ch_1_1000"
    store, settlement, _balance, safety = _services(reason)

    with pytest.raises(ValueError, match="pause reason changed"):
        safety.reactivate_after_reconciliation(
            PROJECT_ID,
            expected_pause_reason="STRIPE_GROWTH_BALANCE_REFUND_ch_old_1000",
            confirm_reconciled=True,
            confirm_reactivation=True,
            reconciliation_note="Refund was reviewed and Growth Balance is safe to reopen.",
        )

    assert settlement.modified == []
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["paused_reason"] == reason


def test_operator_reactivation_requires_both_explicit_confirmations() -> None:
    reason = "STRIPE_GROWTH_BALANCE_REFUND_ch_1_1000"
    _store, settlement, _balance, safety = _services(reason)

    with pytest.raises(ValueError, match="confirm_reconciled"):
        safety.reactivate_after_reconciliation(
            PROJECT_ID,
            expected_pause_reason=reason,
            confirm_reconciled=False,
            confirm_reactivation=True,
            reconciliation_note="Reviewed.",
        )
    with pytest.raises(ValueError, match="confirm_reactivation"):
        safety.reactivate_after_reconciliation(
            PROJECT_ID,
            expected_pause_reason=reason,
            confirm_reconciled=True,
            confirm_reactivation=False,
            reconciliation_note="Reviewed.",
        )

    assert settlement.modified == []


def test_operator_can_release_exact_safety_pause_and_records_audit() -> None:
    reason = "STRIPE_GROWTH_BALANCE_REFUND_ch_1_1000"
    store, settlement, _balance, safety = _services(reason)

    result = safety.reactivate_after_reconciliation(
        PROJECT_ID,
        expected_pause_reason=reason,
        confirm_reconciled=True,
        confirm_reactivation=True,
        reconciliation_note="Refund liability and provider billing were reconciled by the operator.",
    )

    assert settlement.modified[-1][1]["status"] == "active"
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["card_status"] == "active"
    assert rail["paused_reason"] is None
    assert result["settlement_ready"] is True
    audit = [
        item
        for item in store.list_namespace(GROWTH_BALANCE_LOCK_NAMESPACE)
        if item.get("kind") == "GROWTH_BALANCE_RAIL_REACTIVATION"
    ]
    assert len(audit) == 1
    assert audit[0]["project_id"] == str(PROJECT_ID)
    assert audit[0]["previous_pause_reason"] == reason
    assert audit[0]["actor"] == "OPERATOR"


def test_operator_reactivation_resolves_matching_fallback_incident() -> None:
    reason = "ISSUING_FALLBACK_iauth_1_webhook_timeout"
    store, _settlement, _balance, safety = _services(reason)
    store.put(
        GROWTH_BALANCE_LOCK_NAMESPACE,
        "issuing_authorization_fallback:iauth_1",
        {
            "kind": "ISSUING_AUTHORIZATION_FALLBACK",
            "authorization_id": "iauth_1",
            "project_id": str(PROJECT_ID),
            "pause_reason": reason,
            "state": "RECONCILIATION_REQUIRED",
        },
    )

    safety.reactivate_after_reconciliation(
        PROJECT_ID,
        expected_pause_reason=reason,
        confirm_reconciled=True,
        confirm_reactivation=True,
        reconciliation_note="Stripe fallback authorization state was reconciled against provider records.",
    )

    incident = store.get(
        GROWTH_BALANCE_LOCK_NAMESPACE,
        "issuing_authorization_fallback:iauth_1",
    )
    assert incident is not None
    assert incident["state"] == "RESOLVED_BY_OPERATOR"
    assert incident["resolved_at"]
    assert "provider records" in incident["resolution_note"]


def test_operator_reactivation_route_is_explicitly_operator_protected() -> None:
    source = Path("app/growth_balance_rail_ops_routes.py").read_text()

    assert "dependencies=[Depends(require_operator)]" in source
    assert "/ops/customer-projects/{project_id}/growth-balance/rail/reactivate" in source
    assert "expected_pause_reason" in source
    assert "confirm_reconciled" in source
    assert "confirm_reactivation" in source
