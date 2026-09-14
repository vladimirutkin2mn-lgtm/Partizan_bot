from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from app.distribution_schemas import DistributionActionView
from app.distribution_types import DistributionActionType

_customer_execution_request_scope: ContextVar[str | None] = ContextVar(
    "customer_execution_request_scope",
    default=None,
)

_CUSTOMER_EXECUTION_UNSUPPORTED_ACTION_TYPES = {
    DistributionActionType.PAID_CAMPAIGN,
    DistributionActionType.OUTREACH_EMAIL,
}


def customer_execution_request_id(action: DistributionActionView) -> str | None:
    metadata = getattr(action, "operational_metadata", None)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("customer_execution_request_id")
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def require_customer_bound_mutation_scope(
    action: DistributionActionView,
    operation: str,
) -> None:
    bound_request_id = customer_execution_request_id(action)
    if bound_request_id is None:
        return
    metadata = getattr(action, "operational_metadata", {})
    if (
        operation in {"approval", "outreach approval"}
        and metadata.get("customer_publish_confirmation_required") is True
        and not metadata.get("customer_publish_confirmed_at")
    ):
        raise ValueError(
            "Customer publish confirmation is required before this prepared action can be approved"
        )
    action_type = getattr(action, "action_type", None)
    if action_type in _CUSTOMER_EXECUTION_UNSUPPORTED_ACTION_TYPES:
        raise ValueError(
            f"Customer execution requests cannot mutate {action_type.value} actions"
        )
    if _customer_execution_request_scope.get() != bound_request_id:
        raise ValueError(
            f"Customer-bound action {operation} requires the dedicated customer execution request flow"
        )


@contextmanager
def customer_execution_request_scope(request_id: UUID) -> Iterator[None]:
    token = _customer_execution_request_scope.set(str(request_id))
    try:
        yield
    finally:
        _customer_execution_request_scope.reset(token)
