from uuid import UUID

import pytest

from app.growth_balance import GROWTH_BALANCE_LOCK_NAMESPACE, GROWTH_BALANCE_RAIL_NAMESPACE
from app.growth_balance_authorization_reservations import (
    GrowthBalanceAuthorizationReservationService,
)
from app.growth_balance_issuing_fallback import GrowthBalanceIssuingFallbackService
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("44444444-4444-4444-4444-444444444444")
CARD_ID = "ic_fallback_project"


class FakeBalanceService:
    def __init__(self, store: MemoryRuntimeStateStore) -> None:
        self._store = store
        self.pause_calls: list[tuple[UUID, str]] = []
        self.fail_next_pause = False

    def authorize_request(self, authorization: object) -> bool:
        del authorization
        return True

    def record_issuing_transaction(self, transaction: object) -> bool:
        del transaction
        return True

    def pause_rail(self, project_id: UUID, reason: str) -> None:
        self.pause_calls.append((project_id, reason))
        if self.fail_next_pause:
            self.fail_next_pause = False
            raise RuntimeError("provider pause unavailable")
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        if rail is not None:
            rail["card_status"] = "inactive"
            rail["paused_reason"] = reason
            self._store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), rail)


def _service():
    store = MemoryRuntimeStateStore()
    store.put(
        GROWTH_BALANCE_RAIL_NAMESPACE,
        str(PROJECT_ID),
        {
            "project_id": str(PROJECT_ID),
            "card_id": CARD_ID,
            "card_status": "active",
            "binding_status": "BOUND",
            "currency": "usd",
            "acquisition_limit_cents": 10_000,
        },
    )
    balance = FakeBalanceService(store)
    reservations = GrowthBalanceAuthorizationReservationService(balance)
    service = GrowthBalanceIssuingFallbackService(
        reservation_service=reservations,
        balance_service=balance,
    )
    return store, balance, service


def _authorization(
    *,
    authorization_id: str = "iauth_fallback",
    approved: bool = True,
    status: str = "pending",
    amount: int = 6_000,
    reason: str | None = "webhook_timeout",
) -> dict:
    history = []
    if reason is not None:
        history.append(
            {
                "reason": reason,
                "reason_message": "Partizan authorization response was unavailable",
            }
        )
    return {
        "id": authorization_id,
        "card": CARD_ID,
        "approved": approved,
        "status": status,
        "amount": amount,
        "currency": "usd",
        "transactions": [],
        "request_history": history,
    }


def _incident(store: MemoryRuntimeStateStore, authorization_id: str = "iauth_fallback"):
    return store.get(
        GROWTH_BALANCE_LOCK_NAMESPACE,
        f"issuing_authorization_fallback:{authorization_id}",
    )


def _hold(store: MemoryRuntimeStateStore, authorization_id: str = "iauth_fallback"):
    return store.get(
        GROWTH_BALANCE_LOCK_NAMESPACE,
        f"issuing_authorization_hold:{authorization_id}",
    )


@pytest.mark.parametrize("reason", ["webhook_timeout", "webhook_error", "network_fallback"])
def test_approved_fallback_reserves_liability_and_pauses_rail(reason: str) -> None:
    store, balance, service = _service()

    assert service.record_authorization(_authorization(reason=reason)) is True

    hold = _hold(store)
    assert hold is not None
    assert hold["state"] == "HELD"
    assert hold["amount_cents"] == 6_000
    incident = _incident(store)
    assert incident is not None
    assert incident["fallback_reason"] == reason
    assert incident["approved"] is True
    assert incident["state"] == "RECONCILIATION_REQUIRED"
    assert incident["pause_confirmed_at"]
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["card_status"] == "inactive"
    assert rail["issuing_fallback_reconciliation_required"] is True
    assert len(balance.pause_calls) == 1


def test_declined_fallback_still_pauses_unhealthy_authorization_rail() -> None:
    store, balance, service = _service()

    assert service.record_authorization(
        _authorization(
            approved=False,
            status="closed",
            amount=0,
            reason="webhook_error",
        )
    ) is True

    assert _hold(store) is None
    incident = _incident(store)
    assert incident is not None
    assert incident["approved"] is False
    assert incident["fallback_reason"] == "webhook_error"
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["card_status"] == "inactive"
    assert len(balance.pause_calls) == 1


def test_duplicate_fallback_event_does_not_repeat_confirmed_provider_pause() -> None:
    store, balance, service = _service()
    authorization = _authorization(reason="webhook_timeout")

    assert service.record_authorization(authorization) is True
    assert service.record_authorization(authorization) is True

    assert len(balance.pause_calls) == 1
    assert _incident(store)["pause_confirmed_at"]
    assert service.unresolved_for_project(PROJECT_ID) is True


def test_pause_failure_keeps_local_rail_locked_and_retry_finishes_pause() -> None:
    store, balance, service = _service()
    balance.fail_next_pause = True
    authorization = _authorization(reason="network_fallback")

    with pytest.raises(RuntimeError, match="provider pause unavailable"):
        service.record_authorization(authorization)

    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["card_status"] == "inactive"
    assert rail["issuing_fallback_reconciliation_required"] is True
    incident = _incident(store)
    assert incident is not None
    assert incident["pause_confirmed_at"] is None
    assert _hold(store) is not None

    assert service.record_authorization(authorization) is True
    assert len(balance.pause_calls) == 2
    assert _incident(store)["pause_confirmed_at"]


def test_healthy_event_does_not_create_or_clear_fallback_incident() -> None:
    store, balance, service = _service()

    assert service.record_authorization(_authorization(reason=None)) is True
    assert _incident(store) is None
    assert balance.pause_calls == []

    assert service.record_authorization(_authorization(reason="webhook_timeout")) is True
    incident = _incident(store)
    assert incident is not None
    confirmed_at = incident["pause_confirmed_at"]

    assert service.record_authorization(_authorization(reason=None)) is True
    later = _incident(store)
    assert later is not None
    assert later["state"] == "RECONCILIATION_REQUIRED"
    assert later["pause_confirmed_at"] == confirmed_at
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["card_status"] == "inactive"
    assert len(balance.pause_calls) == 1


def test_unrelated_request_history_reason_does_not_pause() -> None:
    store, balance, service = _service()

    assert service.record_authorization(_authorization(reason="webhook_approved")) is True

    assert _incident(store) is None
    assert _hold(store) is not None
    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(PROJECT_ID))
    assert rail is not None
    assert rail["card_status"] == "active"
    assert balance.pause_calls == []


def test_route_sends_authorization_lifecycle_through_fallback_service() -> None:
    source = open("app/growth_balance_rail_routes.py", encoding="utf-8").read()

    assert "growth_balance_issuing_fallback_service.record_authorization" in source
    assert "growth_balance_authorization_reservation_service.record_transaction" in source
