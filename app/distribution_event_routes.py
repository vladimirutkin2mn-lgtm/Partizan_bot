from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.distribution_analytics_schemas import (
    DistributionAnalyticsEventCreate,
    DistributionAnalyticsEventReceipt,
)
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_event_ingestion import (
    DISTRIBUTION_EVENT_KEY_HEADER,
    DistributionEventKeyCreateView,
    DistributionEventKeyStatusView,
    distribution_event_key_service,
)
from app.distribution_execution_service import distribution_execution_service
from app.operator_auth import require_operator
from app.product_intake import product_intake_service

router = APIRouter(tags=["distribution-event-integration"])


@router.post(
    "/products/{product_id}/distribution-event-key",
    response_model=DistributionEventKeyCreateView,
    dependencies=[Depends(require_operator)],
)
async def rotate_distribution_event_key(product_id: UUID) -> DistributionEventKeyCreateView:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    return distribution_event_key_service.rotate(product_id)


@router.get(
    "/products/{product_id}/distribution-event-key",
    response_model=DistributionEventKeyStatusView,
    dependencies=[Depends(require_operator)],
)
async def get_distribution_event_key_status(
    product_id: UUID,
) -> DistributionEventKeyStatusView:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    return distribution_event_key_service.status(product_id)


@router.delete(
    "/products/{product_id}/distribution-event-key",
    response_model=DistributionEventKeyStatusView,
    dependencies=[Depends(require_operator)],
)
async def revoke_distribution_event_key(product_id: UUID) -> DistributionEventKeyStatusView:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    return distribution_event_key_service.revoke(product_id)


@router.post(
    "/products/{product_id}/distribution-events",
    response_model=DistributionAnalyticsEventReceipt,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_product_distribution_event(
    product_id: UUID,
    payload: DistributionAnalyticsEventCreate,
    event_key: Annotated[str | None, Header(alias=DISTRIBUTION_EVENT_KEY_HEADER)] = None,
) -> DistributionAnalyticsEventReceipt:
    if not distribution_event_key_service.verify(product_id, event_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Distribution event authentication required",
            headers={"WWW-Authenticate": "PartizanEventKey"},
        )

    try:
        product_intake_service.get_product(product_id)
        experiment, _ = distribution_execution_service.resolve_experiment(
            experiment_id=payload.experiment_id,
            referral_token=payload.referral_token,
            action_id=payload.action_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Distribution attribution target not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if experiment.product_id != product_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Distribution event key cannot write to another product",
        )

    try:
        return distribution_analytics_service.ingest_event(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionExperiment not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
