from uuid import UUID

from app.growth_balance import (
    GROWTH_BALANCE_LOCK_NAMESPACE,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GROWTH_BALANCE_TRANSACTION_NAMESPACE,
)
from app.growth_balance_authorization_reservations import (
    GrowthBalanceAuthorizationReservationService,
)
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("22222222-2222-2222-2222-222222222222")
CARD_ID = "ic_project"


class FakeBalanceService:
    def __init__(self, store: MemoryRuntimeStateStore) -> None:
        self._store = store
        self.authorize_calls: list[object] = []
        self.transaction_calls: list[object] = []

    def authorize_request(self, authorization: object) -> bool:
        self.authorize_calls.append(authorization)
        return True

    def record_issuing_transaction(self, transaction: object) -> bool:
        self.transaction_calls.append(transaction)
        transaction_id = str(transaction["id"])
        amount = int(transaction["amount"])
        self._store.put(
            GROWTH_BALANCE_TRANSACTION_NAMESPACE,
            transaction_id,
            {
                "transaction_id": transaction_id,
                "project_id": str(PROJECT_ID),
                "card_id": CARD_ID,
                "spend_delta_cents": -amount,
            },
        )
        return True


def _service(limit: int = 10_000):
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
            "acquisition_limit_cents": limit,
        },
    )
    balance = FakeBalanceService(store)
    return store, balance, GrowthBalanceAuthorizationReservationService(balance)


def _request(authorization_id: str, pending: int, *, already_authorized: int = 0) -> dict:
    return {
        "id": authorization_id,
        "card": CARD_ID,
        "amount": already_authorized,
        "currency": "usd",
        "pending_request": {"amount": pending, "currency": "usd"},
    }


def _authorization_event(
    authorization_id: str,
    *,
    status: str,
    amount: int,
    approved: bool = True,
    transactions: list[str] | None = None,
) -> dict:
    return {
        "id": authorization_id,
        "card": CARD_ID,
        "status": status,
        "approved": approved,
        "amount": amount,
        "currency": "usd",
        "transactions": list(transactions or []),
    }


def _transaction(transaction_id: str, authorization_id: str, amount: int = -7000) -> dict:
    return {
        "id": transaction_id,
        "card": CARD_ID,
        "authorization": authorization_id,
        "amount": amount,
    }


def _hold(store: MemoryRuntimeStateStore, authorization_id: str) -> dict | None:
    return store.get(
        GROWTH_BALANCE_LOCK_NAMESPACE,
        f"issuing_authorization_hold:{authorization_id}",
    )


def test_second_parallel_authorization_cannot_ignore_first_pending_hold() -> None:
    store, balance, service = _service(limit=10_000)

    assert service.authorize_request(_request("iauth_1", 7000)) is True
    assert service.authorize_request(_request("iauth_2", 7000)) is False

    assert len(balance.authorize_calls) == 2
    first = _hold(store, "iauth_1")
    assert first is not None
    assert first["amount_cents"] == 7000
    assert first["state"] == "HELD"
    assert _hold(store, "iauth_2") is None


def test_incremental_authorization_replaces_total_hold_and_duplicate_is_idempotent() -> None:
    store, _, service = _service(limit=10_000)

    assert service.authorize_request(_request("iauth_1", 7000)) is True
    incremental = _request("iauth_1", 2000, already_authorized=7000)
    assert service.authorize_request(incremental) is True
    assert service.authorize_request(incremental) is True

    hold = _hold(store, "iauth_1")
    assert hold is not None
    assert hold["amount_cents"] == 9000


def test_closed_authorization_waits_for_capture_ledger_before_releasing_hold() -> None:
    store, _, service = _service()
    assert service.authorize_request(_request("iauth_1", 7000)) is True

    assert service.record_authorization(
        _authorization_event(
            "iauth_1",
            status="closed",
            amount=7000,
            transactions=["itxn_1"],
        )
    ) is True
    awaiting = _hold(store, "iauth_1")
    assert awaiting is not None
    assert awaiting["state"] == "CLOSED_AWAITING_CAPTURE"
    assert awaiting["transaction_ids"] == ["itxn_1"]

    assert service.record_transaction(_transaction("itxn_1", "iauth_1")) is True
    assert _hold(store, "iauth_1") is None
    assert store.get(GROWTH_BALANCE_TRANSACTION_NAMESPACE, "itxn_1") is not None


def test_transaction_before_closed_event_does_not_open_capacity_early() -> None:
    store, _, service = _service()
    assert service.authorize_request(_request("iauth_1", 7000)) is True

    assert service.record_transaction(_transaction("itxn_1", "iauth_1")) is True
    held = _hold(store, "iauth_1")
    assert held is not None
    assert held["state"] == "HELD"

    assert service.record_authorization(
        _authorization_event(
            "iauth_1",
            status="closed",
            amount=7000,
            transactions=["itxn_1"],
        )
    ) is True
    assert _hold(store, "iauth_1") is None


def test_reversed_authorization_releases_hold_immediately() -> None:
    store, _, service = _service()
    assert service.authorize_request(_request("iauth_1", 7000)) is True

    assert service.record_authorization(
        _authorization_event("iauth_1", status="reversed", amount=0)
    ) is True
    assert _hold(store, "iauth_1") is None


def test_expired_authorization_stays_reserved_for_late_capture_reconciliation() -> None:
    store, _, service = _service()
    assert service.authorize_request(_request("iauth_1", 7000)) is True

    assert service.record_authorization(
        _authorization_event("iauth_1", status="expired", amount=7000)
    ) is True

    hold = _hold(store, "iauth_1")
    assert hold is not None
    assert hold["amount_cents"] == 7000
    assert hold["state"] == "EXPIRED_RECONCILIATION_REQUIRED"


def test_provider_approved_pending_event_creates_hold_even_without_request_webhook_state() -> None:
    store, _, service = _service()

    assert service.record_authorization(
        _authorization_event("iauth_fallback", status="pending", amount=6000)
    ) is True

    hold = _hold(store, "iauth_fallback")
    assert hold is not None
    assert hold["amount_cents"] == 6000
    assert hold["state"] == "HELD"


def test_closed_declined_authorization_clears_any_stale_hold() -> None:
    store, _, service = _service()
    assert service.authorize_request(_request("iauth_1", 3000)) is True

    assert service.record_authorization(
        _authorization_event(
            "iauth_1",
            status="closed",
            amount=0,
            approved=False,
        )
    ) is True
    assert _hold(store, "iauth_1") is None


def test_route_uses_reservation_service_for_request_authorization_and_lifecycle_events() -> None:
    source = open("app/growth_balance_rail_routes.py", encoding="utf-8").read()

    assert "growth_balance_authorization_reservation_service.authorize_request" in source
    assert '"issuing_authorization.created"' in source
    assert '"issuing_authorization.updated"' in source
    assert "growth_balance_authorization_reservation_service.record_authorization" in source
    assert "growth_balance_authorization_reservation_service.record_transaction" in source
