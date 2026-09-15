from uuid import UUID

import app.growth_balance_paid_recovery as recovery_module
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.growth_balance import GROWTH_BALANCE_TOPUP_NAMESPACE, GrowthBalanceService
from app.growth_balance_paid_recovery import install_paid_checkout_project_recovery
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("22222222-2222-2222-2222-222222222222")


class ReadySettlement:
    def readiness(self, project_id: UUID) -> tuple[bool, str]:
        del project_id
        return True, "READY"

    def provision_or_update(self, project_id: UUID, acquisition_capacity_cents: int) -> dict:
        return {
            "project_id": str(project_id),
            "acquisition_limit_cents": acquisition_capacity_cents,
        }


def _reservation(generation: int) -> dict:
    return {
        "reservation_key": f"reservation:{PROJECT_ID}:{generation}",
        "project_id": str(PROJECT_ID),
        "checkout_generation": generation,
        "amount_cents": 100_000,
        "currency": "usd",
        "state": "RESERVED",
        "created_at": f"2026-09-14T12:0{generation}:00+00:00",
    }


def _paid_session(session_id: str, generation: int) -> dict:
    return {
        "id": session_id,
        "client_reference_id": str(PROJECT_ID),
        "mode": "payment",
        "payment_status": "paid",
        "amount_total": 100_000,
        "currency": "usd",
        "metadata": {
            "partizan_project_id": str(PROJECT_ID),
            "partizan_entitlement": "growth_balance_topup",
            "partizan_amount_cents": "100000",
            "partizan_checkout_generation": str(generation),
        },
    }


def _service_with_same_amount_reservations() -> tuple[MemoryRuntimeStateStore, GrowthBalanceService]:
    store = MemoryRuntimeStateStore()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(PROJECT_ID),
        {
            "id": str(PROJECT_ID),
            "status": "CHECKOUT_PENDING",
            "launch_unlocked": False,
        },
    )
    for generation in (1, 2):
        reservation = _reservation(generation)
        store.put(
            GROWTH_BALANCE_TOPUP_NAMESPACE,
            reservation["reservation_key"],
            reservation,
        )
    service = GrowthBalanceService(store, settlement_service=ReadySettlement())
    install_paid_checkout_project_recovery(service)
    return store, service


def test_lost_session_write_recovers_only_stripe_metadata_generation(monkeypatch) -> None:
    store, service = _service_with_same_amount_reservations()
    monkeypatch.setattr(
        recovery_module,
        "retrieve_launch_checkout",
        lambda **kwargs: _paid_session(str(kwargs["session_id"]), 1),
    )

    credited = service.credit_paid_checkout(
        PROJECT_ID,
        session_id="cs_delayed_generation_1",
        amount_cents=100_000,
        currency="usd",
        stripe_customer_id="cus_exact_generation",
    )

    assert credited is True
    recovered = store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, "cs_delayed_generation_1")
    assert recovered is not None
    assert recovered["state"] == "PAID"
    assert recovered["checkout_generation"] == 1
    assert recovered["recovery_binding"] == "STRIPE_CHECKOUT_GENERATION"

    generation_1 = store.get(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        f"reservation:{PROJECT_ID}:1",
    )
    generation_2 = store.get(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        f"reservation:{PROJECT_ID}:2",
    )
    assert generation_1 is not None
    assert generation_1["session_id"] == "cs_delayed_generation_1"
    assert generation_1["state"] == "CHECKOUT_CREATED"
    assert generation_2 is not None
    assert generation_2["state"] == "RESERVED"
    assert "session_id" not in generation_2


def test_missing_exact_generation_fails_closed_instead_of_guessing_newest(monkeypatch) -> None:
    store, service = _service_with_same_amount_reservations()
    monkeypatch.setattr(
        recovery_module,
        "retrieve_launch_checkout",
        lambda **kwargs: _paid_session(str(kwargs["session_id"]), 3),
    )

    credited = service.credit_paid_checkout(
        PROJECT_ID,
        session_id="cs_generation_without_reservation",
        amount_cents=100_000,
        currency="usd",
    )

    assert credited is False
    assert store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, "cs_generation_without_reservation") is None
    assert store.get(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        f"reservation:{PROJECT_ID}:1",
    )["state"] == "RESERVED"
    assert store.get(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        f"reservation:{PROJECT_ID}:2",
    )["state"] == "RESERVED"


def test_generation_recovery_rejects_reservation_already_bound_elsewhere(monkeypatch) -> None:
    store, service = _service_with_same_amount_reservations()
    reservation = store.get(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        f"reservation:{PROJECT_ID}:1",
    )
    assert reservation is not None
    reservation["state"] = "CHECKOUT_CREATED"
    reservation["session_id"] = "cs_other_session"
    store.put(GROWTH_BALANCE_TOPUP_NAMESPACE, reservation["reservation_key"], reservation)
    monkeypatch.setattr(
        recovery_module,
        "retrieve_launch_checkout",
        lambda **kwargs: _paid_session(str(kwargs["session_id"]), 1),
    )

    credited = service.credit_paid_checkout(
        PROJECT_ID,
        session_id="cs_conflicting_session",
        amount_cents=100_000,
        currency="usd",
    )

    assert credited is False
    assert store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, "cs_conflicting_session") is None
    assert store.get(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        f"reservation:{PROJECT_ID}:1",
    )["session_id"] == "cs_other_session"
