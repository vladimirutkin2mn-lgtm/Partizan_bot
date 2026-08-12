from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.autonomy_schemas import (
    AutonomyEvaluationRequest,
    AutonomyEvaluationView,
    GrowthMandateStatusRequest,
    GrowthMandateUpsertRequest,
    GrowthMandateView,
)
from app.autonomy_service import growth_mandate_service
from app.operator_auth import require_operator
from app.product_intake import product_intake_service

router = APIRouter(
    tags=["autonomy"],
    dependencies=[Depends(require_operator)],
)


@router.put(
    "/products/{product_id}/growth-mandate",
    response_model=GrowthMandateView,
)
async def upsert_growth_mandate(
    product_id: UUID,
    payload: GrowthMandateUpsertRequest,
) -> GrowthMandateView:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    return growth_mandate_service.upsert(product_id, payload)


@router.get(
    "/products/{product_id}/growth-mandate",
    response_model=GrowthMandateView,
)
async def get_growth_mandate(product_id: UUID) -> GrowthMandateView:
    try:
        product_intake_service.get_product(product_id)
        return growth_mandate_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product or Growth Mandate not found") from exc


@router.patch(
    "/products/{product_id}/growth-mandate/status",
    response_model=GrowthMandateView,
)
async def set_growth_mandate_status(
    product_id: UUID,
    payload: GrowthMandateStatusRequest,
) -> GrowthMandateView:
    try:
        product_intake_service.get_product(product_id)
        return growth_mandate_service.set_status(product_id, payload.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product or Growth Mandate not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/products/{product_id}/growth-mandate/evaluate",
    response_model=AutonomyEvaluationView,
)
async def evaluate_growth_mandate(
    product_id: UUID,
    payload: AutonomyEvaluationRequest,
) -> AutonomyEvaluationView:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    return growth_mandate_service.evaluate(product_id, payload)
