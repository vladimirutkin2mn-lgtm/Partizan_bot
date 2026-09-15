from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.growth_balance_rail_safety import growth_balance_rail_safety_service
from app.operator_auth import require_operator

router = APIRouter(
    tags=["growth-balance-ops"],
    dependencies=[Depends(require_operator)],
)


class GrowthBalanceRailReactivationRequest(BaseModel):
    expected_pause_reason: str = Field(min_length=1, max_length=500)
    confirm_reconciled: bool
    confirm_reactivation: bool
    reconciliation_note: str = Field(min_length=3, max_length=1000)


@router.post("/ops/customer-projects/{project_id}/growth-balance/rail/reactivate")
def reactivate_growth_balance_rail_after_reconciliation(
    project_id: UUID,
    payload: GrowthBalanceRailReactivationRequest,
) -> dict:
    """Explicitly release one exact safety pause after operator reconciliation."""

    try:
        return growth_balance_rail_safety_service.reactivate_after_reconciliation(
            project_id,
            expected_pause_reason=payload.expected_pause_reason,
            confirm_reconciled=payload.confirm_reconciled,
            confirm_reactivation=payload.confirm_reactivation,
            reconciliation_note=payload.reconciliation_note,
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
