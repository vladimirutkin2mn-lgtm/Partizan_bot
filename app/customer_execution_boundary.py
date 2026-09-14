from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator
from uuid import UUID

from app.distribution_schemas import DistributionActionView

_customer_execution_request_scope: ContextVar[str | None] = ContextVar(
    "customer_execution_request_scope",
    default=None,
)


def customer_execution_request_id(action: DistributionActionView) -> str | None:
    value = action.operational_metadata.get("customer_execution_request_id")
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
