from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.operator_auth import require_operator
from app.outreach_targets import (
    OutreachSuppressRequest,
    OutreachTargetCreateRequest,
    OutreachTargetListView,
    OutreachTargetView,
    outreach_target_service,
)

router = APIRouter(
    tags=["outreach"],
    dependencies=[Depends(require_operator)],
)


@router.post(
    "/products/{product_id}/outreach-targets",
    response_model=OutreachTargetView,
    status_code=status.HTTP_201_CREATED,
)
async def create_outreach_target(
    product_id: UUID,
    payload: OutreachTargetCreateRequest,
) -> OutreachTargetView:
    try:
        return outreach_target_service.create(product_id, payload)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Product distribution map not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/products/{product_id}/outreach-targets",
    response_model=OutreachTargetListView,
)
async def list_outreach_targets(product_id: UUID) -> OutreachTargetListView:
    return outreach_target_service.list_product(product_id)


@router.get(
    "/outreach-targets/{target_id}",
    response_model=OutreachTargetView,
)
async def get_outreach_target(target_id: UUID) -> OutreachTargetView:
    try:
        return outreach_target_service.get(target_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="OutreachTarget not found") from exc


@router.post(
    "/outreach-targets/{target_id}/suppress",
    response_model=OutreachTargetView,
)
async def suppress_outreach_target(
    target_id: UUID,
    payload: OutreachSuppressRequest,
) -> OutreachTargetView:
    try:
        return outreach_target_service.suppress(target_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="OutreachTarget not found") from exc
