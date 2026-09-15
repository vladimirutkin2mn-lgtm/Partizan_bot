from uuid import UUID

import pytest

from app.config import Settings
from app.growth_balance import (
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GROWTH_BALANCE_TOPUP_NAMESPACE,
    GrowthBalanceService,
)
from app.growth_balance_payment_reversals import (
    GROWTH_BALANCE_PAYMENT_REVERSAL_NAMESPACE,
    GrowthBalancePaymentReversalService,
)
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("33333333-3333-3333-3333-333333333333")


class PauseSettlement:
    def __init__(self, store: MemoryRuntimeStateStore) -> None:
        self.store = store
        self.pauses: list[tuple[UUID, str]] = []

    def pause(self, project_id: UUID, reason: str) -> None:
        self.pauses.append((project_id, reason))
        rail = self.store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        if rail is not None:
            rail["card_status"] = "inactive"
            rail["paused_reason"] = reason
            self.store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), rail)

    def readiness(self, project_id: UUID) -> tuple[bool, str]:
        del project_id
        return True, "READY"


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, partizan_managed_spend_fee_pct=10)


def _service(*, amount_cents: int = 10_000, generation: int = 7):
    store = MemoryRuntimeStateStore()
    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        "cs_paid_topup",
        {
            "session_id": "cs_paid_topup",
            "project_id": str(PROJECT_ID),
            "checkout_generation": generation,
            "amount_cents": amount_cents,
            "currency": "usd",
            "state": "PAID",
        },
    )
    store.put(
        GROWTH_BALANCE_RAIL_NAMESPACE,
        str(PROJECT_ID),
        {
            "project_id": str(PROJECT_ID),
            "card_id": "ic_test",
            "card_status": "active",
            "binding_status": "BOUND",
            "acquisition_limit_cents": GrowthBalanceService._max_acquisition_cents(
                amount_cents, 10
            ),
        },
    )
    settlement = PauseSettlement(store)
    balance = GrowthBalanceService(store, settlement_service=settlement)
    reversals = GrowthBalancePaymentReversalService(balance)
    return store, settlement, balance, reversals


def _charge(*, amount_cents: int = 10_000, refunded_cents: int = 0, generation: int = 7):
    return {
        "id": "ch_growth_balance",
        "amount": amount_cents,
        "amount_refunded": refunded_cents,
        "currency": "usd",
        "metadata": {
            "partizan_project_id": str(PROJECT_ID),
            "partizan_entitlement": "growth_balance_topup",
            "partizan_amount_cents": str(amount_cents),
            "partizan_checkout_generation": str(generation),
        },
    }


def _dispute(*, amount_cents: int = 3_000, status: str = "needs_response"):
    return {
        "id": "dp_growth_balance",
        "charge": "ch_growth_balance",
        "amount": amount_cents,
        "currency": "usd",
        "status": status,
    }


def test_partial_refund_reduces_funded_capacity_and_pauses_once(settings: Settings) -> None:
    store, settlement, balance, reversals = _service()
    charge = _charge(refunded_cents=2_500)

    assert reversals.record_charge_refund(
        charge,
        event_id="evt_refund_1",
        event_created=100,
        settings=settings,
    )
    assert balance._funded_cents(PROJECT_ID) == 7_500
    assert len(settlement.pauses) == 1
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["card_status"] == "inactive"
    assert rail["acquisition_limit_cents"] == GrowthBalanceService._max_acquisition_cents(
        7_500, 10
    )

    assert reversals.record_charge_refund(
        charge,
        event_id="evt_refund_1",
        event_created=100,
        settings=settings,
    )
    assert balance._funded_cents(PROJECT_ID) == 7_500
    assert len(settlement.pauses) == 1


def test_larger_cumulative_refund_reduces_balance_again_and_repauses(settings: Settings) -> None:
    _, settlement, balance, reversals = _service()
    first = _charge(refunded_cents=2_500)
    larger = _charge(refunded_cents=4_000)

    reversals.record_charge_refund(
        first,
        event_id="evt_refund_1",
        event_created=100,
        settings=settings,
    )
    reversals.record_charge_refund(
        larger,
        event_id="evt_refund_2",
        event_created=110,
        settings=settings,
    )

    assert balance._funded_cents(PROJECT_ID) == 6_000
    assert len(settlement.pauses) == 2
    assert settlement.pauses[-1][1].endswith("_4000")


def test_refund_and_dispute_overlap_is_capped_at_original_topup(settings: Settings) -> None:
    store, _, balance, reversals = _service()
    reversals.record_charge_refund(
        _charge(refunded_cents=4_000),
        event_id="evt_refund",
        event_created=100,
        settings=settings,
    )
    reversals.record_dispute(
        _dispute(amount_cents=8_000),
        event_type="charge.dispute.created",
        charge=_charge(),
        event_id="evt_dispute",
        event_created=110,
        settings=settings,
    )

    assert balance._funded_cents(PROJECT_ID) == 0
    adjustment = store.get(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        f"payment_reversal_adjustment:{PROJECT_ID}:7",
    )
    assert adjustment is not None
    assert adjustment["amount_cents"] == -10_000


def test_dispute_reinstatement_restores_capacity_without_reactivating_rail(
    settings: Settings,
) -> None:
    store, settlement, balance, reversals = _service()
    dispute = _dispute(amount_cents=3_000)
    reversals.record_dispute(
        dispute,
        event_type="charge.dispute.created",
        charge=_charge(),
        event_id="evt_dispute_created",
        event_created=100,
        settings=settings,
    )
    assert balance._funded_cents(PROJECT_ID) == 7_000
    assert len(settlement.pauses) == 1

    reinstated = {**dispute, "status": "won"}
    reversals.record_dispute(
        reinstated,
        event_type="charge.dispute.funds_reinstated",
        charge=_charge(),
        event_id="evt_dispute_reinstated",
        event_created=200,
        settings=settings,
    )

    assert balance._funded_cents(PROJECT_ID) == 10_000
    assert len(settlement.pauses) == 1
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["card_status"] == "inactive"
    assert rail["acquisition_limit_cents"] == GrowthBalanceService._max_acquisition_cents(
        10_000, 10
    )


def test_older_dispute_event_cannot_reopen_reinstated_liability(settings: Settings) -> None:
    _, settlement, balance, reversals = _service()
    dispute = _dispute(amount_cents=3_000)
    reversals.record_dispute(
        dispute,
        event_type="charge.dispute.funds_reinstated",
        charge=_charge(),
        event_id="evt_reinstated",
        event_created=200,
        settings=settings,
    )
    reversals.record_dispute(
        dispute,
        event_type="charge.dispute.created",
        charge=_charge(),
        event_id="evt_created_old",
        event_created=100,
        settings=settings,
    )

    assert balance._funded_cents(PROJECT_ID) == 10_000
    assert settlement.pauses == []


def test_relevant_refund_without_exact_paid_generation_fails_closed(settings: Settings) -> None:
    store = MemoryRuntimeStateStore()
    settlement = PauseSettlement(store)
    balance = GrowthBalanceService(store, settlement_service=settlement)
    reversals = GrowthBalancePaymentReversalService(balance)

    with pytest.raises(RuntimeError, match="exactly one paid Checkout generation"):
        reversals.record_charge_refund(
            _charge(refunded_cents=1_000),
            event_id="evt_missing_topup",
            event_created=100,
            settings=settings,
        )
    assert store.list_namespace(GROWTH_BALANCE_PAYMENT_REVERSAL_NAMESPACE) == []
    assert settlement.pauses == []


def test_non_growth_balance_charge_is_ignored(settings: Settings) -> None:
    _, settlement, balance, reversals = _service()
    charge = _charge(refunded_cents=1_000)
    charge["metadata"]["partizan_entitlement"] = "launch_plan"

    assert not reversals.record_charge_refund(
        charge,
        event_id="evt_launch_refund",
        event_created=100,
        settings=settings,
    )
    assert balance._funded_cents(PROJECT_ID) == 10_000
    assert settlement.pauses == []
