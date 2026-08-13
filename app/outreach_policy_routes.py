from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.operator_auth import require_operator
from app.outreach_policy import (
    OutreachAutonomousPreparationView,
    OutreachPolicyStatusRequest,
    OutreachPolicyUpsertRequest,
    OutreachPolicyView,
    outreach_autonomous_preparation_service,
    outreach_policy_service,
)

router = APIRouter(
    tags=["outreach"],
    dependencies=[Depends(require_operator)],
)


@router.put(
    "/products/{product_id}/outreach-policy",
    response_model=OutreachPolicyView,
)
async def upsert_outreach_policy(
    product_id: UUID,
    payload: OutreachPolicyUpsertRequest,
) -> OutreachPolicyView:
    try:
        return outreach_policy_service.upsert(product_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail="Growth Mandate is required") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/products/{product_id}/outreach-policy",
    response_model=OutreachPolicyView,
)
async def get_outreach_policy(product_id: UUID) -> OutreachPolicyView:
    try:
        return outreach_policy_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Outreach Policy not found") from exc


@router.post(
    "/products/{product_id}/outreach-policy/status",
    response_model=OutreachPolicyView,
)
async def set_outreach_policy_status(
    product_id: UUID,
    payload: OutreachPolicyStatusRequest,
) -> OutreachPolicyView:
    try:
        return outreach_policy_service.set_status(product_id, payload.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Outreach Policy not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/products/{product_id}/outreach-autonomy/prepare-next",
    response_model=OutreachAutonomousPreparationView | None,
)
async def prepare_next_outreach(
    product_id: UUID,
) -> OutreachAutonomousPreparationView | None:
    try:
        return await outreach_autonomous_preparation_service.prepare_next(product_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Outreach target or dependency is unavailable",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
