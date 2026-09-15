from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.growth_balance import GROWTH_BALANCE_RAIL_NAMESPACE, GrowthBalanceSettlementService
from app.runtime_store import RuntimeStateStore


class SettlementReadiness(Protocol):
    def readiness(self, project_id: UUID) -> tuple[bool, str]: ...


def customer_growth_balance_execution_readiness(
    store: RuntimeStateStore,
    product_id: UUID,
    *,
    settlement: SettlementReadiness | None = None,
) -> tuple[bool, str, dict | None]:
    """Return the effective paid-execution readiness for a customer-linked product.

    Non-customer products intentionally keep the existing paid-provider behavior. Once a
    product belongs to a customer project, however, the Partizan-funded Growth Balance
    rail is an execution prerequisite and must be physically active, unpaused and bound
    to Meta before a paid provider mutation is allowed.
    """

    matches = [
        item
        for item in store.list_namespace(CUSTOMER_PROJECT_NAMESPACE)
        if str(item.get("product_id") or "") == str(product_id)
    ]
    if not matches:
        return True, "NOT_CUSTOMER_BOUND", None
    if len(matches) != 1:
        return False, "CUSTOMER_PROJECT_BINDING_AMBIGUOUS", None

    project_id_raw = matches[0].get("id")
    if not project_id_raw:
        return False, "CUSTOMER_PROJECT_BINDING_INVALID", None
    try:
        project_id = UUID(str(project_id_raw))
    except ValueError:
        return False, "CUSTOMER_PROJECT_BINDING_INVALID", None

    readiness = settlement or GrowthBalanceSettlementService(store)
    ready, status = readiness.readiness(project_id)
    if not ready:
        return False, status, None

    rail = store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
    if rail is None or not str(rail.get("card_id") or "").strip():
        return False, "STRIPE_ISSUING_CARD_NOT_PROVISIONED", rail
    if rail.get("binding_status") != "BOUND" or rail.get("bound_provider") != "meta":
        return False, "META_BILLING_NOT_BOUND_TO_PARTIZAN_CARD", rail
    if str(rail.get("paused_reason") or "").strip():
        return False, "STRIPE_ISSUING_CARD_PAUSED", rail
    if str(rail.get("card_status") or "").lower() != "active":
        return False, "STRIPE_ISSUING_CARD_INACTIVE", rail
    if int(rail.get("acquisition_limit_cents") or 0) <= 0:
        return False, "STRIPE_ISSUING_LIMIT_NOT_CONFIGURED", rail
    return True, "READY", rail


def require_customer_growth_balance_execution_ready(
    store: RuntimeStateStore,
    product_id: UUID,
    *,
    settlement: SettlementReadiness | None = None,
) -> dict | None:
    ready, status, rail = customer_growth_balance_execution_readiness(
        store,
        product_id,
        settlement=settlement,
    )
    if not ready:
        raise ValueError(f"Growth Balance paid execution rail is not ready ({status})")
    return rail
