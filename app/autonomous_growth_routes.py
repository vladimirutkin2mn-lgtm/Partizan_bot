from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.autonomous_growth import (
    AutonomousGrowthSweepView,
    autonomous_growth_sweep_service,
)
from app.operator_auth import require_operator

router = APIRouter(
    tags=["autonomous-growth"],
    dependencies=[Depends(require_operator)],
)


@router.post(
    "/ops/autonomous-growth/sweep",
    response_model=AutonomousGrowthSweepView,
)
async def run_autonomous_growth_sweep(
    product_id: UUID | None = None,
) -> AutonomousGrowthSweepView:
    try:
        return await autonomous_growth_sweep_service.run_once(product_id=product_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/ops/autonomous-growth/sweeps",
    response_model=list[AutonomousGrowthSweepView],
)
async def list_autonomous_growth_sweeps(
    limit: int = Query(default=20, ge=1, le=100),
) -> list[AutonomousGrowthSweepView]:
    try:
        return autonomous_growth_sweep_service.recent_runs(limit)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
