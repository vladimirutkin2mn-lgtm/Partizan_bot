from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.paid_control_reconciliation import (
    PaidReconcileResultView,
    PaidReconciliationQueueView,
    paid_control_reconciliation_service,
)
from app.paid_control_sweep import (
    PaidControlSweepView,
    paid_control_sweep_service,
)

router = APIRouter(tags=["paid-control-ops"])


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
        return paid_control_reconciliation_service.reconcile(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Paid action not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
