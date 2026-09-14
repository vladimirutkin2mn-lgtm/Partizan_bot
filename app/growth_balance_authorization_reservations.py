from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID

from app.database_advisory_lock import postgres_session_advisory_lock
from app.growth_balance import (
    GROWTH_BALANCE_LOCK_NAMESPACE,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GROWTH_BALANCE_TRANSACTION_NAMESPACE,
    GrowthBalanceService,
    growth_balance_service,
)
from app.stripe_objects import stripe_field

_AUTHORIZATION_HOLD_PREFIX = "issuing_authorization_hold:"
_ACTIVE_HOLD_STATES = {
    "HELD",
    "CLOSED_AWAITING_CAPTURE",
    "EXPIRED_RECONCILIATION_REQUIRED",
}
_memory_lock = RLock()


class GrowthBalanceAuthorizationReservationService:
    """Serialize Issuing approvals against durable pending authorization holds.

    Stripe card spending controls remain an independent hard boundary, but their
    aggregation can lag. This service makes Partizan's own Growth Balance capacity
    decision account for approved-but-not-yet-captured authorizations as well as
    settled Issuing transactions.
    """

    def __init__(self, balance_service: GrowthBalanceService = growth_balance_service) -> None:
        self._balance = balance_service
        self._store = balance_service._store

    def authorize_request(self, authorization: object) -> bool:
        authorization_id = self._object_id(stripe_field(authorization, "id"))
        card_id = self._object_id(stripe_field(authorization, "card"))
        if not authorization_id or not card_id:
            return False
        rail = self._rail_for_card(card_id)
        if rail is None:
            return False
        project_id = UUID(str(rail["project_id"]))

        with self._project_lock(project_id) as acquired:
            if not acquired:
                return False
            # Re-run every existing policy check inside the same serialization scope.
            if not self._balance.authorize_request(authorization):
                return False

            pending = stripe_field(authorization, "pending_request")
            pending_amount = self._integer_field(pending, "amount")
            current_authorized = self._integer_field(authorization, "amount")
            if pending_amount <= 0 or current_authorized < 0:
                return False
            desired_hold = current_authorized + pending_amount
            limit = int(rail.get("acquisition_limit_cents") or 0)
            settled = self._settled_spend_cents(project_id)
            other_holds = self._held_cents(project_id, excluding=authorization_id)
            if desired_hold > max(limit - settled - other_holds, 0):
                return False

            self._persist_hold(
                authorization_id=authorization_id,
                project_id=project_id,
                card_id=card_id,
                amount_cents=desired_hold,
                currency=str(
                    stripe_field(pending, "currency")
                    or stripe_field(authorization, "currency", "")
                ).lower(),
                state="HELD",
            )
            return True

    def record_authorization(self, authorization: object) -> bool:
        authorization_id = self._object_id(stripe_field(authorization, "id"))
        card_id = self._object_id(stripe_field(authorization, "card"))
        if not authorization_id or not card_id:
            return False
        rail = self._rail_for_card(card_id)
        if rail is None:
            return False
        project_id = UUID(str(rail["project_id"]))

        with self._project_lock(project_id) as acquired:
            if not acquired:
                raise RuntimeError("Growth Balance authorization reconciliation is busy")

            status = str(stripe_field(authorization, "status", "")).lower()
            approved = stripe_field(authorization, "approved") is True
            amount_cents = max(self._integer_field(authorization, "amount"), 0)
            currency = str(stripe_field(authorization, "currency", "")).lower()

            if status == "pending" and approved:
                self._persist_hold(
                    authorization_id=authorization_id,
                    project_id=project_id,
                    card_id=card_id,
                    amount_cents=amount_cents,
                    currency=currency,
                    state="HELD",
                )
                return True

            if status == "reversed" or (status == "closed" and not approved):
                self._delete_hold(authorization_id)
                return True

            if status == "expired":
                # Stripe can still report late capture activity after expiry. Keep the
                # amount reserved until an operator/provider reconciliation proves the
                # remote liability is gone.
                existing = self._hold(authorization_id)
                held_amount = max(amount_cents, int((existing or {}).get("amount_cents") or 0))
                self._persist_hold(
                    authorization_id=authorization_id,
                    project_id=project_id,
                    card_id=card_id,
                    amount_cents=held_amount,
                    currency=currency or str((existing or {}).get("currency") or ""),
                    state="EXPIRED_RECONCILIATION_REQUIRED",
                )
                return True

            if status == "closed" and approved:
                transaction_ids = self._transaction_ids(authorization)
                if transaction_ids and self._transactions_recorded(transaction_ids):
                    self._delete_hold(authorization_id)
                    return True
                existing = self._hold(authorization_id)
                held_amount = max(amount_cents, int((existing or {}).get("amount_cents") or 0))
                self._persist_hold(
                    authorization_id=authorization_id,
                    project_id=project_id,
                    card_id=card_id,
                    amount_cents=held_amount,
                    currency=currency or str((existing or {}).get("currency") or ""),
                    state="CLOSED_AWAITING_CAPTURE",
                    transaction_ids=transaction_ids,
                )
                return True

            return False

    def record_transaction(self, transaction: object) -> bool:
        card_id = self._object_id(stripe_field(transaction, "card"))
        if not card_id:
            return self._balance.record_issuing_transaction(transaction)
        rail = self._rail_for_card(card_id)
        if rail is None:
            return self._balance.record_issuing_transaction(transaction)
        project_id = UUID(str(rail["project_id"]))

        with self._project_lock(project_id) as acquired:
            if not acquired:
                raise RuntimeError("Growth Balance transaction reconciliation is busy")
            recorded = self._balance.record_issuing_transaction(transaction)
            if not recorded:
                return False
            authorization_id = self._object_id(stripe_field(transaction, "authorization"))
            if authorization_id:
                self._release_if_fully_captured(authorization_id)
            return True

    def _release_if_fully_captured(self, authorization_id: str) -> None:
        hold = self._hold(authorization_id)
        if not hold or hold.get("state") != "CLOSED_AWAITING_CAPTURE":
            return
        transaction_ids = [
            str(value) for value in hold.get("transaction_ids") or [] if str(value).strip()
        ]
        if transaction_ids and self._transactions_recorded(transaction_ids):
            self._delete_hold(authorization_id)

    def _held_cents(self, project_id: UUID, *, excluding: str | None = None) -> int:
        return sum(
            int(item.get("amount_cents") or 0)
            for item in self._store.list_namespace(GROWTH_BALANCE_LOCK_NAMESPACE)
            if item.get("kind") == "ISSUING_AUTHORIZATION_HOLD"
            and str(item.get("project_id") or "") == str(project_id)
            and item.get("state") in _ACTIVE_HOLD_STATES
            and str(item.get("authorization_id") or "") != str(excluding or "")
        )

    def _settled_spend_cents(self, project_id: UUID) -> int:
        total = sum(
            int(item.get("spend_delta_cents") or 0)
            for item in self._store.list_namespace(GROWTH_BALANCE_TRANSACTION_NAMESPACE)
            if str(item.get("project_id") or "") == str(project_id)
        )
        return max(total, 0)

    def _rail_for_card(self, card_id: str) -> dict | None:
        for rail in self._store.list_namespace(GROWTH_BALANCE_RAIL_NAMESPACE):
            if str(rail.get("card_id") or "") == card_id:
                return rail
        return None

    def _persist_hold(
        self,
        *,
        authorization_id: str,
        project_id: UUID,
        card_id: str,
        amount_cents: int,
        currency: str,
        state: str,
        transaction_ids: list[str] | None = None,
    ) -> None:
        existing = self._hold(authorization_id) or {}
        now = datetime.now(UTC).isoformat()
        payload = {
            "kind": "ISSUING_AUTHORIZATION_HOLD",
            "authorization_id": authorization_id,
            "project_id": str(project_id),
            "card_id": card_id,
            "amount_cents": max(int(amount_cents), 0),
            "currency": currency,
            "state": state,
            "transaction_ids": list(transaction_ids or existing.get("transaction_ids") or []),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        self._store.put(GROWTH_BALANCE_LOCK_NAMESPACE, self._hold_key(authorization_id), payload)

    def _hold(self, authorization_id: str) -> dict | None:
        return self._store.get(GROWTH_BALANCE_LOCK_NAMESPACE, self._hold_key(authorization_id))

    def _delete_hold(self, authorization_id: str) -> None:
        self._store.delete(GROWTH_BALANCE_LOCK_NAMESPACE, self._hold_key(authorization_id))

    def _transactions_recorded(self, transaction_ids: list[str]) -> bool:
        return all(
            self._store.get(GROWTH_BALANCE_TRANSACTION_NAMESPACE, transaction_id) is not None
            for transaction_id in transaction_ids
        )

    @staticmethod
    def _hold_key(authorization_id: str) -> str:
        return f"{_AUTHORIZATION_HOLD_PREFIX}{authorization_id}"

    @staticmethod
    def _integer_field(value: object, field: str) -> int:
        try:
            return int(stripe_field(value, field, 0) or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _transaction_ids(cls, authorization: object) -> list[str]:
        values = stripe_field(authorization, "transactions") or []
        result: list[str] = []
        for value in values:
            identifier = cls._object_id(value)
            if identifier:
                result.append(identifier)
        return result

    @staticmethod
    def _object_id(value: object) -> str:
        if isinstance(value, str):
            return value.strip()
        return str(stripe_field(value, "id", "") or "").strip()

    @contextmanager
    def _project_lock(self, project_id: UUID) -> Iterator[bool]:
        if self._store.ephemeral:
            with _memory_lock:
                yield True
            return
        lock_key = int.from_bytes(
            hashlib.sha256(
                f"partizan:growth-balance-authorization:{project_id}".encode("utf-8")
            ).digest()[:8],
            byteorder="big",
            signed=True,
        )
        with postgres_session_advisory_lock(lock_key) as acquired:
            yield acquired


growth_balance_authorization_reservation_service = (
    GrowthBalanceAuthorizationReservationService()
)
