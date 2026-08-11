from __future__ import annotations

from uuid import UUID

from app.paid_lifecycle_audit import (
    PaidAuditActor,
    PaidAuditEventType,
    PaidAuditEventView,
    PaidAuditResult,
    PaidLifecycleView,
    paid_audit_ledger,
    paid_lifecycle_service,
)


def observe_paid_lifecycle(action_id: UUID) -> PaidLifecycleView | None:
    """Best-effort lifecycle observation for instrumentation only.

    Operational/provider behavior must never depend on audit reconstruction succeeding.
    Strict lifecycle reads continue to use PaidLifecycleService.get directly.
    """
    try:
        return paid_lifecycle_service.get(action_id)
    except Exception:
        return None


def append_paid_audit(
    *,
    action_id: UUID,
    event_type: PaidAuditEventType,
    actor: PaidAuditActor,
    result: PaidAuditResult,
    before: PaidLifecycleView | None = None,
    after: PaidLifecycleView | None = None,
    correlation_id: str | UUID | None = None,
    reason: str | None = None,
    deduplicate: bool = False,
) -> PaidAuditEventView | None:
    """Append audit evidence without changing the result of the paid operation.

    The ledger remains best-effort observability. A database/model/audit failure must not
    turn a successful provider pause, activation, staging operation, or worker sync into a
    client-visible provider failure or cause that external operation to be retried blindly.
    """
    try:
        return paid_audit_ledger.record(
            action_id=action_id,
            event_type=event_type,
            actor=actor,
            result=result,
            before=before,
            after=after,
            correlation_id=correlation_id,
            reason=reason,
            deduplicate=deduplicate,
        )
    except Exception:
        return None
