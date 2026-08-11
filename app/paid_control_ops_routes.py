from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.operator_auth import require_operator
from app.paid_audit_safe import append_paid_audit, observe_paid_lifecycle
from app.paid_control_reconciliation import (
    PaidReconcileResultView,
    PaidReconciliationQueueView,
    paid_control_reconciliation_service,
)
from app.paid_control_sweep import (
    PaidControlSweepView,
    paid_control_sweep_service,
)
from app.paid_lifecycle_audit import (
    PaidAuditActor,
    PaidAuditEventType,
    PaidAuditEventView,
    PaidAuditResult,
    PaidLifecycleView,
    paid_audit_ledger,
    paid_lifecycle_service,
)

router = APIRouter(
    tags=["paid-control-ops"],
    dependencies=[Depends(require_operator)],
)


@router.get(
    "/ops/paid-control/sweeps",
    response_model=list[PaidControlSweepView],
)
async def recent_paid_control_sweeps(
    limit: int = Query(default=20, ge=1, le=100),
) -> list[PaidControlSweepView]:
    try:
        return paid_control_sweep_service.recent_runs(limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/ops/paid-control/lifecycle/{action_id}",
    response_model=PaidLifecycleView,
)
async def paid_control_lifecycle(action_id: UUID) -> PaidLifecycleView:
    try:
        return paid_lifecycle_service.get(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Paid action not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/ops/paid-control/audit",
    response_model=list[PaidAuditEventView],
)
async def paid_control_audit(
    action_id: UUID | None = None,
    provider: str | None = Query(default=None, max_length=120),
    event_type: PaidAuditEventType | None = None,
    actor: PaidAuditActor | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[PaidAuditEventView]:
    try:
        return paid_audit_ledger.query(
            action_id=action_id,
            provider=provider,
            event_type=event_type,
            actor=actor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/ops/paid-control/reconciliation",
    response_model=PaidReconciliationQueueView,
)
async def paid_control_reconciliation_queue() -> PaidReconciliationQueueView:
    return paid_control_reconciliation_service.queue()


@router.post(
    "/ops/paid-control/reconciliation/{action_id}/sync",
    response_model=PaidReconcileResultView,
)
async def reconcile_paid_control_action(action_id: UUID) -> PaidReconcileResultView:
    try:
        before = observe_paid_lifecycle(action_id)
        result = paid_control_reconciliation_service.reconcile(action_id)
        after = observe_paid_lifecycle(action_id)
        append_paid_audit(
            action_id=action_id,
            event_type=PaidAuditEventType.RECONCILIATION_SYNC,
            actor=PaidAuditActor.OPERATOR,
            result=(PaidAuditResult.SUCCESS if result.resolved else PaidAuditResult.FAILED),
            before=before,
            after=after,
            reason=result.sync.reason,
        )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Paid action not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
