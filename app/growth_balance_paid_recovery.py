from __future__ import annotations

from datetime import UTC, datetime
from types import MethodType
from uuid import UUID

from app.config import get_settings
from app.customer_billing import retrieve_launch_checkout
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, CustomerProjectNotFoundError
from app.growth_balance import (
    GROWTH_BALANCE_TOPUP_NAMESPACE,
    GrowthBalanceService,
    growth_balance_service,
)
from app.stripe_objects import stripe_field


def install_paid_checkout_project_recovery(
    service: GrowthBalanceService = growth_balance_service,
) -> GrowthBalanceService:
    """Recover paid Growth Balance state from exact Stripe-bound checkout data."""

    if getattr(service, "_paid_checkout_project_recovery_installed", False):
        return service

    original_credit = service.credit_paid_checkout

    def credit(
        instance: GrowthBalanceService,
        project_id: UUID,
        *,
        session_id: str,
        amount_cents: int,
        currency: str,
        stripe_customer_id: str | None = None,
    ) -> bool:
        if instance._store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, session_id) is None:
            if not _recover_exact_paid_session(
                instance,
                project_id,
                session_id=session_id,
                amount_cents=amount_cents,
                currency=currency,
            ):
                return False

        credited = original_credit(
            project_id,
            session_id=session_id,
            amount_cents=amount_cents,
            currency=currency,
            stripe_customer_id=stripe_customer_id,
        )
        if not credited:
            return False

        record = instance._store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, session_id)
        if record is None or record.get("state") != "PAID":
            return True
        project = instance._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
        if project is None:
            raise CustomerProjectNotFoundError(project_id)

        changed = False
        if stripe_customer_id and not project.get("stripe_customer_id"):
            project["stripe_customer_id"] = stripe_customer_id
            changed = True

        paid_at = str(record.get("paid_at") or "").strip()
        current_funded_at = str(project.get("growth_balance_last_funded_at") or "").strip()
        if paid_at and (
            not current_funded_at or _timestamp_is_newer(paid_at, current_funded_at)
        ):
            project["growth_balance_last_funded_at"] = paid_at
            changed = True

        if not project.get("launch_unlocked"):
            project["launch_unlocked"] = True
            project["launch_entitlement_source"] = "GROWTH_BALANCE"
            project["launch_unlocked_at"] = paid_at or datetime.now(UTC).isoformat()
            if project.get("status") in {"PREVIEW", "CHECKOUT_PENDING"}:
                project["status"] = "UNLOCKED"
            changed = True

        if changed:
            instance._persist_project(project)
        return True

    service.credit_paid_checkout = MethodType(credit, service)
    service._paid_checkout_project_recovery_installed = True
    return service


def _recover_exact_paid_session(
    service: GrowthBalanceService,
    project_id: UUID,
    *,
    session_id: str,
    amount_cents: int,
    currency: str,
) -> bool:
    """Reconnect a lost session write to the exact reservation named by Stripe metadata.

    Never guess among same-amount reservations. The Checkout Session was created with
    a monotonic generation in Stripe metadata before the local session-id write. On a
    crash-gap replay, retrieve that provider object and bind only the matching durable
    reservation.
    """

    session = retrieve_launch_checkout(settings=get_settings(), session_id=session_id)
    metadata = stripe_field(session, "metadata")
    try:
        generation = int(stripe_field(metadata, "partizan_checkout_generation", 0))
        metadata_amount = int(stripe_field(metadata, "partizan_amount_cents", 0))
        session_amount = int(stripe_field(session, "amount_total", 0))
    except (TypeError, ValueError):
        return False
    normalized_currency = currency.lower()
    verified = (
        generation > 0
        and str(stripe_field(session, "id", "")) == session_id
        and str(stripe_field(session, "client_reference_id", "")) == str(project_id)
        and str(stripe_field(metadata, "partizan_project_id", "")) == str(project_id)
        and stripe_field(metadata, "partizan_entitlement") == "growth_balance_topup"
        and metadata_amount == int(amount_cents)
        and session_amount == int(amount_cents)
        and str(stripe_field(session, "currency", "")).lower() == normalized_currency
        and stripe_field(session, "mode") == "payment"
        and stripe_field(session, "payment_status") == "paid"
    )
    if not verified:
        return False

    reservation_key = f"reservation:{project_id}:{generation}"
    reservation = service._store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, reservation_key)
    if reservation is None or reservation.get("state") not in {"RESERVED", "CHECKOUT_CREATED"}:
        return False
    if (
        str(reservation.get("project_id") or "") != str(project_id)
        or int(reservation.get("checkout_generation") or 0) != generation
        or int(reservation.get("amount_cents") or 0) != int(amount_cents)
        or str(reservation.get("currency") or "").lower() != normalized_currency
        or (
            reservation.get("session_id")
            and str(reservation.get("session_id")) != session_id
        )
    ):
        return False

    record = {
        "session_id": session_id,
        "project_id": str(project_id),
        "checkout_generation": generation,
        "amount_cents": int(amount_cents),
        "currency": normalized_currency,
        "state": "PENDING",
        "created_at": str(reservation.get("created_at") or datetime.now(UTC).isoformat()),
        "recovered_from_reservation": True,
        "recovery_binding": "STRIPE_CHECKOUT_GENERATION",
    }
    created = service._store.put_if_absent(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        session_id,
        record,
    )
    if not created:
        existing = service._store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, session_id)
        return bool(
            existing
            and str(existing.get("project_id") or "") == str(project_id)
            and int(existing.get("checkout_generation") or 0) == generation
            and int(existing.get("amount_cents") or 0) == int(amount_cents)
            and str(existing.get("currency") or "").lower() == normalized_currency
        )

    reservation["state"] = "CHECKOUT_CREATED"
    reservation["session_id"] = session_id
    reservation["recovered_at"] = datetime.now(UTC).isoformat()
    service._store.put(GROWTH_BALANCE_TOPUP_NAMESPACE, reservation_key, reservation)
    return True


def _timestamp_is_newer(candidate: str, current: str) -> bool:
    try:
        candidate_dt = datetime.fromisoformat(candidate)
        current_dt = datetime.fromisoformat(current)
    except ValueError:
        return candidate > current
    if candidate_dt.tzinfo is None:
        candidate_dt = candidate_dt.replace(tzinfo=UTC)
    if current_dt.tzinfo is None:
        current_dt = current_dt.replace(tzinfo=UTC)
    return candidate_dt > current_dt
